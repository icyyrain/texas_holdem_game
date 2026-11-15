from __future__ import annotations

from pathlib import Path
import sys

from holdem.core.cards import Card


CARD_IMAGE_DIR = "cards"
SMALL_CARD_IMAGE_DIR = "cards_small"


def resource_root() -> Path:
    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        return Path(bundled_root) / "assets"
    return Path(__file__).resolve().parents[3] / "assets"


def card_image_name(card: Card) -> str:
    return f"{card.code}.png"


def _image_dir(root: Path | None, small: bool) -> Path:
    asset_root = resource_root() if root is None else root
    return asset_root / (SMALL_CARD_IMAGE_DIR if small else CARD_IMAGE_DIR)


def card_image_path(card: Card, root: Path | None = None, small: bool = False) -> Path:
    """96x134 PNG for ``card``, or 60x84 when ``small``."""
    return _image_dir(root, small) / card_image_name(card)


def card_back_path(root: Path | None = None, small: bool = False) -> Path:
    return _image_dir(root, small) / "back.png"
