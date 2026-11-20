"""The lobby: everyone connected to the server, and the rooms they can create and join.

Like ``Room`` it is network-free and single-threaded. A connection becomes a ``Session`` on
``hello`` and starts in the lobby, where it sees the room list. Creating or joining a room hands
the connection to that ``Room`` as its participant; table messages are then passed through.

Rooms are listed for everyone (name, blinds, players, whether a password is needed). A room
closes once no person has been connected to it for ``empty_room_timeout`` seconds. A session that
disconnects can come back with its token within ``reconnect_grace`` seconds and is put back into
its room, where the room's own reconnect logic gives it its seat back.
"""

from __future__ import annotations

import logging
import re
import secrets
import time
from dataclasses import dataclass
from typing import Callable

from holdem import protocol
from holdem.bots import Policy
from holdem.core.cards import Deck
from holdem.core.table import TableConfig
from holdem.server.room import JoinError, Member, Participant, Room, Schedule
from holdem.strategy import smart_action

log = logging.getLogger(__name__)

ROOM_SEATS = 9
ROOM_ID_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I
# Bots join as "Bot 1", "Bot 2", ...; people may not use those names.
BOT_NAME = re.compile(r"Bot \d+")


@dataclass
class Session:
    player_id: str
    name: str
    token: str
    participant: Participant | None  # None while disconnected
    room: Room | None = None
    member: Member | None = None  # who this session is inside ``room``
    generation: int = 0  # bumped on every disconnect and rejoin, so stale timers can tell


