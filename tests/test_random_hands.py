import random

import pytest

from holdem.core.betting import Action
from holdem.core.cards import Deck
from holdem.core.table import Street, Table, TableConfig


def seeded_decks(rng):
    def deck():
        cards = Deck.standard()
        cards.shuffle(rng)
        return cards

    return deck


def random_action(hand, rng):
    legal = hand.legal_actions(hand.to_act)
    assert legal.actions, "the player to act must have something to do"
    action = rng.choice(sorted(legal.actions))
    amount = None
    if action in (Action.BET, Action.RAISE):
        amount = rng.choice([legal.min_raise_to, legal.max_raise_to, rng.randint(legal.min_raise_to, legal.max_raise_to)])
    return action, amount


def chips_on_table(table):
    in_front = sum(seat.stack for seat in table.seats if seat is not None)
    in_pots = sum(player.total_bet for player in table.hand.players.values()) if table.hand_in_progress else 0
    return in_front + in_pots


@pytest.mark.parametrize("seed", range(20))
def test_random_hands_keep_chips_constant_and_always_finish(seed):
    rng = random.Random(seed)
    players = rng.randint(2, 9)
    config = TableConfig(small_blind=1, big_blind=2, min_buy_in=1, max_buy_in=400)
    table = Table(config, deck_factory=seeded_decks(rng))
    for seat in range(players):
        # Mixed stack sizes so short all-ins and side pots come up often.
        table.sit(f"p{seat}", seat, rng.choice([1, 3, 7, 20, 60, 200, 400]))
    total = chips_on_table(table)

    for _ in range(100):
        for seat in table.seats:
            if seat is not None and seat.stack == 0:
                table.rebuy(seat.player_id, rng.choice([1, 5, 50, 200]))
                total = chips_on_table(table)
        hand = table.start_hand()

        for _ in range(200):
            if hand.is_complete():
                break
            hand.apply(hand.to_act, *random_action(hand, rng))
            assert chips_on_table(table) == total
            assert all(seat.stack >= 0 for seat in table.seats if seat is not None)
        assert hand.is_complete()

        result = hand.results()
        assert sum(result.net.values()) == 0
        live = {seat for seat, player in hand.players.items() if not player.folded}
        for pot, shares in result.pots:
            assert sum(shares.values()) == pot.amount
            assert set(shares) <= pot.eligible <= live
        if result.showdown:
            # Every live player is in the main pot, so the best hand always wins some of it.
            best = max(rank for rank, _ in result.showdown.values())
            main_shares = result.pots[0][1]
            assert all(result.showdown[seat][0] == best for seat in main_shares)
        assert len(hand.board) == 5 or hand.street != Street.SHOWDOWN
        assert chips_on_table(table) == total
