from __future__ import annotations

import math
import tkinter as tk
from typing import Callable


FELT_CENTER = "#1f7f49"
FELT_EDGE = "#093520"
CARD_SHADOW = "#062614"
RAIL_DARK = "#24140a"
RAIL_LIGHT = "#6b4325"
GOLD = "#e8c766"
GOLD_DARK = "#a8862f"
CREAM = "#f6efd9"
MUTED = "#a7c4b1"
PANEL = "#0a2a18"
WIN = "#f5d76e"
LOSE = "#ff8a7a"
PUSH = "#d8e4dc"
BUST_FILL = "#8f1d1d"

TONE_COLORS = {"neutral": CREAM, "win": WIN, "lose": LOSE, "push": PUSH}

# (value, fill, edge, stripe)
CHIP_STYLES = (
    (500, "#6b3fa0", "#3e1f66", "#f3e8ff"),
    (100, "#1d1d1f", "#000000", GOLD),
    (25, "#1f8a4c", "#0f5530", CREAM),
    (5, "#c0392b", "#7d1f15", CREAM),
    (1, "#f1efe8", "#a9a393", "#2f6fb0"),
)

FELT_DISABLED = {"fill": "#1e3327", "depth": "#0f1d15", "outline": "#3b5446", "text": "#6c8476"}
BUTTON_STYLES = {
    "primary": {
        "fill": "#e2bd55", "hover": "#f0d27c", "depth": "#94711f", "outline": "#fff1c1", "text": "#2a1a08",
        "disabled": FELT_DISABLED,
    },
    "secondary": {
        "fill": "#14472c", "hover": "#1d5e3b", "depth": "#061c10", "outline": GOLD_DARK, "text": CREAM,
        "disabled": FELT_DISABLED,
    },
    "ghost": {
        "fill": "#3b2313", "hover": "#563420", "depth": "#1a0e06", "outline": GOLD_DARK, "text": CREAM,
        "disabled": {"fill": "#2e1c10", "depth": "#160c05", "outline": "#5a4526", "text": "#8a7560"},
    },
}


