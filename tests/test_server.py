import asyncio
import json

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from helpers import seeded_decks
from holdem import protocol
from holdem.bots import random_action
from holdem.server import app
from holdem.server.__main__ import parse_args
from holdem.server.app import start_server


@pytest.fixture
async def server():
    servers = []

    async def start(**options):
        options.setdefault("hand_delay", 0)
        options.setdefault("deck_factory", seeded_decks(5))
        options.setdefault("bot_policy", random_action)
        ws_server, lobby = await start_server(host="127.0.0.1", port=0, **options)
        servers.append(ws_server)
        port = ws_server.sockets[0].getsockname()[1]
        return f"ws://127.0.0.1:{port}", lobby

    yield start
    for ws_server in servers:
        ws_server.close()
        await ws_server.wait_closed()


async def send(ws, message):
    await ws.send(protocol.encode(message))


async def recv_until(ws, predicate, timeout=3.0):
    """Read messages until one matches; returns (match, everything read)."""
    seen = []
    async with asyncio.timeout(timeout):
        while True:
            message = protocol.decode_server(await ws.recv())
            seen.append(message)
            if predicate(message):
                return message, seen


def of_type(kind):
    return lambda message: message["type"] == kind


async def joined(url, name, password=None, token=None):
    """Connect and say hello; returns the socket and the welcome."""
    ws = await connect(url)
    await send(ws, protocol.hello(name, password, token))
    welcome, _ = await recv_until(ws, of_type("welcome"))
    return ws, welcome


async def create_room(ws, password=None, bots=0):
    """Open a room (blinds 1/2) and wait until we are at its table; returns the room id."""
    await send(ws, protocol.create_room("test room", 1, 2, password=password, bots=bots))
    joined_room, _ = await recv_until(ws, of_type("room_joined"))
    await recv_until(ws, of_type("table_state"))
    return joined_room["room"]


async def join_room(ws, room_id, password=None):
    await send(ws, protocol.join_room(room_id, password))
    await recv_until(ws, of_type("table_state"))


async def heads_up(url):
    """alice opens a room and takes seat 0, bob joins and takes seat 1."""
    alice, alice_welcome = await joined(url, "alice")
    bob, _ = await joined(url, "bob")
    room_id = await create_room(alice)
    await join_room(bob, room_id)
    await send(alice, protocol.sit(0, 100))
    await send(bob, protocol.sit(1, 100))
    return alice, bob, alice_welcome


async def test_hello_lands_in_the_lobby(server):
    url, _ = await server()
    async with connect(url) as ws:
        await send(ws, protocol.hello("alice"))
        welcome = protocol.decode_server(await ws.recv())
        rooms = protocol.decode_server(await ws.recv())

    assert welcome["type"] == "welcome" and welcome["version"] == protocol.PROTOCOL_VERSION
    assert rooms == protocol.rooms([])


@pytest.mark.parametrize(
    "first, code",
    [
        (protocol.hello("alice", "wrong"), "bad_password"),
        (protocol.sit(0, 100), "bad_message"),
        ({"type": "hello", "version": 99, "nickname": "a", "password": "pw"}, "version_mismatch"),
    ],
)
async def test_refused_handshakes_get_an_error_then_close(server, first, code):
    url, _ = await server(password="pw")
    async with connect(url) as ws:
        await send(ws, first)
        error = protocol.decode_server(await ws.recv())
        with pytest.raises(ConnectionClosed):
            await ws.recv()

    assert (error["type"], error["code"]) == ("error", code)


async def test_malformed_messages_after_joining_are_refused_without_disconnecting(server):
    url, _ = await server()
    ws, _ = await joined(url, "alice")
    async with ws:
        await ws.send("{nope")
        error, _ = await recv_until(ws, of_type("error"))
        await send(ws, protocol.hello("again"))
        again, _ = await recv_until(ws, of_type("error"))
        await create_room(ws)
        await send(ws, protocol.sit(0, 100))
        state, _ = await recv_until(ws, of_type("table_state"))

    assert error["code"] == again["code"] == "bad_message"
    assert state["your_seat"] == 0


async def test_oversized_messages_close_the_connection(server):
    url, _ = await server()
    ws, _ = await joined(url, "alice")
    async with ws:
        await ws.send(json.dumps({"type": "leave", "pad": "x" * protocol.MAX_MESSAGE_BYTES}))
        with pytest.raises(ConnectionClosed):
            await recv_until(ws, lambda message: False)
    assert ws.close_code == 1009


async def test_a_new_room_shows_up_in_the_lobby_and_its_password_is_checked(server):
    url, _ = await server()
    alice, _ = await joined(url, "alice")
    bob, _ = await joined(url, "bob")
    async with alice, bob:
        room_id = await create_room(alice, password="pw")
        listed, _ = await recv_until(bob, lambda m: m["type"] == "rooms" and m["rooms"])
        await send(bob, protocol.join_room(room_id, "wrong"))
        refused, _ = await recv_until(bob, of_type("error"))
        await send(bob, protocol.join_room(room_id, "pw"))
        joined_room, _ = await recv_until(bob, of_type("room_joined"))

    assert listed["rooms"][0]["id"] == room_id and listed["rooms"][0]["locked"]
    assert refused["code"] == "bad_password"
    assert joined_room["room"] == room_id


