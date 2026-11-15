import random

import pytest

from holdem import strategy
from holdem.core.betting import Action, legal_actions
from holdem.core.cards import Card, Deck
from holdem.core.table import Street, Table, TableConfig
from holdem.strategy import (
    HAND_PERCENTILE,
    Situation,
    chen_score,
    estimate_equity,
    situation_for,
    smart_action,
)


def cards(*codes):
    return tuple(Card.from_code(code) for code in codes)


@pytest.mark.parametrize(
    "hole, score",
    [
        (("AS", "AD"), 20), (("KH", "KC"), 16), (("2S", "2D"), 5), (("AS", "KS"), 12), (("AS", "KD"), 10),
        (("TS", "9S"), 8), (("JS", "TS"), 9), (("QS", "JD"), 7), (("7S", "2D"), -1), (("AH", "5D"), 5),
    ],
)
def test_chen_scores(hole, score):
    assert chen_score(*cards(*hole)) == score


def test_starting_hand_percentiles_cover_every_class_in_score_order():
    assert len(HAND_PERCENTILE) == 169
    assert HAND_PERCENTILE[(14, 14, False)] == pytest.approx(6 / 1326)
    assert HAND_PERCENTILE[(7, 2, False)] == 1.0
    assert HAND_PERCENTILE[(14, 13, True)] < HAND_PERCENTILE[(14, 13, False)] < HAND_PERCENTILE[(9, 8, False)]


def test_aces_win_about_85_percent_against_a_random_hand():
    equity = estimate_equity(cards("AS", "AD"), (), [1.0], random.Random(1), 3000)
    assert equity == pytest.approx(0.85, abs=0.03)


def test_the_nuts_always_win_and_a_board_everyone_plays_splits():
    rng = random.Random(2)
    assert estimate_equity(cards("AS", "KS"), cards("QS", "JS", "TS"), [1.0, 1.0], rng, 300) == 1.0
    royal_board = cards("AH", "KH", "QH", "JH", "TH")
    assert estimate_equity(cards("2C", "3D"), royal_board, [1.0], rng, 50) == 0.5
    assert estimate_equity(cards("2C", "3D"), royal_board, [1.0, 1.0], rng, 50) == pytest.approx(1 / 3)
    assert estimate_equity(cards("2C", "3D"), (), [], rng, 50) == 1.0


def test_strong_ranges_lower_the_equity():
    hole = cards("KS", "QD")
    against_anything = estimate_equity(hole, (), [1.0], random.Random(3), 3000)
    against_strong = estimate_equity(hole, (), [0.1], random.Random(3), 3000)
    assert against_strong < against_anything - 0.1


def test_range_dealing_mostly_stays_in_the_range():
    rng = random.Random(4)
    dealt = []
    for _ in range(500):
        deck = list(strategy.FULL_DECK)
        rng.shuffle(deck)
        dealt.append(strategy._deal_from_range(deck, strategy.AGGRESSIVE_RANGE, rng))
        assert len(deck) == 50
    inside = sum(HAND_PERCENTILE[strategy._hand_class(*pair)] <= strategy.AGGRESSIVE_RANGE for pair in dealt)
    assert inside >= 495


def test_aggression_counts_bets_raises_and_raising_all_ins_but_not_blinds():
    events = [
        {"type": "blind", "seat": 0, "blind": "small", "amount": 1, "all_in": False},
        {"type": "blind", "seat": 1, "blind": "big", "amount": 2, "all_in": False},
        {"type": "action", "seat": 2, "action": "call", "amount": 2, "to": 2, "all_in": False},
        {"type": "action", "seat": 0, "action": "all_in", "amount": 5, "to": 6, "all_in": True},
        {"type": "action", "seat": 1, "action": "call", "amount": 4, "to": 6, "all_in": False},
        {"type": "street", "street": "flop", "cards": ["AS", "KS", "QS"]},
        {"type": "action", "seat": 1, "action": "bet", "amount": 4, "to": 4, "all_in": False},
        {"type": "action", "seat": 2, "action": "call", "amount": 4, "to": 4, "all_in": False},
    ]
    assert strategy._aggressive_seats(events) == {0, 1}


