"""One table and the people at it, independent of the network.

Anything that plays a seat is a ``Participant``: a WebSocket connection or an in-process bot.
The room validates every request, drives the hand and sends each participant its own view.
It is single-threaded; the server calls it from its asyncio loop.
"""

from __future__ import annotations

import logging
import random
import secrets
from dataclasses import dataclass
from typing import Callable, Protocol

from holdem import protocol
from holdem.bots import BotPlayer
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
    participant: Participant


class Room:
    def __init__(
        self,
        config: TableConfig,
        schedule: Schedule,
        password: str | None = None,
        hand_delay: float = 3.0,
        deck_factory: Callable[[], Deck] | None = None,
    ):
        self.table = Table(config, deck_factory) if deck_factory else Table(config)
        self.schedule = schedule
        self.password = password or None
        self.hand_delay = hand_delay
        self.members: dict[str, Member] = {}
        self.hand_number = 0
        self._names: dict[str, str] = {}
        self._events_sent = 0
        self._hand_open = False
        self._start_pending = False
        self._last_prompt: tuple[int, int, int] | None = None

    # Joining and leaving

    def join(
        self, participant: Participant, nickname: str, password: str | None = None,
        version: int = protocol.PROTOCOL_VERSION,
    ) -> Member:
        if version != protocol.PROTOCOL_VERSION:
            raise JoinError("version_mismatch", f"server speaks protocol {protocol.PROTOCOL_VERSION}")
        if self.password is not None and not secrets.compare_digest((password or "").encode(), self.password.encode()):
            raise JoinError("bad_password", "wrong room password")
        if any(member.name == nickname for member in self.members.values()):
            raise JoinError("nickname_taken", f"someone here is already called {nickname}")
        member = Member(secrets.token_hex(8), nickname, secrets.token_urlsafe(16), participant)
        self.members[member.player_id] = member
        self._names[member.player_id] = nickname
        participant.deliver(protocol.welcome(member.player_id, member.token, self.table.config))
        participant.deliver(self._state_for(member.player_id))
        log.info("%s joined", nickname)
        return member

    def disconnect(self, player_id: str) -> None:
        """The participant is gone; its seat is given up (immediately, or when its hand ends)."""
        member = self.members.pop(player_id, None)
        if member is None:
            return
        log.info("%s disconnected", member.name)
        if self.table.seat_of(player_id) is not None:
            self.table.leave(player_id)
        self._after_change()

    def add_bot(self, nickname: str, rng: random.Random | None = None, think: float = 1.0) -> BotPlayer:
        """Seat a random bot at the first free seat with the maximum buy-in."""
        free = next((index for index, seat in enumerate(self.table.seats) if seat is None), None)
        if free is None:
            raise ValueError("the table is full")
        bot = BotPlayer(self, rng=rng, think=think)
        bot.player_id = self.join(bot, nickname, self.password).player_id
        self.table.sit(bot.player_id, free, self.table.config.max_buy_in)
        self._after_change()
        return bot

    # Requests

    def handle(self, player_id: str, message: protocol.Message) -> None:
        """Apply one validated client message; a refusal goes back to that player only."""
        member = self.members.get(player_id)
        if member is None:
            return
        kind = message["type"]
        try:
            if kind == "sit":
                self.table.sit(player_id, message["seat"], message["buy_in"])
            elif kind == "leave":
                self.table.leave(player_id)
            elif kind == "rebuy":
                self.table.rebuy(player_id, message["amount"])
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
            seat = self._seat_to_act()
            if seat is not None and self._plays_itself(seat):
                self._auto_act(seat)
                continue
            break
        self._broadcast_state()
        if seat is not None:
            self._prompt(seat)
        self._schedule_hand()

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

    def _prompt(self, seat: Seat) -> None:
        hand = self.table.hand
        key = (self.hand_number, seat.index, len(hand.events))
        if key == self._last_prompt:
            return
        self._last_prompt = key
        message = protocol.your_turn(self.hand_number, seat.index, hand.legal_actions(seat.index))
        self.members[seat.player_id].participant.deliver(message)

    def _schedule_hand(self) -> None:
        if self._start_pending or self.table.hand_in_progress or self._ready_players() < 2:
            return
        self._start_pending = True
        self.schedule(self.hand_delay, self._start_hand)

    def _start_hand(self) -> None:
        self._start_pending = False
        if self.table.hand_in_progress or self._ready_players() < 2:
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
            and seat.player_id in self.members
        )

    # Views

    def _send_events(self) -> None:
        hand = self.table.hand
        if hand is None:
            return
        for event in hand.events[self._events_sent:]:
            for member in self.members.values():
                member.participant.deliver(protocol.event(self.hand_number, self._redact(event, member.player_id)))
        self._events_sent = len(hand.events)

    def _redact(self, event: protocol.Message, viewer: str) -> protocol.Message:
        if event["type"] == "hole_cards" and self.table.hand.players[event["seat"]].seat.player_id != viewer:
            return {"type": "hole_cards", "seat": event["seat"]}
        return event

    def _broadcast_state(self) -> None:
        for player_id, member in self.members.items():
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
            "button": hand.button,
            "small_blind": hand.small_blind_seat,
            "big_blind": hand.big_blind_seat,
            "complete": hand.is_complete(),
        }
