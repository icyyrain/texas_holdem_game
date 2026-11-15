import random

import pytest

from holdem import protocol
from holdem.core.cards import Deck
from holdem.core.table import TableConfig
from holdem.server.room import JoinError, Room


class FakeClock:
    """Collects scheduled callbacks so tests decide when time passes."""

    def __init__(self):
        self.pending = []

    def schedule(self, delay, callback):
        self.pending.append((delay, callback))

    def run(self, limit=10_000):
        for _ in range(limit):
            if not self.pending:
                return
            _, callback = self.pending.pop(0)
            callback()
        raise AssertionError("callbacks kept scheduling more callbacks")


class Client:
    def __init__(self):
        self.messages = []

    def deliver(self, message):
        # Everything a room sends must survive the wire.
        self.messages.append(protocol.decode_server(protocol.encode(message)))

    def of_type(self, kind):
        return [message for message in self.messages if message["type"] == kind]

    def last(self, kind):
        return self.of_type(kind)[-1]

    def events(self, kind=None):
        return [m["event"] for m in self.of_type("event") if kind is None or m["event"]["type"] == kind]


def seeded_decks(seed=0):
    rng = random.Random(seed)

    def deck():
        cards = Deck.standard()
        cards.shuffle(rng)
        return cards

    return deck


def make_room(password=None, **config):
    clock = FakeClock()
    settings = {"small_blind": 1, "big_blind": 2, "min_buy_in": 40, "max_buy_in": 200, "max_seats": 6, **config}
    room = Room(TableConfig(**settings), clock.schedule, password=password, hand_delay=0, deck_factory=seeded_decks())
    return room, clock


def join(room, name, password=None):
    client = Client()
    member = room.join(client, name, password)
    return member.player_id, client


def seated_room(count=2):
    room, clock = make_room()
    players = []
    for seat in range(count):
        player_id, client = join(room, f"p{seat}")
        room.handle(player_id, protocol.sit(seat, 100))
        players.append((player_id, client))
    return room, clock, players


def test_join_sends_welcome_then_state():
    room, _ = make_room()
    player_id, client = join(room, "alice")

    welcome, state = client.messages
    assert welcome["type"] == "welcome"
    assert welcome["player_id"] == player_id
    assert welcome["config"]["big_blind"] == 2
    assert state["type"] == "table_state"
    assert state["your_seat"] is None
    assert state["hand"] is None


@pytest.mark.parametrize(
    "password, name, version, code",
    [
        ("nope", "bob", protocol.PROTOCOL_VERSION, "bad_password"),
        (None, "bob", protocol.PROTOCOL_VERSION, "bad_password"),
        ("secret", "alice", protocol.PROTOCOL_VERSION, "nickname_taken"),
        ("secret", "bob", protocol.PROTOCOL_VERSION + 1, "version_mismatch"),
    ],
)
def test_join_refusals(password, name, version, code):
    room, _ = make_room(password="secret")
    join(room, "alice", "secret")

    with pytest.raises(JoinError) as error:
        room.join(Client(), name, password, version)
    assert error.value.code == code


def test_sitting_is_broadcast_to_everyone():
    room, _ = make_room()
    alice, alice_client = join(room, "alice")
    _, bob_client = join(room, "bob")

    room.handle(alice, protocol.sit(2, 100))

    seat = bob_client.last("table_state")["seats"][2]
    assert (seat["name"], seat["stack"]) == ("alice", 100)
    assert alice_client.last("table_state")["your_seat"] == 2


def test_refused_requests_go_back_to_the_sender_only():
    room, _ = make_room()
    alice, alice_client = join(room, "alice")
    _, bob_client = join(room, "bob")
    before = len(bob_client.messages)

    room.handle(alice, protocol.sit(2, 5000))
    room.handle(alice, protocol.leave())
    room.handle(alice, protocol.action("fold"))

    assert [m["code"] for m in alice_client.of_type("error")] == ["invalid", "invalid", "illegal_action"]
    assert len(bob_client.messages) == before


def test_hand_starts_once_two_players_sit():
    room, clock, players = seated_room(2)

    assert room.hand_number == 0
    clock.run()

    assert room.table.hand_in_progress
    state = players[0][1].last("table_state")
    assert state["hand"]["number"] == 1
    assert state["hand"]["street"] == "preflop"


def test_each_player_sees_only_their_own_hole_cards():
    room, clock, players = seated_room(3)
    clock.run()

    for seat, (_, client) in enumerate(players):
        state = client.last("table_state")
        visible = [entry["seat"] for entry in state["seats"] if entry and entry["cards"] is not None]
        assert visible == [seat]
        dealt = {event["seat"]: event.get("cards") for event in client.events("hole_cards")}
        assert dealt[seat] == state["seats"][seat]["cards"]
        assert all(cards is None for other, cards in dealt.items() if other != seat)