def test_situation_shows_public_facts_and_only_your_own_cards():
    table = Table(TableConfig(1, 2, 40, 200, max_seats=3))
    for seat in range(3):
        table.sit(f"p{seat}", seat, 100)
    hand = table.start_hand()
    first = hand.to_act

    situation = situation_for(hand, first)
    assert situation.hole == tuple(hand.players[first].hole)
    assert (situation.pot, situation.to_call, situation.opponents, situation.aggressors) == (3, 2, 2, 0)
    assert situation.street is Street.PREFLOP and situation.board == ()

    hand.apply(first, Action.RAISE, 8)
    after = situation_for(hand, hand.to_act)
    assert (after.pot, after.current_bet, after.aggressors) == (11, 8, 1)
    assert after.to_call == 8 - hand.players[hand.to_act].street_bet


def make_situation(hole, board=(), *, pot=3, current_bet=2, street_bet=0, stack=200, opponents=1, aggressors=0):
    legal = legal_actions(stack=stack, street_bet=street_bet, current_bet=current_bet, min_raise=2, can_raise=True)
    street = {0: Street.PREFLOP, 3: Street.FLOP, 4: Street.TURN, 5: Street.RIVER}[len(board)]
    return Situation(
        legal=legal, hole=cards(*hole), board=cards(*board), street=street, pot=pot,
        to_call=max(current_bet - street_bet, 0), street_bet=street_bet, stack=stack, big_blind=2,
        current_bet=current_bet, opponents=opponents, aggressors=aggressors,
    )


def test_preflop_premium_hands_raise_and_trash_folds_or_checks():
    rng = random.Random(5)
    assert smart_action(make_situation(("AS", "AD")), rng) == (Action.RAISE, 6)
    assert smart_action(make_situation(("7S", "2D"), current_bet=8, pot=11), rng) == (Action.FOLD, None)
    big_blind_option = make_situation(("7S", "2D"), street_bet=2, current_bet=2, pot=4)
    assert smart_action(big_blind_option, rng) == (Action.CHECK, None)
    assert smart_action(make_situation(("KS", "KD"), current_bet=8, pot=11), rng) == (Action.RAISE, 24)


def test_short_stacks_shove_good_hands():
    situation = make_situation(("AS", "KD"), stack=20)
    assert smart_action(situation, random.Random(6)) == (Action.ALL_IN, None)


def test_postflop_never_folds_the_nuts_and_folds_hopeless_hands_to_a_big_bet():
    rng = random.Random(7)
    for _ in range(20):
        action, amount = smart_action(make_situation(("AS", "KS"), ("QS", "JS", "TS"), pot=40, current_bet=20), rng)
        assert action in (Action.RAISE, Action.CALL, Action.ALL_IN)
    hopeless = make_situation(("7C", "2D"), ("AS", "KH", "QD", "JC", "9S"), pot=60, current_bet=30, aggressors=1)
    assert smart_action(hopeless, rng) == (Action.FOLD, None)


def test_smart_bots_only_make_legal_moves_and_keep_the_chips(monkeypatch):
    monkeypatch.setattr(strategy, "MAX_EVALUATIONS", 60)
    monkeypatch.setattr(strategy, "MIN_TRIALS", 20)
    rng = random.Random(8)

    def deck():
        shuffled = Deck.standard()
        shuffled.shuffle(rng)
        return shuffled

    table = Table(TableConfig(1, 2, 20, 200, max_seats=6), deck_factory=deck)
    for seat in range(6):
        table.sit(f"p{seat}", seat, 60 + 25 * seat)
    total = sum(seat.stack for seat in table.seats)
    for _ in range(40):
        if sum(seat.stack > 0 for seat in table.seats if seat) < 2:
            break
        hand = table.start_hand()
        while not hand.is_complete():
            seat = hand.to_act
            action, amount = smart_action(situation_for(hand, seat), rng)
            hand.apply(seat, action, amount)  # raises on an illegal move
        table.finish_hand()
        assert sum(seat.stack for seat in table.seats if seat) == total
