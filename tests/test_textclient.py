import asyncio

from holdem import protocol
from holdem.core.betting import Action, LegalActions
from holdem.core.table import TableConfig
from holdem.server.app import start_server
from holdem.textclient import TextClient, run

LEGAL = LegalActions(frozenset({Action.FOLD, Action.CALL, Action.RAISE}), call_amount=2, min_raise_to=4, max_raise_to=100)


def seat(index, name, stack=100, cards=None, **flags):
    entry = {"seat": index, "player_id": name, "name": name, "stack": stack, "away": False, "leaving": False,
             "in_hand": True, "folded": False, "all_in": False, "bet": 0, "cards": cards}
    return {**entry, **flags}


def state(to_act=0):
    hand = {"number": 3, "street": "flop", "board": ["AH", "9C", "5S"], "pots": [{"amount": 12, "eligible": [0, 2]}],
            "current_bet": 4, "to_act": to_act, "button": 2, "small_blind": 0, "big_blind": 2, "complete": False}
    seats = [seat(0, "alice", 94, ["AS", "AD"], bet=4), None, seat(2, "bob", 90, folded=True)]
    return protocol.table_state(0, seats, hand)


def test_first_state_is_rendered_with_positions_and_own_cards():
    client = TextClient()

    lines = client.on_message(state())

    assert lines[0] == "Hand 3 · flop · board [A♥] [9♣] [5♠] · pot 12"
    assert lines[1] == "  Seat 1: alice 94 bet 4 [A♠] [A♦] (you, to act)"
    assert lines[2] == "  Seat 2: empty"
    assert lines[3] == "  Seat 3: bob 90 (button, folded)"
    assert client.on_message(state()) == []


def test_events_use_seat_names():
    client = TextClient()
    client.on_message(state())

    assert client.on_message(protocol.event(3, {"type": "hole_cards", "seat": 2})) == ["bob is dealt two cards"]
    assert client.on_message(protocol.event(4, {"type": "hand_start", "button": 2, "small_blind": 0, "big_blind": 2})) == [
        "", "=== Hand 4 · button bob",
    ]


def test_your_turn_prompt_and_action_commands():
    client = TextClient()
    client.on_message(state())

    assert client.on_message(protocol.your_turn(3, 0, LEGAL)) == [
        "Your turn with [A♠] [A♦]: fold (f) / call 2 (c) / raise to 4-100 (r N)"
    ]
    assert client.command("r 2") == (None, ["raise must go to between 4 and 100"])
    assert client.command("r 10") == (protocol.action("raise", 10), [])


def test_turn_ends_when_the_state_moves_on():
    client = TextClient()
    client.on_message(state())
    client.on_message(protocol.your_turn(3, 0, LEGAL))

    client.on_message(state(to_act=2))

    assert client.command("c") == (None, ["It is not your turn."])


def test_table_commands():
    client = TextClient()

    assert client.command("sit 3 150") == (protocol.sit(2, 150), [])
    assert client.command("rebuy 50") == (protocol.rebuy(50), [])
    assert client.command("leave") == (protocol.leave(), [])
    assert client.command("sit 3") == (None, ["Usage: sit SEAT BUYIN"])
    assert client.command("rebuy -5") == (None, ["Usage: rebuy AMOUNT"])
    assert client.command("dance")[1][0].startswith("Unknown command")
    assert client.command("") == (None, [])


def test_errors_are_shown():
    assert TextClient().on_message(protocol.error("invalid", "that seat is taken")) == ["! that seat is taken"]


async def wait_for(lines, text, timeout=3.0):
    async with asyncio.timeout(timeout):
        while not any(text in line for line in lines):
            await asyncio.sleep(0.01)


async def test_text_client_plays_against_a_server_bot():
    config = TableConfig(small_blind=1, big_blind=2, min_buy_in=40, max_buy_in=200, max_seats=4)
    server, _ = await start_server(config, host="127.0.0.1", port=0, bots=1, bot_think=0.01, hand_delay=0.01)
    port = server.sockets[0].getsockname()[1]
    typed: asyncio.Queue = asyncio.Queue()
    shown: list[str] = []
    try:
        session = asyncio.create_task(run(f"ws://127.0.0.1:{port}", "alice", None, typed, shown.append))
        await wait_for(shown, "Connected.")
        await typed.put("sit 2 100")
        await wait_for(shown, "Your turn")
        await typed.put("f")
        await wait_for(shown, "alice folds")
        await typed.put("quit")
        assert await session == 0
    finally:
        server.close()
        await server.wait_closed()

    assert any(line.startswith("alice is dealt [") for line in shown)
    assert any(line == "Bot 1 is dealt two cards" for line in shown)


async def test_wrong_password_is_reported():
    config = TableConfig(small_blind=1, big_blind=2, min_buy_in=40, max_buy_in=200)
    server, _ = await start_server(config, host="127.0.0.1", port=0, password="pw")
    port = server.sockets[0].getsockname()[1]
    shown: list[str] = []
    try:
        code = await run(f"ws://127.0.0.1:{port}", "alice", "nope", asyncio.Queue(), shown.append)
    finally:
        server.close()
        await server.wait_closed()

    assert code == 1
    assert shown == ["! wrong room password", "Disconnected from the server."]


async def test_unreachable_server_is_reported():
    shown: list[str] = []

    assert await run("ws://127.0.0.1:9", "alice", None, asyncio.Queue(), shown.append) == 1
    assert shown[0].startswith("Cannot connect to ws://127.0.0.1:9")
