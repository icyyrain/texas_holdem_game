import asyncio
import json
import random

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from holdem import protocol
from holdem.core.cards import Deck
from holdem.core.table import TableConfig
from holdem.server import app
from holdem.server.__main__ import parse_args
from holdem.server.app import start_server

CONFIG = TableConfig(small_blind=1, big_blind=2, min_buy_in=40, max_buy_in=200, max_seats=6)


def seeded_decks():
    rng = random.Random(5)

    def deck():
        cards = Deck.standard()
        cards.shuffle(rng)
        return cards

    return deck


@pytest.fixture
async def server():
    servers = []

    async def start(**options):
        options.setdefault("hand_delay", 0)
        options.setdefault("deck_factory", seeded_decks())
        ws_server, room = await start_server(CONFIG, host="127.0.0.1", port=0, **options)
        servers.append(ws_server)
        port = ws_server.sockets[0].getsockname()[1]
        return f"ws://127.0.0.1:{port}", room

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


async def joined(url, name, password=None):
    ws = await connect(url)
    await send(ws, protocol.hello(name, password))
    welcome, _ = await recv_until(ws, of_type("welcome"))
    await recv_until(ws, of_type("table_state"))
    return ws, welcome


async def test_hello_gets_welcome_and_state(server):
    url, _ = await server()
    async with connect(url) as ws:
        await send(ws, protocol.hello("alice"))
        welcome = protocol.decode_server(await ws.recv())
        state = protocol.decode_server(await ws.recv())

    assert welcome["type"] == "welcome"
    assert welcome["config"]["max_seats"] == 6
    assert state["type"] == "table_state"


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


async def test_two_clients_play_a_hand_and_see_only_their_own_cards(server):
    url, room = await server()
    alice, _ = await joined(url, "alice")
    bob, _ = await joined(url, "bob")
    async with alice, bob:
        await send(alice, protocol.sit(0, 100))
        await send(bob, protocol.sit(1, 100))

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


async def test_disconnect_folds_and_frees_the_seat(server):
    url, room = await server()
    alice, _ = await joined(url, "alice")
    bob, _ = await joined(url, "bob")
    async with bob:
        await send(alice, protocol.sit(0, 100))
        await send(bob, protocol.sit(1, 100))
        await recv_until(alice, of_type("your_turn"))
        await alice.close()

        state, _ = await recv_until(bob, lambda m: m["type"] == "table_state" and m["seats"][0] is None)

    assert state["seats"][1]["stack"] == 101


async def test_server_bots_play_hands_a_client_can_watch(server):
    url, room = await server(bots=2, bot_think=0.01, hand_delay=0.01)
    ws, _ = await joined(url, "watcher")
    async with ws:
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


def test_command_line_defaults_and_validation():
    args = parse_args(["--blinds", "5/10", "--password", "pw", "--bots", "2"])
    assert (args.config.min_buy_in, args.config.max_buy_in) == (200, 1000)
    assert (args.host, args.port, args.password, args.bots) == ("0.0.0.0", 8765, "pw", 2)

    for argv in (["--blinds", "10/5"], ["--blinds", "x"], ["--seats", "12"], ["--bots", "10"]):
        with pytest.raises(SystemExit):
            parse_args(argv)
