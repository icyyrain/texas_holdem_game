import heapq
import itertools
import random

import pytest

from holdem import protocol
from holdem.core.cards import Deck
from holdem.core.table import TableConfig
from holdem.server.room import JoinError, Room


class FakeClock:
    """Collects scheduled callbacks so tests decide when time passes."""

    def __init__(self):
        self.now = 0.0
        self.pending = []  # heap of (due, order, callback)
        self._order = itertools.count()

    def time(self):
        return self.now

    def schedule(self, delay, callback):
        heapq.heappush(self.pending, (self.now + delay, next(self._order), callback))

    def step(self):
        """Jump to the next callback and run it; False if nothing is scheduled."""
        if not self.pending:
            return False
        due, _, callback = heapq.heappop(self.pending)
        self.now = max(self.now, due)
        callback()
        return True

    def run(self, limit=10_000):
        """Run everything due now, including what those callbacks schedule with no delay."""
        self.advance(0, limit)

    def advance(self, seconds, limit=10_000):
        """Let ``seconds`` pass, running callbacks as they come due."""
        target = self.now + seconds
        for _ in range(limit):
            if not self.pending or self.pending[0][0] > target:
                self.now = target
                return
            self.step()
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
    room = Room(
        TableConfig(**settings), clock.schedule, password=password, hand_delay=0, deck_factory=seeded_decks(),
        clock=clock.time, reconnect_grace=60,
    )
    return room, clock


def join(room, name, password=None, token=None):
    client = Client()
    member = room.join(client, name, password, token=token)
    return member.player_id, client


def token_of(client):
    return client.of_type("welcome")[0]["token"]


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


def test_disconnecting_mid_hand_keeps_the_seat_until_the_timer_runs_out():
    room, clock, players = seated_room(3)
    clock.run()
    actor = room.table.hand.to_act
    watcher = players[(actor + 1) % 3][1]

    room.disconnect(players[actor][0])

    assert not room.table.hand.players[actor].folded
    seat = watcher.last("table_state")["seats"][actor]
    assert (seat["connected"], seat["away"]) == (False, False)
    clock.advance(29)
    assert room.table.hand.to_act == actor
    clock.advance(1)

    assert room.table.hand.players[actor].folded
    assert watcher.events("action")[-1] == {
        "type": "action", "seat": actor, "action": "fold", "amount": 0, "to": 0, "all_in": False,
    }
    assert watcher.last("table_state")["seats"][actor]["away"]


def test_reconnecting_with_the_token_resumes_the_turn_with_the_time_left():
    room, clock, players = seated_room(3)
    clock.run()
    actor = room.table.hand.to_act
    player_id, old_client = players[actor]
    room.disconnect(player_id)
    clock.advance(10)

    same_id, client = join(room, "ignored", token=token_of(old_client))

    assert same_id == player_id
    welcome, state, turn = client.messages
    assert welcome["type"] == "welcome" and welcome["player_id"] == player_id
    assert state["type"] == "table_state" and state["your_seat"] == actor
    assert state["seats"][actor]["cards"] is not None and state["seats"][actor]["connected"]
    assert state["hand"]["time_left"] == 20
    assert turn["type"] == "your_turn" and turn["time_left"] == 20

    room.handle(player_id, protocol.action("call"))
    assert room.table.hand.to_act != actor


def test_a_rejoin_replaces_a_connection_that_is_still_open():
    room, clock, players = seated_room(2)
    clock.run()
    player_id, old_client = players[0]

    _, client = join(room, "p0", token=token_of(old_client))
    before = len(old_client.messages)
    room.disconnect(player_id, old_client)  # the old connection hangs up late
    play_out_with_folds(room, players)

    assert len(old_client.messages) == before
    assert room.members[player_id].participant is client
    assert client.last("table_state")["seats"][0]["connected"]


def test_unknown_tokens_join_as_someone_new():
    room, _ = make_room()

    player_id, client = join(room, "alice", token="no-such-token")

    assert client.last("welcome")["player_id"] == player_id
    assert client.last("welcome")["token"] != "no-such-token"


def test_a_player_offline_at_the_end_of_a_hand_sits_out_and_comes_back_on_rejoin():
    room, clock, players = seated_room(3)
    clock.run()
    actor = room.table.hand.to_act
    offline = next(seat for seat in range(3) if seat != actor)
    room.disconnect(players[offline][0])

    play_out_with_folds(room, players)
    clock.run()

    assert room.table.seats[offline].away
    assert offline not in room.table.hand.players  # dealt out of the next hand
    join(room, "back", token=token_of(players[offline][1]))
    assert not room.table.seats[offline].away


