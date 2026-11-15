import pytest

from holdem.core.betting import Action, IllegalActionError
from holdem.core.cards import Card, Deck
from holdem.core.evaluator import HandCategory
from holdem.core.table import Street, Table, TableConfig

FOLD, CHECK, CALL, BET, RAISE, ALL_IN = Action


def table_with(stacks):
    table = Table(TableConfig(small_blind=1, big_blind=2, min_buy_in=1, max_buy_in=1000))
    for seat, stack in enumerate(stacks):
        table.sit(f"p{seat}", seat, stack)
    return table


def rigged_deck(deal_order, holes, board=""):
    """A deck dealing ``holes[seat]`` one card per round in ``deal_order``, then ``board``."""
    split = {seat: [Card.from_code(code) for code in holes[seat].split()] for seat in deal_order}
    sequence = [split[seat][0] for seat in deal_order] + [split[seat][1] for seat in deal_order]
    sequence += [Card.from_code(code) for code in board.split()]
    rest = [card for card in Deck.standard().cards if card not in sequence]
    return Deck(rest + sequence[::-1])


def start(stacks, holes=None, board=""):
    """First hand at a fresh table: the button is seat 0, so dealing starts at seat 1 (seat 0 heads-up)."""
    table = table_with(stacks)
    deal_order = [0, 1] if len(stacks) == 2 else [*range(1, len(stacks)), 0]
    deck = rigged_deck(deal_order, holes, board) if holes else None
    return table, table.start_hand(deck)


def act(hand, *steps):
    for seat, action, *amount in steps:
        hand.apply(seat, action, *amount)


def stacks(table):
    return [seat.stack for seat in table.seats if seat is not None]


def events(hand, kind):
    return [event for event in hand.events if event["type"] == kind]


def test_three_handed_blinds_and_first_to_act():
    table, hand = start([100, 100, 100])

    assert (hand.button, hand.small_blind_seat, hand.big_blind_seat) == (0, 1, 2)
    assert stacks(table) == [100, 99, 98]
    assert hand.to_act == 0
    legal = hand.legal_actions(0)
    assert legal.actions == {FOLD, CALL, RAISE, ALL_IN}
    assert (legal.call_amount, legal.min_raise_to, legal.max_raise_to) == (2, 4, 100)


def test_only_the_player_to_act_has_legal_actions():
    _, hand = start([100, 100, 100])

    assert not hand.legal_actions(1).actions
    with pytest.raises(IllegalActionError):
        hand.apply(1, CALL)


def test_heads_up_button_posts_small_blind_acts_first_preflop_and_last_after():
    _, hand = start([100, 100])

    assert (hand.small_blind_seat, hand.big_blind_seat) == (0, 1)
    assert hand.to_act == 0
    act(hand, (0, CALL), (1, CHECK))
    assert hand.street == Street.FLOP
    assert hand.to_act == 1


def test_hole_cards_are_dealt_two_each_and_logged_per_seat():
    _, hand = start([100, 100, 100])

    dealt = events(hand, "hole_cards")
    assert [event["seat"] for event in dealt] == [1, 2, 0]
    for event in dealt:
        assert event["cards"] == [card.code for card in hand.players[event["seat"]].hole]
    assert len({code for event in dealt for code in event["cards"]}) == 6


def test_everyone_folds_to_the_big_blind():
    table, hand = start([100, 100, 100])

    act(hand, (0, FOLD), (1, FOLD))

    assert hand.is_complete()
    assert stacks(table) == [100, 99, 101]
    assert events(hand, "uncalled_returned") == [{"type": "uncalled_returned", "seat": 2, "amount": 1}]
    assert not events(hand, "showdown")
    assert hand.results().net == {0: 0, 1: -1, 2: 1}


def test_big_blind_gets_the_option_after_limps():
    _, hand = start([100, 100, 100])

    act(hand, (0, CALL), (1, CALL))

    assert hand.to_act == 2
    assert {CHECK, RAISE} <= hand.legal_actions(2).actions
    act(hand, (2, CHECK))
    assert hand.street == Street.FLOP
    assert len(hand.board) == 3
    assert hand.to_act == 1


def test_postflop_action_starts_left_of_the_button_and_skips_folded_players():
    _, hand = start([100, 100, 100])

    act(hand, (0, CALL), (1, FOLD), (2, CHECK))

    assert hand.to_act == 2


def test_postflop_minimum_bet_is_the_big_blind():
    _, hand = start([100, 100])
    act(hand, (0, CALL), (1, CHECK))

    legal = hand.legal_actions(1)
    assert BET in legal.actions
    assert legal.min_raise_to == 2
    with pytest.raises(IllegalActionError):
        hand.apply(1, BET, 1)


def test_minimum_raise_follows_the_last_full_raise():
    _, hand = start([200, 200, 200, 200])

    act(hand, (3, RAISE, 6))
    assert hand.legal_actions(0).min_raise_to == 10
    with pytest.raises(IllegalActionError):
        hand.apply(0, RAISE, 9)

    act(hand, (0, RAISE, 20))
    assert hand.legal_actions(1).min_raise_to == 34


