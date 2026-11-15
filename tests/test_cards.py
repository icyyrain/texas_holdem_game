import random

import pytest

from holdem.core.cards import Card, Deck


def test_new_deck_contains_52_unique_cards():
    deck = Deck.standard()

    assert len(deck.cards) == 52
    assert len(set(deck.cards)) == 52


def test_shuffle_preserves_same_cards():
    deck = Deck.standard()
    before = set(deck.cards)

    deck.shuffle()

    assert set(deck.cards) == before
    assert len(deck.cards) == 52


def test_shuffle_accepts_injected_rng_for_reproducible_order():
    first = Deck.standard()
    second = Deck.standard()

    first.shuffle(random.Random(7))
    second.shuffle(random.Random(7))

    assert first.cards == second.cards


def test_deal_pops_from_top_of_deck():
    top = Card("A", "S")
    bottom = Card("9", "H")
    deck = Deck([bottom, top])

    dealt = deck.deal()

    assert dealt == top
    assert deck.cards == [bottom]


def test_deal_from_empty_deck_raises():
    with pytest.raises(IndexError):
        Deck([]).deal()


def test_card_values_run_from_deuce_to_ace():
    assert Card("2", "C").value == 2
    assert Card("T", "D").value == 10
    assert Card("K", "H").value == 13
    assert Card("A", "S").value == 14


def test_card_code_round_trips():
    for card in Deck.standard().cards:
        assert Card.from_code(card.code) == card


@pytest.mark.parametrize("rank, suit", [("1", "S"), ("10", "S"), ("A", "X"), ("a", "S")])
def test_invalid_cards_are_rejected(rank, suit):
    with pytest.raises(ValueError):
        Card(rank, suit)


@pytest.mark.parametrize("code", ["", "A", "10S", "ASX"])
def test_invalid_codes_are_rejected(code):
    with pytest.raises(ValueError):
        Card.from_code(code)


def test_card_display_uses_suit_symbols():
    assert Card("A", "S").display() == "[A♠]"
    assert Card("T", "H").display() == "[10♥]"
    assert Card("Q", "D").display() == "[Q♦]"
    assert Card("2", "C").display() == "[2♣]"
