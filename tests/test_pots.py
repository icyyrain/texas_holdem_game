from holdem.core.pots import Pot, build_pots, split_pot, uncalled_excess


def test_equal_contributions_make_one_pot():
    assert build_pots({0: 10, 1: 10, 2: 10}, live=[0, 1, 2]) == [Pot(30, frozenset({0, 1, 2}))]


def test_all_ins_for_different_amounts_make_side_pots():
    pots = build_pots({0: 100, 1: 300, 2: 300}, live=[0, 1, 2])

    assert pots == [Pot(300, frozenset({0, 1, 2})), Pot(400, frozenset({1, 2}))]


def test_folded_chips_stay_in_but_folded_players_cannot_win():
    assert build_pots({0: 5, 1: 10, 2: 10}, live=[1, 2]) == [Pot(25, frozenset({1, 2}))]


def test_folded_chips_above_a_short_all_in_go_to_the_side_pot():
    pots = build_pots({0: 100, 1: 40, 2: 100}, live=[1, 2])

    assert pots == [Pot(120, frozenset({1, 2})), Pot(120, frozenset({2}))]


def test_no_contributions_make_no_pots():
    assert build_pots({0: 0, 1: 0}, live=[0, 1]) == []


def test_uncalled_excess_is_the_top_bet_above_the_second():
    assert uncalled_excess({0: 100, 1: 300, 2: 0}) == (1, 200)


def test_matched_bets_have_no_uncalled_excess():
    assert uncalled_excess({0: 300, 1: 300, 2: 50}) is None


def test_single_winner_takes_the_pot():
    pot = Pot(30, frozenset({0, 1, 2}))

    assert split_pot(pot, {0: (1, 9), 1: (2, 5, 4, 3), 2: (0, 14)}, order=[1, 2, 0]) == {1: 30}


def test_tied_winners_split_evenly():
    pot = Pot(30, frozenset({0, 1, 2}))

    assert split_pot(pot, {0: (1, 9), 1: (1, 9), 2: (0, 14)}, order=[1, 2, 0]) == {0: 15, 1: 15}


def test_odd_chip_goes_to_first_winner_left_of_the_button():
    pot = Pot(31, frozenset({0, 1, 2}))

    assert split_pot(pot, {0: (1, 9), 1: (0, 14), 2: (1, 9)}, order=[1, 2, 0]) == {2: 16, 0: 15}


def test_pot_with_one_eligible_player_needs_no_ranks():
    assert split_pot(Pot(40, frozenset({2})), {}, order=[0, 1, 2]) == {2: 40}


def test_only_eligible_players_can_win():
    pot = Pot(20, frozenset({1, 2}))

    assert split_pot(pot, {0: (8, 14), 1: (1, 9), 2: (0, 14)}, order=[0, 1, 2]) == {1: 20}
