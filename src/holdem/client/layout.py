"""Table geometry and bet-size math for the client, kept free of tkinter so it is easy to test."""

from __future__ import annotations

import math

WIDTH, HEIGHT = 1200, 720
TOP_RAIL_BOTTOM = 56
BOTTOM_RAIL_TOP = 628
CONTROL_Y = 674
TABLE_CENTER = (600.0, 334.0)
SEAT_RADII = (478.0, 214.0)

Point = tuple[float, float]


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
