from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Action(StrEnum):
    FOLD = "fold"
    CHECK = "check"
    CALL = "call"
    BET = "bet"
    RAISE = "raise"
    ALL_IN = "all_in"


class IllegalActionError(ValueError):
    """The rules do not allow this action right now; the hand state is unchanged."""


@dataclass(frozen=True)
class LegalActions:
    actions: frozenset[Action] = frozenset()
    call_amount: int = 0
    """Chips a CALL adds (capped at the stack)."""
    min_raise_to: int | None = None
    """Smallest street total a BET or RAISE may go to; equals ``max_raise_to`` when only an all-in is possible."""
    max_raise_to: int | None = None


NO_ACTIONS = LegalActions()


def legal_actions(*, stack: int, street_bet: int, current_bet: int, min_raise: int, can_raise: bool) -> LegalActions:
    """What a player may do now.

    ``min_raise`` is the size of the last full bet or raise this street (at least the big blind).
    ``can_raise`` is False when the player already acted and only incomplete all-ins came after.
    """
    to_call = max(current_bet - street_bet, 0)
    actions = {Action.FOLD}
    call_amount = 0
    if to_call == 0:
        actions.add(Action.CHECK)
    else:
        actions.add(Action.CALL)
        call_amount = min(to_call, stack)

    if stack <= to_call:
        actions.add(Action.ALL_IN)
        return LegalActions(frozenset(actions), call_amount)
    if not can_raise:
        return LegalActions(frozenset(actions), call_amount)

    actions.add(Action.BET if current_bet == 0 else Action.RAISE)
    actions.add(Action.ALL_IN)
    max_to = street_bet + stack
    min_to = min(current_bet + min_raise, max_to)
    return LegalActions(frozenset(actions), call_amount, min_to, max_to)


def resolve_action(action: Action, amount: object, legal: LegalActions, *, stack: int, street_bet: int) -> int | None:
    """The player's street total after ``action``, or None for a fold.

    ``amount`` is only read for BET and RAISE, where it is the street total to go to.
    """
    if action not in legal.actions:
        raise IllegalActionError(f"{action} is not allowed now")
    if action is Action.FOLD:
        return None
    if action is Action.CHECK:
        return street_bet
    if action is Action.CALL:
        return street_bet + legal.call_amount
    if action is Action.ALL_IN:
        return street_bet + stack

    if type(amount) is not int:
        raise IllegalActionError(f"{action} needs a whole-chip amount")
    if not legal.min_raise_to <= amount <= legal.max_raise_to:
        raise IllegalActionError(f"{action} must go to between {legal.min_raise_to} and {legal.max_raise_to}")
    return amount
