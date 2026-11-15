"""A rule-based bot that estimates its equity by Monte Carlo simulation.

Preflop it ranks its two cards with the Chen formula. After the flop it deals out the rest of
the board and the live opponents' hands a few hundred times and counts how often it wins,
dealing opponents who bet or raised stronger starting hands than the others. The equity is
then weighed against the price of calling and turned into a fold, check, call, bet or raise,
with a little randomness so the bot is not completely predictable.

Pure functions only: no I/O, and nothing here sees cards a player at the table could not see.
"""

from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass

from holdem.core.betting import Action, LegalActions
from holdem.core.cards import RANKS, SUITS, Card
from holdem.core.evaluator import evaluate
from holdem.core.table import HandState, Street

FOLD, CHECK, CALL, BET, RAISE, ALL_IN = Action

# About 75 µs per seven-card evaluation; this keeps one decision near 100 ms on the server's loop.
MAX_EVALUATIONS = 1200
MIN_TRIALS = 100
# Opponents who put in a bet or raise this hand are dealt hands from this top share of starting
# hands; the others from the wider share (players who limped or called still fold their worst hands).
AGGRESSIVE_RANGE = 0.3
PASSIVE_RANGE = 0.7
_DEAL_TRIES = 30

FULL_DECK = tuple(Card(rank, suit) for suit in SUITS for rank in RANKS)


@dataclass(frozen=True)
class Situation:
    """What a player deciding now can know: public table information plus their own two cards."""

    legal: LegalActions
    hole: tuple[Card, ...]
    board: tuple[Card, ...]
    street: Street
    pot: int
    """All chips in the middle, including bets in front of the players this street."""
    to_call: int
    street_bet: int
    stack: int
    big_blind: int
    current_bet: int
    opponents: int
    """Opponents still holding cards."""
    aggressors: int
    """Of those, how many bet or raised at some point this hand."""


def situation_for(hand: HandState, seat: int) -> Situation:
    """The decision facing ``seat`` in ``hand``; uses only that seat's hole cards."""
    me = hand.players[seat]
    live = [index for index, player in hand.players.items() if not player.folded and index != seat]
    aggressive = _aggressive_seats(hand.events)
    return Situation(
        legal=hand.legal_actions(seat),
        hole=tuple(me.hole),
        board=tuple(hand.board),
        street=hand.street,
        pot=sum(player.total_bet for player in hand.players.values()),
        to_call=max(hand.current_bet - me.street_bet, 0),
        street_bet=me.street_bet,
        stack=me.stack,
        big_blind=hand.config.big_blind,
        current_bet=hand.current_bet,
        opponents=len(live),
        aggressors=sum(1 for index in live if index in aggressive),
    )


def _aggressive_seats(events: list[dict]) -> set[int]:
    """Seats that raised the price at least once (a bet, a raise, or an all-in that raised)."""
    seats = set()
    level = 0
    for event in events:
        if event["type"] == "street":
            level = 0
        elif event["type"] == "blind":
            level = max(level, event["amount"])
        elif event["type"] == "action":
            if event["to"] > level:
                seats.add(event["seat"])
            level = max(level, event["to"])
    return seats


# Starting hands


def chen_score(first: Card, second: Card) -> int:
    """Bill Chen's quick starting-hand score: 20 for aces down to -1 for 7-2 offsuit."""
    high, low = sorted((first, second), key=lambda card: card.value, reverse=True)
    points = {14: 10.0, 13: 8.0, 12: 7.0, 11: 6.0}.get(high.value, high.value / 2)
    if high.value == low.value:
        return math.ceil(max(points * 2, 5))
    if high.suit == low.suit:
        points += 2
    gap = high.value - low.value - 1
    points -= (0, 1, 2, 4)[gap] if gap < 4 else 5
    if gap <= 1 and high.value < 12:
        points += 1
    return math.ceil(points)


def _hand_class(first: Card, second: Card) -> tuple[int, int, bool]:
    high, low = sorted((first.value, second.value), reverse=True)
    return high, low, first.suit == second.suit and high != low


def _build_percentiles() -> dict[tuple[int, int, bool], float]:
    """Each of the 169 starting-hand classes mapped to the share of all deals that rank at least
    as high by Chen score (0.0 = the very best, 1.0 = the very worst)."""
    combos: dict[tuple[int, int, bool], int] = {}
    scores: dict[tuple[int, int, bool], int] = {}
    for first, second in itertools.combinations(FULL_DECK, 2):
        key = _hand_class(first, second)
        combos[key] = combos.get(key, 0) + 1
        scores[key] = chen_score(first, second)
    total = sum(combos.values())
    percentiles = {}
    for key, score in scores.items():
        better_or_equal = sum(count for other, count in combos.items() if scores[other] >= score)
        percentiles[key] = better_or_equal / total
    return percentiles


HAND_PERCENTILE = _build_percentiles()


# Equity


