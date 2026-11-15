from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from holdem.core.evaluator import HandRank


@dataclass(frozen=True)
class Pot:
    amount: int
    eligible: frozenset[int]


def uncalled_excess(bets: Mapping[int, int]) -> tuple[int, int] | None:
    """(seat, chips) for the part of the biggest bet that nobody matched, or None."""
    ranked = sorted(bets.items(), key=lambda item: item[1], reverse=True)
    top_seat, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0
    if top > second:
        return top_seat, top - second
    return None


def build_pots(contributions: Mapping[int, int], live: Iterable[int]) -> list[Pot]:
    """Main pot then side pots, layered by what each live (not folded) seat put in this hand.

    Folded seats' chips count toward each layer they reached, but they are never eligible.
    """
    live = set(live)
    levels = sorted({contributions[seat] for seat in live if contributions[seat] > 0})
    pots: list[Pot] = []
    previous = 0
    for level in levels:
        amount = sum(min(chips, level) - min(chips, previous) for chips in contributions.values())
        eligible = frozenset(seat for seat in live if contributions[seat] >= level)
        pots.append(Pot(amount, eligible))
        previous = level

    # Folded chips above the top live level; uncalled bets are returned first, so this is rare.
    leftover = sum(chips - min(chips, previous) for chips in contributions.values())
    if leftover:
        if pots:
            pots[-1] = Pot(pots[-1].amount + leftover, pots[-1].eligible)
        else:
            pots.append(Pot(leftover, frozenset(live)))
    return pots


def split_pot(pot: Pot, ranks: Mapping[int, HandRank], order: Sequence[int]) -> dict[int, int]:
    """Chips each winner takes from one pot.

    ``order`` lists seats clockwise from the button's left; the odd chips of a split go to
    the first winner in it.
    """
    if len(pot.eligible) == 1:
        (seat,) = pot.eligible
        return {seat: pot.amount}
    best = max(ranks[seat] for seat in pot.eligible)
    winners = [seat for seat in order if seat in pot.eligible and ranks[seat] == best]
    share, odd = divmod(pot.amount, len(winners))
    shares = {seat: share for seat in winners}
    shares[winners[0]] += odd
    return shares