def test_seat_and_nickname_are_freed_when_the_grace_period_ends():
    room, clock, players = seated_room(2)
    clock.run()
    play_out_with_folds(room, players)

    room.disconnect(players[1][0])
    clock.advance(59)
    assert room.table.seats[1] is not None
    clock.advance(1)

    assert room.table.seats[1] is None
    assert players[1][0] not in room.members
    join(room, "p1")  # the nickname is free again


def test_coming_back_within_the_grace_period_keeps_the_seat_after_it_would_have_expired():
    room, clock, players = seated_room(2)
    clock.run()
    play_out_with_folds(room, players)
    room.disconnect(players[1][0])
    clock.advance(30)

    join(room, "p1", token=token_of(players[1][1]))
    clock.advance(60)

    assert room.table.seats[1] is not None
    assert room.members[players[1][0]].participant is not None


def test_leaving_player_is_folded_when_their_turn_comes():
    room, clock, players = seated_room(3)
    clock.run()
    hand = room.table.hand
    later = hand._seats_after(hand.to_act)[0]

    room.handle(players[later][0], protocol.leave())
    room.handle(players[hand.to_act][0], protocol.action("call"))

    assert hand.players[later].folded


def test_disconnecting_between_hands_sits_the_player_out_and_stops_new_hands():
    room, clock, players = seated_room(2)
    clock.run()
    play_out_with_folds(room, players)

    room.disconnect(players[1][0])
    clock.run()

    assert room.table.seats[1].away
    assert room.hand_number == 1


def test_disconnecting_without_a_seat_leaves_at_once():
    room, _ = make_room()
    player_id, _ = join(room, "alice")

    room.disconnect(player_id)

    assert player_id not in room.members
    join(room, "alice")


def test_disconnecting_after_asking_to_leave_does_not_hold_the_seat():
    room, clock, players = seated_room(3)
    clock.run()
    hand = room.table.hand
    later = hand._seats_after(hand.to_act)[0]
    room.handle(players[later][0], protocol.leave())

    room.disconnect(players[later][0])
    play_out_with_folds(room, players)

    assert room.table.seats[later] is None
    assert players[later][0] not in room.members


def test_running_out_of_time_checks_when_possible_and_marks_the_player_away():
    room, clock, players = seated_room(3)
    clock.run()
    hand = room.table.hand
    while hand.to_act != hand.big_blind_seat:
        room.handle(players[hand.to_act][0], protocol.action("call"))
    slow = hand.to_act  # the big blind, who may check
    assert players[slow][1].last("your_turn")["time_left"] == 30

    clock.advance(30)

    assert players[0][1].events("action")[-1] == {
        "type": "action", "seat": slow, "action": "check", "amount": 0, "to": 2, "all_in": False,
    }
    assert room.table.seats[slow].away
    # From now on the away seat plays itself without waiting for the timer.
    while room.table.hand_in_progress:
        actor = room.table.hand.to_act
        assert actor != slow
        room.handle(players[actor][0], protocol.action("check"))
    clock.run()
    assert room.hand_number == 2
    assert slow not in room.table.hand.players


def test_away_requests_sit_out_and_come_back():
    room, clock, players = seated_room(2)
    clock.run()
    play_out_with_folds(room, players)

    room.handle(players[1][0], protocol.away(True))
    clock.run()
    assert room.hand_number == 1
    assert players[0][1].last("table_state")["seats"][1]["away"]

    room.handle(players[1][0], protocol.away(False))
    clock.run()
    assert room.hand_number == 2


def test_away_needs_a_seat():
    room, _ = make_room()
    player_id, client = join(room, "alice")

    room.handle(player_id, protocol.away(True))

    assert client.last("error")["code"] == "invalid"


def test_acting_in_time_stops_the_timer():
    room, clock, players = seated_room(3)
    clock.run()
    first = room.table.hand.to_act
    clock.advance(20)
    room.handle(players[first][0], protocol.action("call"))
    second = room.table.hand.to_act

    clock.advance(15)  # the first player's timer would have run out by now

    assert not room.table.seats[first].away
    assert room.table.hand.to_act == second
    assert players[second][1].last("table_state")["hand"]["time_left"] == 30
    assert room._time_left() == 15


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
        if room.hand_number > 30 or not clock.step():
            break

    assert room.hand_number > 30
    assert not watcher.of_type("error")
    assert watcher.events("showdown") or watcher.events("award")
    seats = [seat for seat in room.table.seats if seat is not None]
    in_pots = sum(p.total_bet for p in room.table.hand.players.values()) if room.table.hand_in_progress else 0
    assert sum(seat.stack for seat in seats) + in_pots == 3 * 200 + sum(rebuys)