def lerp_color(start: str, end: str, t: float) -> str:
    a = [int(start[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(end[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def ease_out_cubic(t: float) -> float:
    return 1 - (1 - t) ** 3


def rounded_rect_points(x0: float, y0: float, x1: float, y1: float, radius: float) -> list[float]:
    r = min(radius, (x1 - x0) / 2, (y1 - y0) / 2)
    return [
        x0 + r, y0, x0 + r, y0, x1 - r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y0 + r,
        x1, y1 - r, x1, y1 - r, x1, y1, x1 - r, y1, x1 - r, y1, x0 + r, y1, x0 + r, y1,
        x0, y1, x0, y1 - r, x0, y1 - r, x0, y0 + r, x0, y0 + r, x0, y0,
    ]


def create_rounded_rect(canvas: tk.Canvas, x0: float, y0: float, x1: float, y1: float, radius: float, **options) -> int:
    return canvas.create_polygon(rounded_rect_points(x0, y0, x1, y1, radius), smooth=True, **options)


def create_pill(
    canvas: tk.Canvas,
    x: float,
    y: float,
    text: str,
    font,
    fill: str,
    outline: str,
    text_fill: str,
    tags: tuple[str, ...],
    anchor: str = "center",
    padding: tuple[int, int] = (12, 4),
) -> tuple[int, int, int, int]:
    label = canvas.create_text(x, y, text=text, font=font, fill=text_fill, anchor=anchor, tags=tags)
    x0, y0, x1, y1 = canvas.bbox(label)
    pad_x, pad_y = padding
    if anchor == "w":
        canvas.move(label, pad_x, 0)
        x1 += pad_x * 2
    elif anchor == "e":
        canvas.move(label, -pad_x, 0)
        x0 -= pad_x * 2
    else:
        x0 -= pad_x
        x1 += pad_x
    y0, y1 = y0 - pad_y, y1 + pad_y
    shape = create_rounded_rect(canvas, x0, y0, x1, y1, (y1 - y0) / 2, fill=fill, outline=outline, width=1, tags=tags)
    canvas.tag_lower(shape, label)
    return x0, y0, x1, y1


def chip_breakdown(amount: int, max_chips: int = 6) -> list[tuple[int, str, str, str]]:
    chips = []
    for style in CHIP_STYLES:
        while amount >= style[0] and len(chips) < max_chips:
            chips.append(style)
            amount -= style[0]
    return chips


def draw_chip(canvas: tk.Canvas, x: float, y: float, radius: float, style, tags: tuple[str, ...]) -> None:
    _, fill, edge, stripe = style
    canvas.create_oval(x - radius, y - radius + 2, x + radius, y + radius + 2, fill=CARD_SHADOW, outline="", tags=tags)
    canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=fill, outline=edge, tags=tags)
    inset = 3
    for index in range(6):
        canvas.create_arc(
            x - radius + inset,
            y - radius + inset,
            x + radius - inset,
            y + radius - inset,
            start=index * 60 + 18,
            extent=24,
            style="arc",
            outline=stripe,
            width=5,
            tags=tags,
        )
    inner = radius * 0.6
    canvas.create_oval(x - inner, y - inner, x + inner, y + inner, fill=fill, outline=stripe, tags=tags)


def draw_chip_stack(
    canvas: tk.Canvas,
    x: float,
    y: float,
    amount: int,
    tags: tuple[str, ...],
    radius: float = 24,
    thickness: float = 6,
) -> None:
    chips = chip_breakdown(amount)
    if not chips:
        return
    radius_y = radius * 0.42
    canvas.create_oval(
        x - radius - 3, y - radius_y + thickness + 2, x + radius + 3, y + radius_y + thickness + 5,
        fill=CARD_SHADOW, outline="", tags=tags,
    )
    for index, (_, fill, edge, stripe) in enumerate(chips):
        top = y - index * thickness
        side = lerp_color(fill, "#000000", 0.3)
        canvas.create_oval(x - radius, top - radius_y + thickness, x + radius, top + radius_y + thickness, fill=side, outline=edge, tags=tags)
        canvas.create_rectangle(x - radius, top, x + radius, top + thickness, fill=side, outline="", tags=tags)
        for offset in (-0.62, 0.0, 0.62):
            spot_x = x + offset * radius
            spot_y = top + radius_y * math.sqrt(1 - offset * offset)
            canvas.create_rectangle(spot_x - 3, spot_y, spot_x + 3, spot_y + thickness, fill=stripe, outline="", tags=tags)
        canvas.create_oval(x - radius, top - radius_y, x + radius, top + radius_y, fill=fill, outline=edge, tags=tags)
    _, fill, edge, stripe = chips[-1]
    top = y - (len(chips) - 1) * thickness
    for index in range(6):
        canvas.create_arc(
            x - radius + 3, top - radius_y + 2, x + radius - 3, top + radius_y - 2,
            start=index * 60 + 18, extent=22, style="arc", outline=stripe, width=4, tags=tags,
        )
    inner_x, inner_y = radius * 0.6, radius_y * 0.6
    canvas.create_oval(x - inner_x, top - inner_y, x + inner_x, top + inner_y, fill=fill, outline=stripe, tags=tags)


class CanvasButton:
    _next_id = 0

    def __init__(
        self,
        canvas: tk.Canvas,
        center: tuple[float, float],
        size: tuple[float, float],
        command: Callable[[], None],
        variant: str = "secondary",
        tags: tuple[str, ...] = (),
    ) -> None:
        CanvasButton._next_id += 1
        self.canvas = canvas
        self.command = command
        self.style = BUTTON_STYLES[variant]
        self.enabled = True
        self.hovered = False
        self.tag = f"button-{CanvasButton._next_id}"
        x, y = center
        width, height = size
        x0, y0, x1, y1 = x - width / 2, y - height / 2, x + width / 2, y + height / 2
        radius = min(12, height / 2)
        item_tags = (self.tag, *tags)
        self.depth = create_rounded_rect(canvas, x0, y0 + 3, x1, y1 + 3, radius, outline="", tags=item_tags)
        self.shape = create_rounded_rect(canvas, x0, y0, x1, y1, radius, width=1, tags=item_tags)
        self.label = canvas.create_text(x, y, text="", state=tk.DISABLED, tags=item_tags)
        canvas.tag_bind(self.tag, "<Enter>", self._on_enter)
        canvas.tag_bind(self.tag, "<Leave>", self._on_leave)
        canvas.tag_bind(self.tag, "<Button-1>", lambda event: self.invoke())
        self._apply_style()

    @property
    def text(self) -> str:
        return self.canvas.itemcget(self.label, "text")

    def set_text(self, text: str, font) -> None:
        self.canvas.itemconfig(self.label, text=text, font=font)

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self._apply_style()

    def invoke(self) -> None:
        if self.enabled:
            self.command()

    def _on_enter(self, event=None) -> None:
        self.hovered = True
        self._apply_style()

    def _on_leave(self, event=None) -> None:
        self.hovered = False
        self.canvas.config(cursor="")
        self._apply_style()

    def _apply_style(self) -> None:
        style = self.style if self.enabled else self.style["disabled"]
        fill = style["hover"] if self.enabled and self.hovered else style["fill"]
        self.canvas.itemconfig(self.depth, fill=style["depth"])
        self.canvas.itemconfig(self.shape, fill=fill, outline=style["outline"])
        self.canvas.itemconfig(self.label, fill=style["text"])
        if self.hovered:
            self.canvas.config(cursor="hand2" if self.enabled else "")
