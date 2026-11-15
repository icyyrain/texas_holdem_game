import pytest

from holdem.core.betting import Action, IllegalActionError, LegalActions, legal_actions, resolve_action


def legal(stack=100, street_bet=0, current_bet=0, min_raise=2, can_raise=True):
    return legal_actions(
        stack=stack, street_bet=street_bet, current_bet=current_bet, min_raise=min_raise, can_raise=can_raise
    )


def test_unopened_pot_allows_check_or_bet_of_at_least_the_big_blind():
    assert legal() == LegalActions(
        frozenset({Action.FOLD, Action.CHECK, Action.BET, Action.ALL_IN}), call_amount=0, min_raise_to=2, max_raise_to=100
    )


def test_facing_a_bet_allows_call_or_raise_by_the_last_raise_size():
    assert legal(street_bet=2, current_bet=10, min_raise=8) == LegalActions(
        frozenset({Action.FOLD, Action.CALL, Action.RAISE, Action.ALL_IN}),
        call_amount=8,
        min_raise_to=18,
        max_raise_to=102,
    )


def test_big_blind_option_is_check_or_raise():
    actions = legal(street_bet=2, current_bet=2, min_raise=2).actions

    assert actions == {Action.FOLD, Action.CHECK, Action.RAISE, Action.ALL_IN}


def test_stack_that_cannot_cover_the_call_can_only_call_all_in():
    assert legal(stack=5, current_bet=10) == LegalActions(
        frozenset({Action.FOLD, Action.CALL, Action.ALL_IN}), call_amount=5
    )


def test_stack_short_of_a_full_raise_may_raise_all_in_only():
    result = legal(stack=15, current_bet=10, min_raise=10)

    assert Action.RAISE in result.actions
    assert (result.min_raise_to, result.max_raise_to) == (15, 15)


def test_player_whose_raise_option_is_closed_can_only_call_or_fold():
    assert legal(street_bet=10, current_bet=13, min_raise=8, can_raise=False) == LegalActions(
        frozenset({Action.FOLD, Action.CALL}), call_amount=3
    )


def resolve(action, amount=None, stack=100, street_bet=2, **state):
    options = legal(stack=stack, street_bet=street_bet, **state)
    return resolve_action(action, amount, options, stack=stack, street_bet=street_bet)


def test_resolve_returns_the_new_street_total():
    assert resolve(Action.CALL, current_bet=10, min_raise=8) == 10
    assert resolve(Action.RAISE, 30, current_bet=10, min_raise=8) == 30
    assert resolve(Action.ALL_IN, current_bet=10, min_raise=8) == 102
    assert resolve(Action.CHECK, current_bet=2) == 2
    assert resolve(Action.FOLD, current_bet=10) is None


@pytest.mark.parametrize("amount", [17, 103, None, 20.0, True])
def test_resolve_rejects_bad_raise_amounts(amount):
    with pytest.raises(IllegalActionError):
        resolve(Action.RAISE, amount, current_bet=10, min_raise=8)


def test_resolve_rejects_actions_that_are_not_legal():
    with pytest.raises(IllegalActionError):
        resolve(Action.CHECK, current_bet=10, min_raise=8)
    with pytest.raises(IllegalActionError):
        resolve(Action.BET, 10, current_bet=10, min_raise=8)
