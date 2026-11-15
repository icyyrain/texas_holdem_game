import pytest

from holdem.core.cards import Card
from holdem.core.evaluator import HandCategory, best_hand, evaluate, hand_name_key
from holdem.i18n import LANGUAGES, TEXTS


def cards(text):
    return [Card.from_code(code) for code in text.split()]


def rank(text):
    return evaluate(cards(text))


@pytest.mark.parametrize(
    "hand, expected",
    [
        ("AS KD 9C 7H 3S", (HandCategory.HIGH_CARD, 14, 13, 9, 7, 3)),
        ("9S 9D AC 7H 3S", (HandCategory.PAIR, 9, 14, 7, 3)),
        ("9S 9D 3C 3H AS", (HandCategory.TWO_PAIR, 9, 3, 14)),
        ("9S 9D 9C 7H 3S", (HandCategory.THREE_OF_A_KIND, 9, 7, 3)),
        ("5S 6D 7C 8H 9S", (HandCategory.STRAIGHT, 9)),
        ("AH JH 9H 7H 3H", (HandCategory.FLUSH, 14, 11, 9, 7, 3)),
        ("9S 9D 9C 3H 3S", (HandCategory.FULL_HOUSE, 9, 3)),
        ("9S 9D 9C 9H 3S", (HandCategory.FOUR_OF_A_KIND, 9, 3)),
        ("5H 6H 7H 8H 9H", (HandCategory.STRAIGHT_FLUSH, 9)),
        ("TS JS QS KS AS", (HandCategory.STRAIGHT_FLUSH, 14)),
    ],
)
def test_identifies_each_category_from_five_cards(hand, expected):
    assert rank(hand) == expected


def test_categories_rank_in_poker_order():
    ordered = [
        "AS KD 9C 7H 3S",
        "2S 2D 3C 4H 6S",
        "2S 2D 3C 3H 4S",
        "2S 2D 2C 3H 4S",
        "AS 2D 3C 4H 5S",
        "2H 3H 4H 5H 7H",
        "2S 2D 2C 3H 3S",
        "2S 2D 2C 2H 3S",
        "AH 2H 3H 4H 5H",
    ]
    ranks = [rank(hand) for hand in ordered]
    assert ranks == sorted(ranks)
    assert len(set(ranks)) == len(ranks)


def test_ace_low_straight_is_five_high():
    assert rank("AS 2D 3C 4H 5S") == (HandCategory.STRAIGHT, 5)
    assert rank("AS 2D 3C 4H 5S") < rank("2S 3D 4C 5H 6S")


def test_ace_high_straight_beats_king_high():
    assert rank("TS JD QC KH AS") > rank("9S TD JC QH KS")


def test_ace_low_straight_flush_is_five_high():
    assert rank("AD 2D 3D 4D 5D") == (HandCategory.STRAIGHT_FLUSH, 5)


def test_straights_do_not_wrap_around():
    assert rank("QS KD AC 2H 3S")[0] == HandCategory.HIGH_CARD


@pytest.mark.parametrize(
    "better, worse",
    [
        ("AS AD KC 4H 3S", "AH AC QD 4S 3H"),
        ("AS AD KC 5H 3S", "AH AC KD 4S 3H"),
        ("KS KD 4C 4H 9S", "KH KC 4D 4S 8H"),
        ("KS KD 5C 5H 2S", "KH KC 4D 4S AH"),
        ("AS KD 9C 7H 4S", "AH KC 9D 7S 3H"),
        ("8S 8D 8C AH 2S", "8S 8D 8C KH QS"),
        ("8S 8D 8C 2H 2S", "7S 7D 7C AH AS"),
        ("8S 8D 8C 8H 3S", "8S 8D 8C 8H 2S"),
        ("AH QH 9H 7H 3H", "AS JS 9S 7S 3S"),
    ],
)
def test_kickers_break_ties_within_a_category(better, worse):
    assert rank(better) > rank(worse)


def test_suits_never_break_ties():
    assert rank("AS KD 9C 7H 3S") == rank("AH KC 9D 7S 3H")


def test_seven_cards_choose_the_best_five():
    assert evaluate(cards("2C 7D 9S 9D 9C 3H 3S")) == (HandCategory.FULL_HOUSE, 9, 3)


def test_flush_beats_straight_in_the_same_seven_cards():
    assert evaluate(cards("4H 5H 6S 7H 8C 2H KH"))[0] == HandCategory.FLUSH


def test_six_card_straight_uses_the_highest_five():
    assert evaluate(cards("3S 4D 5C 6H 7S 8D KC")) == (HandCategory.STRAIGHT, 8)


def test_six_suited_cards_use_the_highest_five():
    assert evaluate(cards("2H 4H 6H 8H TH QH AS")) == (HandCategory.FLUSH, 12, 10, 8, 6, 4)


def test_two_sets_of_trips_make_the_best_full_house():
    assert evaluate(cards("AS AD AC KH KS KD 2C")) == (HandCategory.FULL_HOUSE, 14, 13)


def test_three_pairs_keep_the_best_two_and_best_kicker():
    assert evaluate(cards("AS AD KC KH 3S 3D 2C")) == (HandCategory.TWO_PAIR, 14, 13, 3)


def test_quads_take_the_highest_remaining_kicker():
    assert evaluate(cards("9S 9D 9C 9H KS KD 2C")) == (HandCategory.FOUR_OF_A_KIND, 9, 13)


def test_straight_flush_beats_quads_in_the_same_seven_cards():
    assert evaluate(cards("5H 6H 7H 8H 9H 9S 9D"))[0] == HandCategory.STRAIGHT_FLUSH


def test_board_plays_as_a_split_pot():
    board = cards("AS KS QS JS TS")
    assert evaluate(board + cards("2C 3D")) == evaluate(board + cards("4H 5C"))


def test_wheel_hidden_among_higher_cards():
    assert evaluate(cards("AS 2D 3C 4H 5S KD KC")) == (HandCategory.STRAIGHT, 5)


def test_best_hand_returns_the_five_cards_in_display_order():
    hand_rank, five = best_hand(cards("9S 2C 9D 3H 9C 3S 7D"))

    assert hand_rank == (HandCategory.FULL_HOUSE, 9, 3)
    assert [card.value for card in five] == [9, 9, 9, 3, 3]
    assert {card.suit for card in five[:3]} == {"S", "D", "C"}


def test_best_hand_orders_the_wheel_with_ace_last():
    _, five = best_hand(cards("AS 2D 3C 4H 5S KD KC"))

    assert [card.rank for card in five] == ["5", "4", "3", "2", "A"]


@pytest.mark.parametrize("hand", ["AS KD 9C 7H", "AS KD 9C 7H 3S 2D 4C 5H"])
def test_rejects_wrong_number_of_cards(hand):
    with pytest.raises(ValueError):
        evaluate(cards(hand))


def test_rejects_duplicate_cards():
    with pytest.raises(ValueError):
        evaluate(cards("AS AS 9C 7H 3S"))


def test_hand_name_keys():
    assert hand_name_key(rank("9S 9D 9C 3H 3S")) == "hand_full_house"
    assert hand_name_key(rank("5H 6H 7H 8H 9H")) == "hand_straight_flush"
    assert hand_name_key(rank("TS JS QS KS AS")) == "hand_royal_flush"


def test_every_hand_name_key_is_translated():
    keys = {hand_name_key((category, 9)) for category in HandCategory} | {"hand_royal_flush"}
    for language in LANGUAGES:
        assert keys <= TEXTS[language].keys()
