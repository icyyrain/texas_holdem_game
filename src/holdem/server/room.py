"""One table and the people at it, independent of the network.

Anything that plays a seat is a ``Participant``: a WebSocket connection or an in-process bot.
The room validates every request, drives the hand and sends each participant its own view.
It is single-threaded; the server calls it from its asyncio loop.

A seated player who disconnects keeps the seat for ``reconnect_grace`` seconds and can take it
back by sending their token in ``hello``. Each turn has a timer of ``config.action_timeout``
seconds, which also runs while the player is offline; when it expires the room checks or folds
for them and marks them away. Away seats sit out new hands and are played automatically.
"""

from __future__ import annotations

import logging
import random
import secrets
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from holdem import protocol
from holdem.bots import BotPlayer, Policy, random_action
from holdem.core.betting import Action, IllegalActionError
from holdem.core.cards import Deck
from holdem.core.table import HandState, Seat, Table, TableConfig

log = logging.getLogger(__name__)

Schedule = Callable[[float, Callable[[], None]], object]


class Participant(Protocol):
    def deliver(self, message: protocol.Message) -> None:
        """Take one server message. Must not block or call back into the room synchronously."""


class JoinError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Member:
    player_id: str
    name: str
    token: str
    participant: Participant | None  # None while disconnected
    session: int = 0  # bumped on every disconnect and rejoin, so stale timers can tell