def test_illegal_actions_leave_the_hand_unchanged():
    table, hand = start([100, 100, 100])
    before = (stacks(table), hand.to_act, len(hand.events))

    for action, amount in [(CHECK, None), (BET, 10), (RAISE, 3), (RAISE, 101), (RAISE, True), ("dance", None)]:
        with pytest.raises(IllegalActionError):
            hand.apply(0, action, amount)

    assert (stacks(table), hand.to_act, len(hand.events)) == before


def test_incomplete_all_in_raise_does_not_reopen_raising():
    _, hand = start([100, 13, 100])

    act(hand, (0, RAISE, 10), (1, ALL_IN))
    assert hand.current_bet == 13
    assert events(hand, "action")[-1] == {
        "type": "action", "seat": 1, "action": "raise", "amount": 12, "to": 13, "all_in": True,
    }

    # The big blind has not acted yet, so it may still raise.
    assert hand.legal_actions(2).min_raise_to == 21
    act(hand, (2, CALL))

    legal = hand.legal_actions(0)
    assert legal.actions == {FOLD, CALL}
    assert legal.call_amount == 3


def test_incomplete_raises_adding_up_to_a_full_raise_reopen_raising():
    _, hand = start([18, 100, 100, 100, 14])

    act(hand, (3, RAISE, 10), (4, ALL_IN), (0, ALL_IN), (1, FOLD), (2, FOLD))

    legal = hand.legal_actions(3)
    assert RAISE in legal.actions
    assert legal.min_raise_to == 26


def test_full_hand_to_showdown():
    holes = {0: "AS AD", 1: "KS KD", 2: "7C 2D"}
    table, hand = start([100, 100, 100], holes, board="AH 9C 5S 3D JH")

    act(hand, (0, CALL), (1, CALL), (2, CHECK))
    assert [card.code for card in hand.board] == ["AH", "9C", "5S"]
    act(hand, (1, CHECK), (2, CHECK), (0, BET, 4), (1, CALL), (2, FOLD))
    assert hand.street == Street.TURN
    act(hand, (1, CHECK), (0, CHECK))
    assert hand.street == Street.RIVER
    act(hand, (1, CHECK), (0, CHECK))

    assert hand.is_complete()
    assert hand.street == Street.SHOWDOWN
    assert stacks(table) == [108, 94, 98]
    assert [event["seat"] for event in events(hand, "showdown")] == [1, 0]
    assert hand.results().showdown[0][0][0] == HandCategory.THREE_OF_A_KIND
    assert events(hand, "award") == [{"type": "award", "seat": 0, "amount": 14, "pot": 0}]


def test_all_ins_for_different_amounts_build_side_pots():
    holes = {0: "AS AD", 1: "KS KD", 2: "7C 2D"}
    table, hand = start([50, 100, 200], holes, board="9C 8D 4S 3H 2S")

    act(hand, (0, ALL_IN), (1, ALL_IN), (2, CALL))

    assert hand.is_complete()
    assert [pot.amount for pot, _ in hand.results().pots] == [150, 100]
    assert stacks(table) == [150, 100, 100]


def test_uncalled_bet_is_returned():
    table, hand = start([100, 100])

    act(hand, (0, CALL), (1, CHECK), (1, BET, 10), (0, FOLD))

    assert stacks(table) == [98, 102]
    assert events(hand, "uncalled_returned") == [{"type": "uncalled_returned", "seat": 1, "amount": 10}]


def test_all_in_and_call_runs_out_the_board():
    _, hand = start([100, 100])

    act(hand, (0, ALL_IN), (1, CALL))

    assert hand.is_complete()
    assert len(hand.board) == 5
    assert [event["street"] for event in events(hand, "street")] == ["flop", "turn", "river"]
    assert len(events(hand, "showdown")) == 2


def test_short_all_in_big_blind_needs_no_action():
    _, hand = start([10, 1])

    assert hand.is_complete()
    assert hand.to_act is None
    assert len(hand.board) == 5


def test_split_pot_odd_chip_goes_to_first_winner_left_of_button():
    holes = {0: "2C 3C", 1: "2D 3D", 2: "2H 3H"}
    table, hand = start([100, 100, 100], holes, board="AS KS QS JS TS")

    act(hand, (0, CALL), (1, FOLD), (2, CHECK))
    for _ in range(3):
        act(hand, (2, CHECK), (0, CHECK))

    assert stacks(table) == [100, 99, 101]


def test_no_actions_after_the_hand_is_complete():
    _, hand = start([100, 100, 100])
    act(hand, (0, FOLD), (1, FOLD))

    with pytest.raises(IllegalActionError):
        hand.apply(2, CHECK)


def test_results_are_unavailable_until_the_hand_ends():
    _, hand = start([100, 100])

    with pytest.raises(ValueError):
        hand.results()
