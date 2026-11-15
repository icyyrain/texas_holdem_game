from __future__ import annotations

from dataclasses import dataclass
import random
import secrets


RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "T", "J", "Q", "K", "A")
SUITS = ("C", "D", "H", "S")
SUIT_SYMBOLS = {
    "C": "♣",
    "D": "♦",
    "H": "♥",
    "S": "♠",
}
RANK_VALUES = {rank: value for value, rank in enumerate(RANKS, start=2)}


@dataclass(frozen=True)
class Card:
    rank: str
    suit: str

    def __post_init__(self) -> None:
        if self.rank not in RANK_VALUES:
            raise ValueError(f"invalid rank: {self.rank!r}")
        if self.suit not in SUIT_SYMBOLS:
            raise ValueError(f"invalid suit: {self.suit!r}")

    @classmethod
    def from_code(cls, code: str) -> Card:
        if len(code) != 2:
            raise ValueError(f"invalid card code: {code!r}")
        return cls(code[0], code[1])

    @property
    def value(self) -> int:
        """Rank strength from 2 (deuce) to 14 (ace)."""
        return RANK_VALUES[self.rank]

    @property
    def code(self) -> str:
        """Two-character code such as ``"AS"`` or ``"TH"``; also the card image name."""
        return f"{self.rank}{self.suit}"

    def display(self) -> str:
        rank = "10" if self.rank == "T" else self.rank
        return f"[{rank}{SUIT_SYMBOLS[self.suit]}]"


class Deck:
    def __init__(self, cards: list[Card]):
        self.cards = list(cards)

    @classmethod
    def standard(cls) -> Deck:
        return cls([Card(rank, suit) for suit in SUITS for rank in RANKS])

    def shuffle(self, rng: random.Random | None = None) -> None:
        (rng or secrets.SystemRandom()).shuffle(self.cards)

    def deal(self) -> Card:
        if not self.cards:
            raise IndexError("cannot deal from an empty deck")
        return self.cards.pop()

    def __len__(self) -> int:
        return len(self.cards)
