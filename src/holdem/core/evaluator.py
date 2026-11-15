from __future__ import annotations

from collections import Counter
from enum import IntEnum
from itertools import combinations
from typing import Iterable

from holdem.core.cards import Card


class HandCategory(IntEnum):
    HIGH_CARD = 0
    PAIR = 1
    TWO_PAIR = 2
    THREE_OF_A_KIND = 3
    STRAIGHT = 4
    FLUSH = 5
    FULL_HOUSE = 6
    FOUR_OF_A_KIND = 7
    STRAIGHT_FLUSH = 8


# (category, tiebreak values...): plain tuples compare as hand strength, so
# max() picks the winner and equal tuples split the pot. Suits never count.
HandRank = tuple[int, ...]

_WHEEL = (14, 5, 4, 3, 2)


def _straight_high(values: list[int]) -> int | None:
    """High card of a straight in five descending values, or None; A-2-3-4-5 is five high."""
    if len(set(values)) != 5:
        return None
    if values[0] - values[4] == 4:
        return values[0]
    if tuple(values) == _WHEEL:
        return 5
    return None


def _rank_five(five: tuple[Card, ...]) -> HandRank:
    values = sorted((card.value for card in five), reverse=True)
    is_flush = len({card.suit for card in five}) == 1
    straight_high = _straight_high(values)

    if straight_high and is_flush:
        return (HandCategory.STRAIGHT_FLUSH, straight_high)

    # Group by rank, bigger groups first, then higher rank: [(value, count), ...]
    groups = sorted(Counter(values).items(), key=lambda item: (item[1], item[0]), reverse=True)
    counts = [count for _, count in groups]
    ordered = [value for value, _ in groups]

    if counts == [4, 1]:
        return (HandCategory.FOUR_OF_A_KIND, *ordered)
    if counts == [3, 2]:
        return (HandCategory.FULL_HOUSE, *ordered)
    if is_flush:
        return (HandCategory.FLUSH, *values)
    if straight_high:
        return (HandCategory.STRAIGHT, straight_high)
    if counts == [3, 1, 1]:
        return (HandCategory.THREE_OF_A_KIND, *ordered)
    if counts == [2, 2, 1]:
        return (HandCategory.TWO_PAIR, *ordered)
    if counts == [2, 1, 1, 1]:
        return (HandCategory.PAIR, *ordered)
    return (HandCategory.HIGH_CARD, *values)


def _display_order(five: tuple[Card, ...], rank: HandRank) -> tuple[Card, ...]:
    """Sort the five cards the way a player reads them: groups first, wheel ace last."""
    wheel = rank[0] in (HandCategory.STRAIGHT, HandCategory.STRAIGHT_FLUSH) and rank[1] == 5
    counts = Counter(card.value for card in five)

    def key(card: Card) -> tuple[int, int, int]:
        value = 1 if wheel and card.value == 14 else card.value
        return (counts[card.value], value, "CDHS".index(card.suit))

    return tuple(sorted(five, key=key, reverse=True))


def best_hand(cards: Iterable[Card]) -> tuple[HandRank, tuple[Card, ...]]:
    """Best five-card hand from 5 to 7 cards: its rank and the five cards in display order."""
    cards = list(cards)
    if not 5 <= len(cards) <= 7:
        raise ValueError(f"need 5 to 7 cards, got {len(cards)}")
    if len(set(cards)) != len(cards):
        raise ValueError("duplicate cards")

    rank, five = max(((_rank_five(five), five) for five in combinations(cards, 5)), key=lambda item: item[0])
    return rank, _display_order(five, rank)


def evaluate(cards: Iterable[Card]) -> HandRank:
    """Rank of the best five-card hand from 5 to 7 cards."""
    return best_hand(cards)[0]


def hand_name_key(rank: HandRank) -> str:
    """i18n key naming the hand, e.g. ``"hand_full_house"``; an ace-high straight flush is a royal flush."""
    if rank[0] == HandCategory.STRAIGHT_FLUSH and rank[1] == 14:
        return "hand_royal_flush"
    return f"hand_{HandCategory(rank[0]).name.lower()}"
