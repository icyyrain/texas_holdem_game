import pytest

from helpers import Client, FakeClock, seeded_decks
from holdem import protocol
from holdem.bots import random_action
from holdem.server.lobby import Lobby
from holdem.server.room import JoinError


def make_lobby(**options):
    clock = FakeClock()
    settings = {
        # Bots that think for a second let the fake clock move on between their moves.
        "hand_delay": 0, "bot_think": 1, "bot_policy": random_action, "reconnect_grace": 60,
        "empty_room_timeout": 600, "deck_factory": seeded_decks(), **options,
    }
    return Lobby(clock.schedule, clock=clock.time, **settings), clock


def connect(lobby, name, token=None, password=None):
    client = Client()
    session = lobby.join(client, name, password, token=token)
    return session, client


def create(lobby, session, name="Friday", password=None, bots=0, blinds=(1, 2)):
    lobby.handle(session.player_id, protocol.create_room(name, *blinds, password=password, bots=bots))
    return session.room


def test_hello_lands_in_the_lobby_with_the_room_list():
    lobby, _ = make_lobby()

    session, client = connect(lobby, "alice")

    welcome, rooms = client.messages
    assert (welcome["type"], welcome["player_id"], welcome["token"]) == ("welcome", session.player_id, session.token)
    assert rooms == protocol.rooms([])


@pytest.mark.parametrize("name", ["alice", "Bot 1"])
def test_nicknames_are_unique_and_bot_names_are_reserved(name):
    lobby, _ = make_lobby()
    connect(lobby, "alice")

    with pytest.raises(JoinError) as error:
        connect(lobby, name)
    assert error.value.code == "nickname_taken"


def test_a_server_password_guards_the_lobby():
    lobby, _ = make_lobby(password="secret")

    with pytest.raises(JoinError) as error:
        connect(lobby, "alice", password="nope")
    assert error.value.code == "bad_password"
    connect(lobby, "alice", password="secret")


def test_creating_a_room_enters_it_and_lists_it_for_everyone_else():
    lobby, _ = make_lobby()
    alice, alice_client = connect(lobby, "alice")
    bob, bob_client = connect(lobby, "bob")

    room = create(lobby, alice, "周五局", blinds=(5, 10))

    joined = alice_client.last("room_joined")
    assert (joined["name"], joined["config"]["big_blind"], joined["config"]["max_buy_in"]) == ("周五局", 10, 1000)
    assert alice_client.last("table_state")["your_seat"] is None
    assert bob_client.last("rooms")["rooms"] == [{
        "id": room.room_id, "name": "周五局", "small_blind": 5, "big_blind": 10, "players": 0, "seats": 9,
        "locked": False,
    }]


def test_the_list_follows_seats_taken_inside_a_room():
    lobby, _ = make_lobby()
    alice, _ = connect(lobby, "alice")
    _, bob_client = connect(lobby, "bob")
    create(lobby, alice, bots=2)
    assert bob_client.last("rooms")["rooms"][0]["players"] == 2

    lobby.handle(alice.player_id, protocol.sit(4, 100))

    assert bob_client.last("rooms")["rooms"][0]["players"] == 3


def test_joining_a_locked_room_needs_its_password():
    lobby, _ = make_lobby()
    alice, _ = connect(lobby, "alice")
    bob, bob_client = connect(lobby, "bob")
    room = create(lobby, alice, password="pw")
    assert bob_client.last("rooms")["rooms"][0]["locked"]

    lobby.handle(bob.player_id, protocol.join_room(room.room_id, "wrong"))
    assert bob_client.last("error")["code"] == "bad_password"
    assert bob.room is None

    lobby.handle(bob.player_id, protocol.join_room(room.room_id, "pw"))
    assert bob.room is room
    assert bob_client.last("room_joined")["room"] == room.room_id


def test_table_messages_need_a_room_and_reach_it():
    lobby, clock = make_lobby()
    alice, alice_client = connect(lobby, "alice")
    lobby.handle(alice.player_id, protocol.sit(0, 100))
    assert alice_client.last("error")["code"] == "invalid"

    create(lobby, alice, bots=1)
    lobby.handle(alice.player_id, protocol.sit(5, 100))
    clock.run()

    assert alice_client.last("table_state")["your_seat"] == 5
    assert alice.room.hand_number == 1


