"""Table geometry and bet-size math for the client, kept free of tkinter so it is easy to test."""

from __future__ import annotations

import bisect
import math

WIDTH, HEIGHT = 1200, 720
TOP_RAIL_BOTTOM = 56
BOTTOM_RAIL_TOP = 628
CONTROL_Y = 674
TABLE_CENTER = (600.0, 334.0)
SEAT_RADII = (478.0, 214.0)

Point = tuple[float, float]

SLIDER_STOPS = 80


def seat_positions(max_seats: int, your_seat: int | None = None) -> list[Point]:
    """Anchor point of every seat around the oval, with ``your_seat`` (or seat 0) at the bottom.

    Seat numbers go clockwise, so the next seat after yours is on your left (screen left).
    """
    center_x, center_y = TABLE_CENTER
    radius_x, radius_y = SEAT_RADII
    anchor = your_seat if your_seat is not None else 0
    positions = []
    for seat in range(max_seats):
        angle = math.pi / 2 + 2 * math.pi * (seat - anchor) / max_seats
        positions.append((center_x + radius_x * math.cos(angle), center_y + radius_y * math.sin(angle)))
    return positions


def toward_center(point: Point, fraction: float) -> Point:
    x, y = point
    center_x, center_y = TABLE_CENTER
    return x + (center_x - x) * fraction, y + (center_y - y) * fraction


def preset_amount(fraction: float, pot: int, my_bet: int, call_amount: int, min_to: int, max_to: int) -> int:
    """Street total for betting ``fraction`` of the pot, the pot counted as if you had called first.

    ``pot`` is every chip in the middle, including bets in front of players this street.
    """
    target = my_bet + call_amount + round(fraction * (pot + call_amount))
    return max(min_to, min(max_to, target))


def bet_steps(min_to: int, max_to: int, step: int, stops: int = SLIDER_STOPS) -> list[int]:
    """The amount at each of the raise slider's ``stops + 1`` positions: exponential from the legal
    minimum to the all-in amount, rounded to multiples of ``step`` (the big blind). Small amounts
    get as much of the slider as large ones, so neighbouring positions near the bottom often share
    an amount. A convenience only; any amount in range is legal."""
    if max_to <= min_to:
        return [min_to]
    ratio = max_to / min_to
    inner = (round(min_to * ratio ** (index / stops) / step) * step for index in range(1, stops))
    return [min_to, *(min(max(amount, min_to), max_to) for amount in inner), max_to]


def nearest_step(steps: list[int], amount: int) -> int:
    """First position whose amount is closest to ``amount`` (the lower amount on a tie)."""
    index = bisect.bisect_left(steps, amount)
    if index == len(steps):
        value = steps[-1]
    elif index == 0 or steps[index] - amount < amount - steps[index - 1]:
        value = steps[index]
    else:
        value = steps[index - 1]
    return bisect.bisect_left(steps, value)


def next_step(steps: list[int], index: int, direction: int) -> int:
    """Position of the next different amount up (``direction`` 1) or down (-1) from ``index``."""
    amount = steps[index]
    if direction > 0:
        return min(bisect.bisect_right(steps, amount), len(steps) - 1)
    lower = bisect.bisect_left(steps, amount)
    return bisect.bisect_left(steps, steps[lower - 1]) if lower > 0 else 0
