"""Computer players. A bot is a room participant like any connected client: it receives the
same messages and answers through ``room.handle``, so the server treats it no differently.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Callable

from holdem import protocol
from holdem.core.betting import Action, LegalActions

if TYPE_CHECKING:
    from holdem.server.room import Room

FOLD, CHECK, CALL, BET, RAISE, ALL_IN = Action

Policy = Callable[[LegalActions, random.Random], tuple[Action, "int | None"]]

# Random bots: never fold when checking is free, and rarely shove.
RANDOM_WEIGHTS = {FOLD: 1, CHECK: 3, CALL: 3, BET: 2, RAISE: 1, ALL_IN: 0.2}


def random_action(legal: LegalActions, rng: random.Random) -> tuple[Action, int | None]:
    choices = sorted(action for action in legal.actions if not (action is FOLD and CHECK in legal.actions))
    action = rng.choices(choices, weights=[RANDOM_WEIGHTS[action] for action in choices])[0]
    if action in (BET, RAISE):
        low, high = legal.min_raise_to, legal.max_raise_to
        return action, rng.randint(low, min(high, low * 3))
    return action, None


class BotPlayer:
    """Plays a seat in a Room with ``policy``, pausing ``think`` seconds before each move."""

    def __init__(
        self, room: Room, policy: Policy = random_action, rng: random.Random | None = None, think: float = 1.0
    ):
        self.room = room
        self.policy = policy
        self.rng = rng or random.Random()
        self.think = think
        self.player_id: str | None = None

    def deliver(self, message: protocol.Message) -> None:
        # Never call back into the room while it is still sending; act on a later tick.
        if message["type"] == "your_turn":
            self.room.schedule(self.think, lambda: self._act(message))
        elif message["type"] == "table_state":
            seat = message["your_seat"]
            mine = message["seats"][seat] if seat is not None else None
            if mine is not None and mine["stack"] == 0 and not mine["in_hand"]:
                self.room.schedule(0, self._rebuy)

    def _act(self, turn: protocol.Message) -> None:
        action, amount = self.policy(protocol.legal_from_turn(turn), self.rng)
        self.room.handle(self.player_id, protocol.action(action, amount))

    def _rebuy(self) -> None:
        seat = self.room.table.seat_of(self.player_id)
        if seat is not None and seat.stack == 0:
            self.room.handle(self.player_id, protocol.rebuy(self.room.table.config.max_buy_in))
