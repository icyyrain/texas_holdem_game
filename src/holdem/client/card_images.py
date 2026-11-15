from __future__ import annotations

from pathlib import Path
import sys

from holdem.core.cards import Card


CARD_IMAGE_DIR = "cards"


def resource_root() -> Path:
    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        return Path(bundled_root) / "assets"
    return Path(__file__).resolve().parents[3] / "assets"


def card_image_name(card: Card) -> str:
    return f"{card.code}.png"


def card_image_path(card: Card, root: Path | None = None) -> Path:
    asset_root = resource_root() if root is None else root
    return asset_root / CARD_IMAGE_DIR / card_image_name(card)


def card_back_path(root: Path | None = None) -> Path:
    asset_root = resource_root() if root is None else root
    return asset_root / CARD_IMAGE_DIR / "back.png"