def estimate_equity(
    hole: tuple[Card, ...],
    board: tuple[Card, ...],
    opponent_ranges: list[float],
    rng: random.Random,
    trials: int,
) -> float:
    """Share of the pot ``hole`` wins on average against opponents dealt from the top
    ``opponent_ranges[i]`` share of starting hands, with the board completed at random."""
    if not opponent_ranges:
        return 1.0
    known = set(hole) | set(board)
    remaining = [card for card in FULL_DECK if card not in known]
    missing = 5 - len(board)
    won = 0.0
    for _ in range(trials):
        deck = remaining[:]
        rng.shuffle(deck)
        full_board = list(board) + [deck.pop() for _ in range(missing)]
        mine = evaluate(list(hole) + full_board)
        others = [evaluate(_deal_from_range(deck, share, rng) + full_board) for share in opponent_ranges]
        best = max(others)
        if mine > best:
            won += 1
        elif mine == best:
            won += 1 / (1 + others.count(best))
    return won / trials


def _deal_from_range(deck: list[Card], share: float, rng: random.Random) -> list[Card]:
    """Take two cards from the shuffled ``deck`` that fall in the top ``share`` of starting hands,
    trying a few pairs before settling for whatever comes next."""
    for _ in range(_DEAL_TRIES):
        first, second = deck[-1], deck[-2]
        if share >= 1 or HAND_PERCENTILE[_hand_class(first, second)] <= share:
            del deck[-2:]
            return [first, second]
        # Put the rejected pair somewhere random lower down and look at a new one.
        index = rng.randrange(len(deck) - 1)
        deck[index], deck[-1] = deck[-1], deck[index]
        index = rng.randrange(len(deck) - 1)
        deck[index], deck[-2] = deck[-2], deck[index]
    return [deck.pop(), deck.pop()]


def opponent_ranges(situation: Situation) -> list[float]:
    aggressive = [AGGRESSIVE_RANGE] * situation.aggressors
    return aggressive + [PASSIVE_RANGE] * (situation.opponents - situation.aggressors)


# Decisions


def smart_action(situation: Situation, rng: random.Random) -> tuple[Action, int | None]:
    if situation.street is Street.PREFLOP:
        return _preflop(situation, rng)
    return _postflop(situation, rng)


def _preflop(s: Situation, rng: random.Random) -> tuple[Action, int | None]:
    score = chen_score(*s.hole)
    bb = s.big_blind
    # Tighter with more players left to beat.
    score -= max(s.opponents - 3, 0) // 2
    short = s.stack + s.street_bet <= 12 * bb
    raised = s.current_bet > bb

    if short and score >= (10 if raised else 8):
        return _all_in(s)
    if not raised:
        if score >= 10 or (score >= 8 and rng.random() < 0.35):
            return _raise_to(s, (3 + (s.opponents > 3)) * bb)
        if score >= 6 or (score >= 4 and s.to_call == 0):
            return _call(s)
        return _fold(s)

    price = s.to_call / max(s.stack + s.street_bet, 1)
    if score >= 12:
        return _raise_to(s, 3 * s.current_bet)
    if (score >= 9 and price <= 0.15) or (score >= 7 and price <= 0.05):
        return _call(s)
    return _fold(s)


def _postflop(s: Situation, rng: random.Random) -> tuple[Action, int | None]:
    ranges = opponent_ranges(s)
    trials = max(MIN_TRIALS, MAX_EVALUATIONS // (len(ranges) + 1))
    equity = estimate_equity(s.hole, s.board, ranges, rng, trials)
    # One opponent leaves more room to bet thinner than a crowd does.
    strong, decent = (0.7, 0.55) if s.opponents == 1 else (0.75, 0.6)

    if s.to_call == 0:
        if equity >= strong:
            return _bet_pot(s, rng.choice((0.5, 0.66, 0.75)))
        if equity >= decent and rng.random() < 0.5:
            return _bet_pot(s, 0.5)
        if s.opponents <= 2 and s.street is not Street.FLOP and rng.random() < 0.08:
            return _bet_pot(s, 0.5)  # an occasional bluff
        return _call(s)

    pot_odds = s.to_call / (s.pot + s.to_call)
    if equity >= max(strong + 0.1, 0.8) and rng.random() < 0.7:
        return _raise_to(s, s.current_bet + _fraction_of_pot(s, 0.75))
    if equity >= pot_odds + 0.05:
        return _call(s)
    if s.to_call <= s.big_blind and equity >= pot_odds - 0.05:
        return _call(s)  # too cheap to give up
    return _fold(s)


def _fraction_of_pot(s: Situation, fraction: float) -> int:
    return round(fraction * (s.pot + s.to_call))


def _bet_pot(s: Situation, fraction: float) -> tuple[Action, int | None]:
    return _raise_to(s, s.current_bet + max(_fraction_of_pot(s, fraction), s.big_blind))


def _raise_to(s: Situation, target: int) -> tuple[Action, int | None]:
    legal = s.legal
    raise_action = BET if BET in legal.actions else RAISE if RAISE in legal.actions else None
    if raise_action is None:
        return _all_in(s) if ALL_IN in legal.actions and target >= s.street_bet + s.stack else _call(s)
    if target >= legal.max_raise_to:
        return _all_in(s)
    return raise_action, max(target, legal.min_raise_to)


def _all_in(s: Situation) -> tuple[Action, int | None]:
    return (ALL_IN, None) if ALL_IN in s.legal.actions else _call(s)


def _call(s: Situation) -> tuple[Action, int | None]:
    if CHECK in s.legal.actions:
        return CHECK, None
    if CALL in s.legal.actions:
        return CALL, None
    return ALL_IN, None  # calling costs the whole stack


def _fold(s: Situation) -> tuple[Action, int | None]:
    return (CHECK, None) if CHECK in s.legal.actions else (FOLD, None)