def test_room_limit_and_settings_are_checked():
    lobby, _ = make_lobby(max_rooms=1, max_bots=2)
    alice, alice_client = connect(lobby, "alice")
    bob, bob_client = connect(lobby, "bob")

    create(lobby, bob, bots=3)
    assert bob_client.last("error")["code"] == "invalid"
    create(lobby, bob, blinds=(2, 1))
    assert bob_client.last("error")["code"] == "invalid"
    assert lobby.rooms == {}

    create(lobby, alice)
    create(lobby, bob)
    assert bob_client.last("error")["code"] == "too_many_rooms"
    lobby.handle(alice.player_id, protocol.create_room("again", 1, 2))
    assert alice_client.last("error")["code"] == "invalid"


def test_unknown_rooms_are_reported():
    lobby, _ = make_lobby()
    alice, client = connect(lobby, "alice")

    lobby.handle(alice.player_id, protocol.join_room("NOPE23"))

    assert client.last("error")["code"] == "room_not_found"


def test_leaving_a_room_goes_back_to_the_lobby_and_gives_up_the_seat():
    lobby, _ = make_lobby()
    alice, client = connect(lobby, "alice")
    bob, _ = connect(lobby, "bob")
    room = create(lobby, alice)
    lobby.handle(bob.player_id, protocol.join_room(room.room_id))
    lobby.handle(alice.player_id, protocol.sit(0, 100))

    lobby.handle(alice.player_id, protocol.leave_room())

    assert alice.room is None
    left = client.messages.index(protocol.room_left())
    assert {m["type"] for m in client.messages[left + 1:]} == {"rooms"}
    assert client.last("rooms") == protocol.rooms(lobby._room_list())
    assert room.table.seats[0] is None


def test_once_at_a_table_you_get_no_room_lists():
    # A client takes a room list at the table to mean its room closed.
    lobby, _ = make_lobby()
    alice, alice_client = connect(lobby, "alice")
    bob, bob_client = connect(lobby, "bob")

    room = create(lobby, alice, bots=2)
    lobby.handle(bob.player_id, protocol.join_room(room.room_id))
    lobby.handle(bob.player_id, protocol.sit(5, 100))

    for client in (alice_client, bob_client):
        joined = next(i for i, m in enumerate(client.messages) if m["type"] == "room_joined")
        assert "rooms" not in [m["type"] for m in client.messages[joined:]]


def test_a_room_closes_after_ten_empty_minutes_unless_someone_comes_back():
    lobby, clock = make_lobby()
    alice, _ = connect(lobby, "alice")
    _, watcher = connect(lobby, "watcher")
    room = create(lobby, alice, bots=2)
    lobby.handle(alice.player_id, protocol.leave_room())

    clock.advance(300)
    lobby.handle(alice.player_id, protocol.join_room(room.room_id))
    lobby.handle(alice.player_id, protocol.leave_room())
    clock.advance(599)
    assert room.room_id in lobby.rooms
    clock.advance(1)

    assert lobby.rooms == {}
    assert room.closed
    assert watcher.last("rooms")["rooms"] == []
    hands = room.hand_number
    clock.advance(600)
    assert room.hand_number == hands  # its bots stopped playing


def test_reconnecting_puts_you_back_in_your_room_and_seat():
    lobby, clock = make_lobby()
    alice, old = connect(lobby, "alice")
    room = create(lobby, alice, bots=1)
    lobby.handle(alice.player_id, protocol.sit(3, 100))
    clock.run()

    lobby.disconnect(alice.player_id)
    clock.advance(10)
    again, client = connect(lobby, "whoever", token=alice.token)

    assert again is alice
    assert [m["type"] for m in client.messages[:3]] == ["welcome", "room_joined", "table_state"]
    assert client.last("table_state")["your_seat"] == 3
    assert alice.room is room


def test_reconnecting_to_a_closed_room_lands_in_the_lobby():
    lobby, clock = make_lobby(reconnect_grace=1000)
    alice, _ = connect(lobby, "alice")
    create(lobby, alice)
    lobby.disconnect(alice.player_id)
    clock.advance(600)

    _, client = connect(lobby, "alice", token=alice.token)

    assert [m["type"] for m in client.messages] == ["welcome", "rooms"]
    assert alice.room is None


def test_sessions_expire_and_free_the_nickname():
    lobby, clock = make_lobby()
    alice, _ = connect(lobby, "alice")
    lobby.disconnect(alice.player_id)

    clock.advance(60)

    assert alice.player_id not in lobby.sessions
    connect(lobby, "alice")


def test_a_stale_connection_hanging_up_does_not_disconnect_the_new_one():
    lobby, _ = make_lobby()
    alice, old = connect(lobby, "alice")
    create(lobby, alice)
    _, new = connect(lobby, "alice", token=alice.token)

    lobby.disconnect(alice.player_id, old)

    assert alice.participant is new
    assert alice.room.humans() == 1