class Lobby:
    def __init__(
        self,
        schedule: Schedule,
        clock: Callable[[], float] = time.monotonic,
        password: str | None = None,
        max_rooms: int = 10,
        max_bots: int = 5,
        hand_delay: float = 3.0,
        bot_think: float = 1.0,
        bot_policy: Policy = smart_action,
        action_timeout: float = 30.0,
        reconnect_grace: float = 120.0,
        empty_room_timeout: float = 600.0,
        deck_factory: Callable[[], Deck] | None = None,
    ):
        """``password`` guards the whole server; rooms have their own. ``clock`` must count seconds
        the same way ``schedule`` does."""
        self.schedule = schedule
        self.clock = clock
        self.password = password or None
        self.max_rooms = max_rooms
        self.max_bots = max_bots
        self.hand_delay = hand_delay
        self.bot_think = bot_think
        self.bot_policy = bot_policy
        self.action_timeout = action_timeout
        self.reconnect_grace = reconnect_grace
        self.empty_room_timeout = empty_room_timeout
        self.deck_factory = deck_factory
        self.sessions: dict[str, Session] = {}
        self.rooms: dict[str, Room] = {}  # in the order they were created
        self._listed: list[protocol.Message] | None = None
        self._empty_since: dict[str, int] = {}  # room id -> generation of its pending close

    # Connecting

    def join(
        self, participant: Participant, nickname: str, password: str | None = None,
        version: int = protocol.PROTOCOL_VERSION, token: str | None = None,
    ) -> Session:
        """Admit a connection to the lobby. A known ``token`` takes back that session (and puts it
        back into its room), replacing any connection still attached to it."""
        if version != protocol.PROTOCOL_VERSION:
            raise JoinError("version_mismatch", f"server speaks protocol {protocol.PROTOCOL_VERSION}")
        if self.password is not None and not secrets.compare_digest((password or "").encode(), self.password.encode()):
            raise JoinError("bad_password", "wrong server password")
        if token:
            session = next(
                (s for s in self.sessions.values() if secrets.compare_digest(s.token.encode(), token.encode())), None
            )
            if session is not None:
                self._rejoin(session, participant)
                return session
        if BOT_NAME.fullmatch(nickname) or any(session.name == nickname for session in self.sessions.values()):
            raise JoinError("nickname_taken", f"someone here is already called {nickname}")
        session = Session(secrets.token_hex(8), nickname, secrets.token_urlsafe(16), participant)
        self.sessions[session.player_id] = session
        participant.deliver(protocol.welcome(session.player_id, session.token))
        participant.deliver(protocol.rooms(self._room_list()))
        log.info("%s connected", nickname)
        return session

    def _rejoin(self, session: Session, participant: Participant) -> None:
        session.participant = participant
        session.generation += 1
        log.info("%s reconnected", session.name)
        participant.deliver(protocol.welcome(session.player_id, session.token))
        room = session.room
        if room is not None and self.rooms.get(room.room_id) is room:
            self._cancel_close(room)
            try:
                token = session.member.token if session.member else None
                session.member = room.join(participant, session.name, room.password, token=token)
                return
            except JoinError:
                pass
        session.room = session.member = None
        participant.deliver(protocol.rooms(self._room_list()))

    def disconnect(self, player_id: str, participant: Participant | None = None) -> None:
        """The connection is gone. The session (and its seat, if any) waits ``reconnect_grace``
        seconds for it to come back. With ``participant``, only that connection counts."""
        session = self.sessions.get(player_id)
        if session is None or session.participant is None:
            return
        if participant is not None and session.participant is not participant:
            return
        old, session.participant = session.participant, None
        session.generation += 1
        log.info("%s disconnected", session.name)
        if session.room is not None and session.member is not None:
            session.room.disconnect(session.member.player_id, old)
            self._close_when_empty(session.room)
        generation = session.generation
        self.schedule(self.reconnect_grace, lambda: self._expire(session, generation))

    def _expire(self, session: Session, generation: int) -> None:
        if session.generation != generation or self.sessions.get(session.player_id) is not session:
            return
        del self.sessions[session.player_id]
        log.info("%s did not come back", session.name)

    # Requests

    def handle(self, player_id: str, message: protocol.Message) -> None:
        session = self.sessions.get(player_id)
        if session is None or session.participant is None:
            return
        kind = message["type"]
        try:
            if kind == "create_room":
                self._create(session, message)
            elif kind == "join_room":
                self._join_room(session, message["room"], message["password"])
            elif kind == "leave_room":
                self._leave_room(session)
            elif session.room is not None and session.member is not None:
                session.room.handle(session.member.player_id, message)
            else:
                raise JoinError("invalid", "join a room first")
        except JoinError as error:
            session.participant.deliver(protocol.error(error.code, str(error)))

    def _create(self, session: Session, message: protocol.Message) -> None:
        if session.room is not None:
            raise JoinError("invalid", "leave your room first")
        if len(self.rooms) >= self.max_rooms:
            raise JoinError("too_many_rooms", f"the server already has {self.max_rooms} rooms")
        if not 0 <= message["bots"] <= self.max_bots:
            raise JoinError("invalid", f"bots must be 0 to {self.max_bots}")
        big_blind = message["big_blind"]
        try:
            config = TableConfig(
                message["small_blind"], big_blind, min_buy_in=20 * big_blind, max_buy_in=100 * big_blind,
                max_seats=ROOM_SEATS, action_timeout=self.action_timeout,
            )
        except ValueError as error:
            raise JoinError("invalid", str(error)) from None
        room_id = self._new_room_id()
        room = Room(
            config, self.schedule, password=message["password"], hand_delay=self.hand_delay,
            deck_factory=self.deck_factory, clock=self.clock, reconnect_grace=self.reconnect_grace,
            room_id=room_id, name=message["name"].strip(), on_change=self._push_rooms,
        )
        self.rooms[room_id] = room
        log.info("%s opened room %s (%s)", session.name, room.name, room_id)
        try:
            for number in range(1, message["bots"] + 1):
                room.add_bot(f"Bot {number}", think=self.bot_think, policy=self.bot_policy)
            self._join_room(session, room_id, message["password"])
        except JoinError:
            room.close()
            del self.rooms[room_id]
            self._push_rooms()
            raise

    def _join_room(self, session: Session, room_id: str, password: str | None) -> None:
        if session.room is not None:
            raise JoinError("invalid", "leave your room first")
        room = self.rooms.get(room_id)
        if room is None:
            raise JoinError("room_not_found", "that room is gone")
        # In the room before joining it, so the list that joining pushes goes only to the lobby.
        session.room = room
        try:
            session.member = room.join(session.participant, session.name, password)
        except JoinError:
            session.room = None
            raise
        self._cancel_close(room)
        self._push_rooms()

    def _leave_room(self, session: Session) -> None:
        room = session.room
        if room is None:
            raise JoinError("invalid", "you are not in a room")
        # room_left comes before any room list, so a client at the table knows why the list arrives.
        session.participant.deliver(protocol.room_left())
        member, session.room, session.member = session.member, None, None
        if member is not None:
            room.remove(member.player_id)
        session.participant.deliver(protocol.rooms(self._room_list()))
        self._close_when_empty(room)

    # Rooms

    def _new_room_id(self) -> str:
        while True:
            room_id = "".join(secrets.choice(ROOM_ID_ALPHABET) for _ in range(6))
            if room_id not in self.rooms:
                return room_id

    def _close_when_empty(self, room: Room) -> None:
        """Close the room once nobody has been connected to it for ``empty_room_timeout`` seconds."""
        if room.humans() > 0 or room.room_id not in self.rooms:
            return
        generation = self._empty_since.get(room.room_id, 0) + 1
        self._empty_since[room.room_id] = generation
        self.schedule(self.empty_room_timeout, lambda: self._close_if_still_empty(room, generation))

    def _cancel_close(self, room: Room) -> None:
        self._empty_since[room.room_id] = self._empty_since.get(room.room_id, 0) + 1

    def _close_if_still_empty(self, room: Room, generation: int) -> None:
        if self._empty_since.get(room.room_id) != generation or room.humans() > 0:
            return
        if self.rooms.get(room.room_id) is not room:
            return
        room.close()
        del self.rooms[room.room_id]
        del self._empty_since[room.room_id]
        for session in self.sessions.values():
            if session.room is room:
                session.room = session.member = None
        log.info("closed empty room %s (%s)", room.name, room.room_id)
        self._push_rooms()

    def _room_list(self) -> list[protocol.Message]:
        return [
            {
                "id": room.room_id,
                "name": room.name,
                "small_blind": room.table.config.small_blind,
                "big_blind": room.table.config.big_blind,
                "players": sum(seat is not None for seat in room.table.seats),
                "seats": room.table.config.max_seats,
                "locked": room.password is not None,
            }
            for room in self.rooms.values()
        ]

    def _push_rooms(self) -> None:
        """Send the room list to everyone in the lobby, if it changed since the last time."""
        listed = self._room_list()
        if listed == self._listed:
            return
        self._listed = listed
        message = protocol.rooms(listed)
        for session in self.sessions.values():
            if session.room is None and session.participant is not None:
                session.participant.deliver(message)