class Room:
    def __init__(
        self,
        config: TableConfig,
        schedule: Schedule,
        password: str | None = None,
        hand_delay: float = 3.0,
        deck_factory: Callable[[], Deck] | None = None,
        clock: Callable[[], float] = time.monotonic,
        reconnect_grace: float = 120.0,
        room_id: str = "",
        name: str = "",
        on_change: Callable[[], None] | None = None,
    ):
        """``clock`` must count seconds the same way ``schedule`` does. ``on_change`` is called after
        every change, so a lobby can keep its room list current."""
        self.table = Table(config, deck_factory) if deck_factory else Table(config)
        self.room_id = room_id
        self.name = name
        self.on_change = on_change
        self.closed = False
        self.schedule = schedule
        self.clock = clock
        self.password = password or None
        self.hand_delay = hand_delay
        self.reconnect_grace = reconnect_grace
        self.members: dict[str, Member] = {}
        self.hand_number = 0
        self._names: dict[str, str] = {}
        self._events_sent = 0
        self._hand_open = False
        self._start_pending = False
        self._turn: tuple[int, int, int] | None = None  # (hand number, seat, events so far)
        self._deadline = 0.0
        # Players the room marked away because they were offline; coming back clears it.
        self._away_offline: set[str] = set()

    # Joining and leaving

    def join(
        self, participant: Participant, nickname: str, password: str | None = None,
        version: int = protocol.PROTOCOL_VERSION, token: str | None = None,
    ) -> Member:
        """Admit a participant. A known ``token`` takes back that player's place (and seat) instead,
        replacing any connection still attached to it; an unknown one is ignored."""
        if version != protocol.PROTOCOL_VERSION:
            raise JoinError("version_mismatch", f"server speaks protocol {protocol.PROTOCOL_VERSION}")
        if self.password is not None and not secrets.compare_digest((password or "").encode(), self.password.encode()):
            raise JoinError("bad_password", "wrong room password")
        if token:
            member = next(
                (m for m in self.members.values() if secrets.compare_digest(m.token.encode(), token.encode())), None
            )
            if member is not None:
                self._rejoin(member, participant)
                return member
        if any(member.name == nickname for member in self.members.values()):
            raise JoinError("nickname_taken", f"someone here is already called {nickname}")
        member = Member(secrets.token_hex(8), nickname, secrets.token_urlsafe(16), participant)
        self.members[member.player_id] = member
        self._names[member.player_id] = nickname
        participant.deliver(protocol.room_joined(self.room_id, self.name, self.table.config))
        participant.deliver(self._state_for(member.player_id))
        log.info("%s joined %s", nickname, self.name or "the room")
        self._notify()
        return member

    def _rejoin(self, member: Member, participant: Participant) -> None:
        member.participant = participant
        member.session += 1
        seat = self.table.seat_of(member.player_id)
        if seat is not None and member.player_id in self._away_offline:
            self._away_offline.discard(member.player_id)
            seat.away = False
        log.info("%s reconnected", member.name)
        participant.deliver(protocol.room_joined(self.room_id, self.name, self.table.config))
        turn = self._turn
        self._after_change()  # everyone sees them back; they get a fresh table_state
        # A turn that was already running when they came back is sent again, with its time left.
        if seat is not None and self._seat_to_act() is seat and self._turn == turn and not self._plays_itself(seat):
            self._send_turn(seat)

    def disconnect(self, player_id: str, participant: Participant | None = None) -> None:
        """The participant is gone. Someone without a seat leaves at once; a seated player keeps
        the seat for ``reconnect_grace`` seconds. With ``participant``, only that connection counts:
        a connection replaced by a rejoin can hang up without affecting the new one."""
        member = self.members.get(player_id)
        if member is None or member.participant is None:
            return
        if participant is not None and member.participant is not participant:
            return
        log.info("%s disconnected", member.name)
        seat = self.table.seat_of(player_id)
        if seat is None or seat.leaving:
            del self.members[player_id]
        else:
            member.participant = None
            member.session += 1
            if not self._still_playing(seat):
                self._set_away_offline(seat)
            session = member.session
            self.schedule(self.reconnect_grace, lambda: self._expire(member, session))
        self._after_change()

    def remove(self, player_id: str) -> None:
        """The player left the room on purpose: give up the seat (when the hand ends, if they are in it)."""
        if self.members.pop(player_id, None) is None:
            return
        self._away_offline.discard(player_id)
        if self.table.seat_of(player_id) is not None:
            self.table.leave(player_id)
        self._after_change()

    def close(self) -> None:
        """Stop the room for good: no more hands, timers or bot moves."""
        self.closed = True

    def humans(self) -> int:
        """Connected members that are not bots."""
        return sum(
            1 for member in self.members.values()
            if member.participant is not None and not isinstance(member.participant, BotPlayer)
        )

    def _expire(self, member: Member, session: int) -> None:
        if self.closed or member.session != session or self.members.get(member.player_id) is not member:
            return
        log.info("%s did not come back", member.name)
        del self.members[member.player_id]
        self._away_offline.discard(member.player_id)
        if self.table.seat_of(member.player_id) is not None:
            self.table.leave(member.player_id)
        self._after_change()

    def _still_playing(self, seat: Seat) -> bool:
        """True while the seat holds live cards in the current hand."""
        if not self.table.hand_in_progress:
            return False
        player = self.table.hand.players.get(seat.index)
        return player is not None and player.seat is seat and not player.folded

    def _set_away_offline(self, seat: Seat) -> None:
        if not seat.away:
            seat.away = True
            self._away_offline.add(seat.player_id)

    def _connected(self, player_id: str) -> bool:
        member = self.members.get(player_id)
        return member is not None and member.participant is not None

    def add_bot(
        self, nickname: str, rng: random.Random | None = None, think: float = 1.0, policy: Policy = random_action,
    ) -> BotPlayer:
        """Seat a bot at the first free seat with the maximum buy-in."""
        free = next((index for index, seat in enumerate(self.table.seats) if seat is None), None)
        if free is None:
            raise ValueError("the table is full")
        bot = BotPlayer(self, policy=policy, rng=rng, think=think)
        bot.player_id = self.join(bot, nickname, self.password).player_id
        self.table.sit(bot.player_id, free, self.table.config.max_buy_in)
        self._after_change()
        return bot

    # Requests

    def handle(self, player_id: str, message: protocol.Message) -> None:
        """Apply one validated client message; a refusal goes back to that player only."""
        member = self.members.get(player_id)
        if member is None or self.closed:
            return
        kind = message["type"]
        try:
            if kind == "sit":
                self.table.sit(player_id, message["seat"], message["buy_in"])
            elif kind == "leave":
                self.table.leave(player_id)
            elif kind == "rebuy":
                self.table.rebuy(player_id, message["amount"])
            elif kind == "away":
                self.table.set_away(player_id, message["away"])
                self._away_offline.discard(player_id)
            elif kind == "action":
                self._act(player_id, message["action"], message["amount"])
            else:
                raise ValueError(f"{kind} is not allowed now")
        except IllegalActionError as error:
            member.participant.deliver(protocol.error("illegal_action", str(error)))
            return
        except ValueError as error:
            member.participant.deliver(protocol.error("invalid", str(error)))
            return
        self._after_change()

    def _act(self, player_id: str, action: str, amount: int | None) -> None:
        seat = self.table.seat_of(player_id)
        if seat is None or not self.table.hand_in_progress:
            raise IllegalActionError("there is no hand for you to act in")
        self.table.hand.apply(seat.index, action, amount)

    # Driving the game

    def _after_change(self) -> None:
        """Send what happened, play for seats nobody controls, then prompt or start a hand."""
        while True:
            self._send_events()
            hand = self.table.hand
            if self._hand_open and hand.is_complete():
                self._hand_open = False
                for player_id in self.table.finish_hand():
                    log.info("%s left the table", self._names.get(player_id, player_id))
                for seat in self.table.seats:
                    if seat is not None and not self._connected(seat.player_id):
                        self._set_away_offline(seat)
            seat = self._seat_to_act()
            if seat is not None and self._plays_itself(seat):
                self._auto_act(seat)
                continue
            break
        new_turn = seat is not None and self._start_turn(seat)
        self._broadcast_state()
        if new_turn:
            self._send_turn(seat)
        self._schedule_hand()
        self._notify()

    def _notify(self) -> None:
        if self.on_change is not None:
            self.on_change()

    def _seat_to_act(self) -> Seat | None:
        if not self.table.hand_in_progress:
            return None
        return self.table.hand.players[self.table.hand.to_act].seat

    def _plays_itself(self, seat: Seat) -> bool:
        return seat.leaving or seat.away or seat.player_id not in self.members

    def _auto_act(self, seat: Seat) -> None:
        hand = self.table.hand
        action = Action.CHECK if Action.CHECK in hand.legal_actions(seat.index).actions else Action.FOLD
        hand.apply(seat.index, action)

    def _turn_key(self) -> tuple[int, int, int] | None:
        if not self.table.hand_in_progress:
            return None
        return self.hand_number, self.table.hand.to_act, len(self.table.hand.events)

    def _start_turn(self, seat: Seat) -> bool:
        """Start the timer for the seat to act; False if this turn was already started."""
        key = self._turn_key()
        if key == self._turn:
            return False
        self._turn = key
        timeout = self.table.config.action_timeout
        self._deadline = self.clock() + timeout
        self.schedule(timeout, lambda: self._time_out(key))
        return True

    def _time_left(self) -> float | None:
        if self._turn is None or self._turn != self._turn_key():
            return None
        return max(0.0, round(self._deadline - self.clock(), 1))

    def _send_turn(self, seat: Seat) -> None:
        participant = self.members[seat.player_id].participant
        if participant is not None:
            legal = self.table.hand.legal_actions(seat.index)
            participant.deliver(protocol.your_turn(self.hand_number, seat.index, legal, self._time_left()))

    def _time_out(self, key: tuple[int, int, int]) -> None:
        if self.closed or key != self._turn or key != self._turn_key():
            return
        seat = self._seat_to_act()
        log.info("%s ran out of time", self._names.get(seat.player_id, seat.player_id))
        seat.away = True
        if self._connected(seat.player_id):
            self._away_offline.discard(seat.player_id)
        else:
            self._away_offline.add(seat.player_id)
        self._after_change()  # the seat is away now, so it plays itself

    def _schedule_hand(self) -> None:
        if self.closed or self._start_pending or self.table.hand_in_progress or self._ready_players() < 2:
            return
        self._start_pending = True
        self.schedule(self.hand_delay, self._start_hand)

    def _start_hand(self) -> None:
        self._start_pending = False
        if self.closed or self.table.hand_in_progress or self._ready_players() < 2:
            return
        self.table.start_hand()
        self.hand_number += 1
        self._hand_open = True
        self._events_sent = 0
        log.info("hand %d started", self.hand_number)
        self._after_change()

    def _ready_players(self) -> int:
        return sum(
            1 for seat in self.table.seats
            if seat is not None and seat.stack > 0 and not seat.away and not seat.leaving
            and self._connected(seat.player_id)
        )

    # Views

    def _send_events(self) -> None:
        hand = self.table.hand
        if hand is None:
            return
        for event in hand.events[self._events_sent:]:
            for member in self.members.values():
                if member.participant is not None:
                    member.participant.deliver(protocol.event(self.hand_number, self._redact(event, member.player_id)))
        self._events_sent = len(hand.events)

    def _redact(self, event: protocol.Message, viewer: str) -> protocol.Message:
        if event["type"] == "hole_cards" and self.table.hand.players[event["seat"]].seat.player_id != viewer:
            return {"type": "hole_cards", "seat": event["seat"]}
        return event

    def _broadcast_state(self) -> None:
        for player_id, member in self.members.items():
            if member.participant is not None:
                member.participant.deliver(self._state_for(player_id))

    def _state_for(self, viewer: str) -> protocol.Message:
        hand = self.table.hand
        shown = hand.results().showdown if hand is not None and hand.is_complete() else {}
        seats = []
        for seat in self.table.seats:
            if seat is None:
                seats.append(None)
                continue
            player = hand.players.get(seat.index) if hand is not None else None
            if player is not None and player.seat is not seat:
                player = None
            visible = player is not None and (seat.player_id == viewer or seat.index in shown)
            seats.append({
                "seat": seat.index,
                "player_id": seat.player_id,
                "name": self._names.get(seat.player_id, "?"),
                "stack": seat.stack,
                "away": seat.away,
                "leaving": seat.leaving,
                "connected": self._connected(seat.player_id),
                "in_hand": player is not None and self.table.hand_in_progress,
                "folded": player is not None and player.folded,
                "all_in": player is not None and not player.folded and seat.stack == 0,
                "bet": player.street_bet if player is not None else 0,
                "cards": [card.code for card in player.hole] if visible else None,
            })
        own = self.table.seat_of(viewer)
        return protocol.table_state(own.index if own else None, seats, self._hand_view(hand))

    def _hand_view(self, hand: HandState | None) -> protocol.Message | None:
        if hand is None:
            return None
        return {
            "number": self.hand_number,
            "street": hand.street.value,
            "board": [card.code for card in hand.board],
            "pots": [{"amount": pot.amount, "eligible": sorted(pot.eligible)} for pot in hand.pots],
            "current_bet": hand.current_bet,
            "to_act": hand.to_act,
            "time_left": self._time_left(),
            "button": hand.button,
            "small_blind": hand.small_blind_seat,
            "big_blind": hand.big_blind_seat,
            "complete": hand.is_complete(),
        }