def test_only_the_player_to_act_is_prompted():
    room, clock, players = seated_room(3)
    clock.run()

    to_act = room.table.hand.to_act
    for seat, (_, client) in enumerate(players):
        turns = client.of_type("your_turn")
        assert len(turns) == (1 if seat == to_act else 0)
    turn = players[to_act][1].last("your_turn")
    assert set(turn["actions"]) == {"fold", "call", "raise", "all_in"}
    assert (turn["call_amount"], turn["min_raise_to"]) == (2, 4)


def test_acting_out_of_turn_is_refused_and_changes_nothing():
    room, clock, players = seated_room(3)
    clock.run()
    hand = room.table.hand
    waiting = next(seat for seat in range(3) if seat != hand.to_act)
    events_before = len(hand.events)

    room.handle(players[waiting][0], protocol.action("fold"))

    assert players[waiting][1].last("error")["code"] == "illegal_action"
    assert len(hand.events) == events_before


def test_actions_are_broadcast_as_events():
    room, clock, players = seated_room(3)
    clock.run()
    actor = room.table.hand.to_act

    room.handle(players[actor][0], protocol.action("call"))

    for _, client in players:
        last_action = client.events("action")[-1]
        assert (last_action["seat"], last_action["action"]) == (actor, "call")


def play_out_with_folds(room, players):
    while room.table.hand_in_progress:
        actor = room.table.hand.to_act
        room.handle(players[actor][0], protocol.action("fold"))


def test_next_hand_starts_after_one_ends():
    room, clock, players = seated_room(2)
    clock.run()

    play_out_with_folds(room, players)
    assert not room.table.hand_in_progress
    clock.run()

    assert room.hand_number == 2
    assert room.table.hand_in_progress


def test_showdown_cards_become_visible_to_everyone():
    room, clock, players = seated_room(2)
    clock.run()
    while room.table.hand_in_progress:
        actor = room.table.hand.to_act
        legal = room.table.hand.legal_actions(actor).actions
        room.handle(players[actor][0], protocol.action("check" if "check" in legal else "call"))

    state = players[0][1].last("table_state")
    assert state["hand"]["complete"]
    assert all(entry["cards"] is not None for entry in state["seats"] if entry)


def test_disconnecting_mid_hand_folds_for_the_player_and_frees_the_seat():
    room, clock, players = seated_room(3)
    clock.run()
    actor = room.table.hand.to_act

    room.disconnect(players[actor][0])

    assert room.table.hand.players[actor].folded
    remaining = [client for seat, (_, client) in enumerate(players) if seat != actor]
    assert remaining[0].events("action")[-1] == {
        "type": "action", "seat": actor, "action": "fold", "amount": 0, "to": 0, "all_in": False,
    }
    play_out_with_folds(room, [p if s != actor else None for s, p in enumerate(players)])
    assert remaining[0].last("table_state")["seats"][actor] is None


def test_leaving_player_is_folded_when_their_turn_comes():
    room, clock, players = seated_room(3)
    clock.run()
    hand = room.table.hand
    later = hand._seats_after(hand.to_act)[0]

    room.handle(players[later][0], protocol.leave())
    room.handle(players[hand.to_act][0], protocol.action("call"))

    assert hand.players[later].folded


def test_disconnecting_between_hands_frees_the_seat_and_stops_new_hands():
    room, clock, players = seated_room(2)
    clock.run()
    play_out_with_folds(room, players)

    room.disconnect(players[1][0])
    clock.run()

    assert room.table.seats[1] is None
    assert room.hand_number == 1


def test_rebuy_is_refused_during_a_hand():
    room, clock, players = seated_room(2)
    clock.run()

    room.handle(players[0][0], protocol.rebuy(10))

    assert players[0][1].last("error")["code"] == "invalid"


def test_bots_play_through_the_same_interface_and_keep_chips():
    room, clock = make_room()
    rebuys = []
    table_rebuy = room.table.rebuy
    room.table.rebuy = lambda player_id, amount: (table_rebuy(player_id, amount), rebuys.append(amount))
    for name in ("bot1", "bot2", "bot3"):
        room.add_bot(name, rng=random.Random(name), think=0)
    _, watcher = join(room, "watcher")

    for _ in range(5000):
        if not clock.pending or room.hand_number > 30:
            break
        _, callback = clock.pending.pop(0)
        callback()

    assert room.hand_number > 30
    assert not watcher.of_type("error")
    assert watcher.events("showdown") or watcher.events("award")
    seats = [seat for seat in room.table.seats if seat is not None]
    in_pots = sum(p.total_bet for p in room.table.hand.players.values()) if room.table.hand_in_progress else 0
    assert sum(seat.stack for seat in seats) + in_pots == 3 * 200 + sum(rebuys)
