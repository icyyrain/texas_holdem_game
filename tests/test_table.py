import pytest

from holdem.core.betting import Action
from holdem.core.table import Table, TableConfig


def make_table(**overrides):
    settings = {"small_blind": 1, "big_blind": 2, "min_buy_in": 40, "max_buy_in": 200, "max_seats": 9}
    return Table(TableConfig(**{**settings, **overrides}))


def fold_around(hand):
    while not hand.is_complete():
        hand.apply(hand.to_act, Action.FOLD)


@pytest.mark.parametrize(
    "settings",
    [
        {"small_blind": 0},
        {"big_blind": 0},
        {"min_buy_in": 0},
        {"max_buy_in": 30},
        {"max_seats": 1},
        {"max_seats": 10},
        {"action_timeout": 0},
        {"big_blind": 2.5},
    ],
)
def test_invalid_config_is_rejected(settings):
    with pytest.raises(ValueError):
        make_table(**settings)


def test_sit_validates_seat_player_and_buy_in():
    table = make_table()
    table.sit("alice", 3, 100)

    for player, seat, buy_in in [
        ("bob", 3, 100),
        ("alice", 4, 100),
        ("bob", 9, 100),
        ("bob", -1, 100),
        ("bob", 4, 39),
        ("bob", 4, 201),
        ("bob", 4, 100.0),
    ]:
        with pytest.raises(ValueError):
            table.sit(player, seat, buy_in)
    assert table.seat_of("alice").stack == 100


def test_button_moves_clockwise_over_empty_seats_and_wraps():
    table = make_table()
    for player, seat in [("a", 1), ("b", 4), ("c", 6)]:
        table.sit(player, seat, 100)

    buttons = []
    for _ in range(4):
        hand = table.start_hand()
        buttons.append(hand.button)
        fold_around(hand)

    assert buttons == [1, 4, 6, 1]


def test_hand_needs_two_players_with_chips_who_are_not_away():
    table = make_table()
    table.sit("a", 0, 100)
    with pytest.raises(ValueError):
        table.start_hand()

    table.sit("b", 1, 100)
    table.sit("c", 2, 100)
    table.set_away("c", True)
    hand = table.start_hand()
    assert set(hand.players) == {0, 1}


def test_busted_players_are_dealt_out():
    table = make_table()
    for player, seat in [("a", 0), ("b", 1), ("c", 2)]:
        table.sit(player, seat, 100)
    table.seat_of("c").stack = 0

    assert set(table.start_hand().players) == {0, 1}


def test_cannot_start_a_hand_while_one_is_running():
    table = make_table()
    table.sit("a", 0, 100)
    table.sit("b", 1, 100)
    table.start_hand()

    with pytest.raises(ValueError):
        table.start_hand()


def test_leaving_mid_hand_frees_the_seat_after_the_hand():
    table = make_table()
    for player, seat in [("a", 0), ("b", 1), ("c", 2)]:
        table.sit(player, seat, 100)
    hand = table.start_hand()

    assert table.leave("b") is False
    assert table.seat_of("b") is not None
    fold_around(hand)
    assert table.finish_hand() == ["b"]
    assert table.seat_of("b") is None


def test_leaving_between_hands_is_immediate():
    table = make_table()
    table.sit("a", 0, 100)

    assert table.leave("a") is True
    assert table.seat_of("a") is None
    with pytest.raises(ValueError):
        table.leave("a")


def test_player_who_sat_down_mid_hand_can_leave_at_once():
    table = make_table()
    table.sit("a", 0, 100)
    table.sit("b", 1, 100)
    table.start_hand()
    table.sit("c", 2, 100)

    assert table.leave("c") is True


def test_rebuy_only_between_hands_and_up_to_the_max_buy_in():
    table = make_table()
    table.sit("a", 0, 100)
    table.sit("b", 1, 100)

    table.rebuy("a", 100)
    assert table.seat_of("a").stack == 200
    with pytest.raises(ValueError):
        table.rebuy("a", 1)

    table.start_hand()
    with pytest.raises(ValueError):
        table.rebuy("b", 10)


def test_hand_results_land_in_the_table_seats():
    table = make_table()
    table.sit("a", 0, 100)
    table.sit("b", 1, 100)

    fold_around(table.start_hand())

    assert [table.seat_of(player).stack for player in "ab"] == [99, 101]