async def test_two_clients_play_a_hand_and_see_only_their_own_cards(server):
    url, _ = await server()
    alice, bob, _ = await heads_up(url)
    async with alice, bob:
        # Heads-up the first button (seat 0, alice) posts the small blind and acts first.
        turn, alice_seen = await recv_until(alice, of_type("your_turn"))
        assert turn["seat"] == 0
        await send(alice, protocol.action("call"))
        bob_turn, bob_seen = await recv_until(bob, of_type("your_turn"))
        assert "check" in bob_turn["actions"]

        for seen, own in ((alice_seen, 0), (bob_seen, 1)):
            dealt = {m["event"]["seat"]: m["event"].get("cards") for m in seen if m["type"] == "event" and m["event"]["type"] == "hole_cards"}
            assert dealt[own] is not None and dealt[1 - own] is None
        actions = [m["event"] for m in bob_seen if m["type"] == "event" and m["event"]["type"] == "action"]
        assert [(event["seat"], event["action"]) for event in actions] == [(0, "call")]

        await send(bob, protocol.action("raise", 1))
        error, _ = await recv_until(bob, of_type("error"))
        assert error["code"] == "illegal_action"


async def test_reconnecting_with_the_token_resumes_the_room_seat_and_turn(server):
    url, _ = await server()
    alice, bob, welcome = await heads_up(url)
    async with bob:
        await recv_until(alice, of_type("your_turn"))
        await alice.close()
        await recv_until(bob, lambda m: m["type"] == "table_state" and not m["seats"][0]["connected"])

        again, rewelcome = await joined(url, "alice", token=welcome["token"])
        async with again:
            turn, seen = await recv_until(again, of_type("your_turn"))
            await send(again, protocol.action("call"))
            action, _ = await recv_until(bob, lambda m: m["type"] == "event" and m["event"]["type"] == "action")

    assert rewelcome["player_id"] == welcome["player_id"]
    assert [m["type"] for m in seen[:2]] == ["room_joined", "table_state"]
    assert turn["seat"] == 0 and 0 < turn["time_left"] <= 30
    assert (action["event"]["seat"], action["event"]["action"]) == (0, "call")


async def test_joining_again_with_the_token_hangs_up_the_old_connection(server):
    url, _ = await server()
    old, welcome = await joined(url, "alice")
    async with old:
        new, _ = await joined(url, "alice", token=welcome["token"])
        async with new:
            error, _ = await recv_until(old, of_type("error"))
            with pytest.raises(ConnectionClosed):
                await recv_until(old, lambda message: False)
            room_id = await create_room(new)

    assert error["code"] == "replaced"
    assert old.close_reason == "replaced"
    assert room_id


async def test_the_server_acts_for_a_player_who_runs_out_of_time(server):
    url, _ = await server(action_timeout=0.2)
    alice, bob, _ = await heads_up(url)
    async with alice, bob:
        await recv_until(alice, of_type("your_turn"))

        fold, _ = await recv_until(bob, lambda m: m["type"] == "event" and m["event"]["type"] == "action")
        state, _ = await recv_until(bob, lambda m: m["type"] == "table_state" and m["seats"][0]["away"])

    assert (fold["event"]["seat"], fold["event"]["action"]) == (0, "fold")
    assert state["seats"][1]["stack"] == 101


async def test_a_room_with_bots_plays_hands_its_creator_can_watch(server):
    url, _ = await server(bot_think=0.01, hand_delay=0.01)
    ws, _ = await joined(url, "watcher")
    async with ws:
        await create_room(ws, bots=2)
        award, _ = await recv_until(ws, lambda m: m["type"] == "event" and m["event"]["type"] == "award")

    assert award["event"]["amount"] > 0


async def test_connection_drops_a_client_that_stops_reading(monkeypatch):
    closed = []

    class StuckSocket:
        remote_address = ("192.0.2.1", 5000)

        async def close(self, code, reason):
            closed.append(code)

    monkeypatch.setattr(app, "MAX_OUTBOX", 3)
    connection = app.Connection(StuckSocket())
    for _ in range(10):
        connection.deliver(protocol.leave())
    await asyncio.sleep(0)

    assert connection.outbox.qsize() == 3
    assert closed == [1008]


def test_password_can_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("HOLDEM_PASSWORD", "from-env")
    assert parse_args([]).password == "from-env"
    assert parse_args(["--password", "flag"]).password == "flag"
    monkeypatch.setenv("HOLDEM_PASSWORD", "")
    assert parse_args([]).password is None


def test_command_line_defaults_and_validation(monkeypatch):
    monkeypatch.delenv("HOLDEM_PASSWORD", raising=False)
    args = parse_args([])
    assert (args.host, args.port, args.password) == ("0.0.0.0", 8765, None)
    assert (args.max_rooms, args.max_bots, args.empty_room_timeout) == (10, 5, 600.0)
    assert (args.action_timeout, args.reconnect_grace, args.bot_style) == (30.0, 120.0, "smart")
    args = parse_args(["--max-rooms", "3", "--max-bots", "0", "--action-timeout", "15", "--bot-style", "random"])
    assert (args.max_rooms, args.max_bots, args.action_timeout, args.bot_style) == (3, 0, 15.0, "random")

    for argv in (
        ["--max-rooms", "0"], ["--max-bots", "9"], ["--action-timeout", "0"], ["--reconnect-grace", "-1"],
        ["--empty-room-timeout", "-5"], ["--bot-style", "genius"], ["--blinds", "1/2"],
    ):
        with pytest.raises(SystemExit):
            parse_args(argv)
