import queue
import threading

import pytest

from holdem import protocol
from holdem.client.app import HoldemApp
from holdem.client.layout import HEIGHT, TABLE_CENTER, WIDTH, preset_amount, seat_positions
from holdem.core.betting import Action, LegalActions
from holdem.core.table import TableConfig

CONFIG = TableConfig(small_blind=1, big_blind=2, min_buy_in=40, max_buy_in=200, max_seats=6)


class FakeScheduler:
    def __init__(self):
        self.pending = []

    def __call__(self, delay, callback):
        self.pending.append(callback)

    def run_until_idle(self, limit=5000):
        for _ in range(limit):
            if not self.pending:
                return
            self.pending.pop(0)()
        raise AssertionError("never went idle")


class FakeNetwork:
    def __init__(self):
        self.incoming = queue.Queue()
        self.sent = []
        self.connected = None
        self.closed = False

    def connect(self, url, hello):
        self.connected = (url, hello)

    def send(self, message):
        self.sent.append(message)

    def close(self):
        self.closed = True


class FakeServer:
    instances = []
    fail_with = None

    def __init__(self, config, port, password=None, bots=0):
        self.config, self.requested_port, self.password, self.bots = config, port, password, bots
        self.ready = threading.Event()
        self.port = None
        self.error = None
        self.stopped = False
        FakeServer.instances.append(self)

    def start(self):
        if FakeServer.fail_with:
            self.error = FakeServer.fail_with
        else:
            self.port = self.requested_port
        self.ready.set()

    def stop(self):
        self.stopped = True


@pytest.fixture(scope="module")
def app():
    networks = []
    answers = []

    def new_network():
        networks.append(FakeNetwork())
        return networks[-1]

    def ask(title, prompt, minimum, maximum, initial):
        app.asked.append((minimum, maximum, initial))
        return answers.pop(0) if answers else None

    app = HoldemApp(network_factory=new_network, server_factory=FakeServer, ask_integer=ask)
    app.networks, app.answers, app.asked = networks, answers, []
    app.update_idletasks()
    yield app
    app.destroy()


@pytest.fixture
def scheduler(app, monkeypatch):
    fake = FakeScheduler()
    monkeypatch.setattr(app, "_schedule", fake)
    app.exit_room()
    if app.language != "zh":
        app.toggle_language()
    app.vars["nickname"].set("alice")
    app.vars["join_host"].set("127.0.0.1")
    app.vars["join_port"].set("8765")
    app.vars["blinds"].set("1/2")
    app.vars["bots"].set("0")
    app.answers.clear()
    app.asked.clear()
    FakeServer.instances.clear()
    FakeServer.fail_with = None
    return fake


def texts(app, tag="dyn"):
    return [app.canvas.itemcget(item, "text") for item in app.canvas.find_withtag(tag) if app.canvas.type(item) == "text"]


def seat(index, name, stack=100, **flags):
    entry = {"seat": index, "player_id": name, "name": name, "stack": stack, "away": False, "leaving": False,
             "in_hand": False, "folded": False, "all_in": False, "bet": 0, "cards": None}
    return {**entry, **flags}


def hand(**overrides):
    view = {"number": 1, "street": "preflop", "board": [], "pots": [], "current_bet": 2, "to_act": 0,
            "button": 0, "small_blind": 0, "big_blind": 1, "complete": False}
    return {**view, **overrides}


def state(your_seat, seats, hand_view=None, max_seats=6):
    entries = [None] * max_seats
    for entry in seats:
        entries[entry["seat"]] = entry
    return protocol.table_state(your_seat, entries, hand_view)


def enter_table(app, table_state):
    app.join_room()
    app.handle_message(protocol.welcome("alice", "tok", CONFIG))
    app.handle_message(table_state)
    return app.network


LEGAL = LegalActions(frozenset({Action.FOLD, Action.CALL, Action.RAISE, Action.ALL_IN}), call_amount=1, min_raise_to=4, max_raise_to=99)


def heads_up_preflop():
    """Alice (seat 0, button and small blind) to act; Bob (seat 1) is the big blind."""
    seats = [
        seat(0, "alice", 99, in_hand=True, bet=1, cards=["AS", "KD"]),
        seat(1, "bob", 98, in_hand=True, bet=2),
    ]
    return state(0, seats, hand())


def deal_events():
    return [
        protocol.event(1, {"type": "hand_start", "button": 0, "small_blind": 0, "big_blind": 1}),
        protocol.event(1, {"type": "blind", "seat": 0, "blind": "small", "amount": 1, "all_in": False}),
        protocol.event(1, {"type": "blind", "seat": 1, "blind": "big", "amount": 2, "all_in": False}),
        protocol.event(1, {"type": "hole_cards", "seat": 0, "cards": ["AS", "KD"]}),
        protocol.event(1, {"type": "hole_cards", "seat": 1}),
    ]


# Layout and bet math


@pytest.mark.parametrize("max_seats", range(2, 10))
def test_your_seat_is_at_the_bottom_and_every_seat_fits(max_seats):
    for your_seat in (None, 0, max_seats - 1):
        positions = seat_positions(max_seats, your_seat)
        x, y = positions[your_seat or 0]
        assert round(x) == TABLE_CENTER[0] and y > TABLE_CENTER[1]
        for px, py in positions:
            assert 60 < px < WIDTH - 60 and 110 < py < HEIGHT - 150


def test_next_seat_clockwise_is_on_your_left():
    positions = seat_positions(6, 2)

    assert positions[3][0] < TABLE_CENTER[0] < positions[1][0]


@pytest.mark.parametrize(
    "fraction, pot, my_bet, call, low, high, expected",
    [
        (0.5, 10, 0, 0, 2, 100, 5),
        (1.0, 10, 0, 0, 2, 100, 10),
        (1.0, 20, 0, 10, 20, 100, 40),
        (0.5, 3, 1, 1, 4, 99, 4),
        (1.0, 500, 0, 0, 2, 100, 100),
    ],
)
def test_preset_amount(fraction, pot, my_bet, call, low, high, expected):
    assert preset_amount(fraction, pot, my_bet, call, low, high) == expected


# Lobby


def lobby_status(app):
    return texts(app, "lobby_status")


def test_join_needs_a_nickname(app, scheduler):
    app.vars["nickname"].set("   ")

    app.join_room()

    assert lobby_status(app) == ["请输入昵称"]
    assert app.network is None


def test_join_connects_with_a_hello(app, scheduler):
    app.vars["join_host"].set("10.0.0.5")
    app.vars["join_port"].set("9000")
    app.vars["join_password"].set("pw")

    app.join_room()

    assert app.network.connected == ("ws://10.0.0.5:9000", protocol.hello("alice", "pw"))
    assert lobby_status(app) == ["正在连接…"]
    assert not app.join_button.enabled
    app.vars["join_password"].set("")


@pytest.mark.parametrize("port", ["0", "70000", "abc", ""])
def test_join_rejects_bad_ports(app, scheduler, port):
    app.vars["join_port"].set(port)

    app.join_room()

    assert lobby_status(app) == ["端口必须是 1 到 65535 之间的整数"]


def test_create_room_starts_a_server_then_joins_it(app, scheduler):
    app.vars["blinds"].set("5/10")
    app.vars["bots"].set("2")
    app.vars["create_port"].set("9100")

    app.create_room()
    scheduler.run_until_idle()

    server = FakeServer.instances[-1]
    assert (server.config.big_blind, server.config.min_buy_in, server.config.max_buy_in) == (10, 200, 1000)
    assert server.bots == 2
    assert app.network.connected[0] == "ws://127.0.0.1:9100"
    assert app.room_address.endswith(":9100")


@pytest.mark.parametrize(
    "blinds, bots, message",
    [("x", "0", "盲注要写成「小盲/大盲」，例如 1/2"), ("2/1", "0", "盲注要写成「小盲/大盲」，例如 1/2"), ("1/2", "9", "电脑玩家人数要在 0 到 8 之间")],
)
def test_create_room_validates_settings(app, scheduler, blinds, bots, message):
    app.vars["blinds"].set(blinds)
    app.vars["bots"].set(bots)

    app.create_room()

    assert lobby_status(app) == [message]
    assert not FakeServer.instances


def test_create_room_reports_a_server_that_cannot_start(app, scheduler):
    FakeServer.fail_with = "port in use"

    app.create_room()
    scheduler.run_until_idle()

    assert lobby_status(app) == ["无法创建房间：port in use"]
    assert app.network is None


def test_refused_join_returns_to_the_lobby_with_the_reason(app, scheduler):
    app.join_room()
    network = app.network

    app.handle_message(protocol.error("bad_password", "wrong room password"))

    assert network.closed
    assert app.screen == "lobby"
    assert lobby_status(app) == ["房间密码不对"]


def test_failed_connection_is_reported(app, scheduler):
    app.join_room()

    app.handle_message({"type": "_connect_failed", "reason": "refused"})

    assert lobby_status(app) == ["无法连接：refused"]


# Table


def test_welcome_and_state_open_the_table(app, scheduler):
    enter_table(app, state(None, [seat(1, "bob")]))

    assert app.screen == "table"
    shown = texts(app)
    assert "bob" in shown
    assert shown.count("点击入座") == 5
    assert "点击空座入座" in texts(app, "status")
    assert any("盲注 1 / 2" in text for text in shown)


def test_clicking_an_empty_seat_asks_for_a_buy_in_and_sits(app, scheduler):
    network = enter_table(app, state(None, [seat(1, "bob")]))
    app.answers.append(150)

    app.click_seat(3)

    assert app.asked == [(40, 200, 200)]
    assert network.sent == [protocol.sit(3, 150)]
    assert app.canvas.tag_bind("sit-3", "<Button-1>")


def test_events_animate_with_buttons_locked_then_match_the_state(app, scheduler):
    enter_table(app, state(0, [seat(0, "alice"), seat(1, "bob")]))

    for message in deal_events():
        app.handle_message(message)
    app.handle_message(heads_up_preflop())
    app.handle_message(protocol.your_turn(1, 0, LEGAL))

    assert app.animating
    assert not app.call_button.enabled
    scheduler.run_until_idle()

    assert not app.animating
    assert app.view == app.state
    assert app.call_button.enabled
    assert app.call_button.text == "跟注 1"
    assert app.raise_button.text == "加注到 4"
    assert "轮到你了" in texts(app, "status")
    assert app.labels[1] == ("大盲 2", "neutral")


def your_turn_ready(app, scheduler, legal=LEGAL):
    network = enter_table(app, heads_up_preflop())
    app.handle_message(protocol.your_turn(1, 0, legal))
    scheduler.run_until_idle()
    return network


def test_fold_and_call_send_actions_once(app, scheduler):
    network = your_turn_ready(app, scheduler)

    app.check_or_call()
    app.fold()

    assert network.sent == [protocol.action("call")]
    assert not app.call_button.enabled


def test_raise_uses_the_slider_amount(app, scheduler):
    network = your_turn_ready(app, scheduler)

    app.raise_var.set(30)
    app._update_controls()
    assert app.raise_button.text == "加注到 30"
    app.bet_or_raise()

    assert network.sent == [protocol.action("raise", 30)]


def test_raising_everything_is_labelled_all_in(app, scheduler):
    network = your_turn_ready(app, scheduler)

    app.preset(None)

    assert app.raise_button.text == "全下 99"
    app.bet_or_raise()
    assert network.sent == [protocol.action("raise", 99)]


def test_pot_presets_count_the_bets_on_the_table(app, scheduler):
    your_turn_ready(app, scheduler)

    app.preset(1.0)

    # Pot 3 in bets; calling 1 makes 4, so a pot raise goes to 2 + 4.
    assert app.raise_var.get() == 6


def test_check_is_offered_when_free(app, scheduler):
    legal = LegalActions(frozenset({Action.FOLD, Action.CHECK, Action.BET, Action.ALL_IN}), min_raise_to=2, max_raise_to=98)
    seats = [seat(0, "alice", 98, in_hand=True, cards=["AS", "KD"]), seat(1, "bob", 98, in_hand=True)]
    network = enter_table(app, state(0, seats, hand(street="flop", board=["2C", "7D", "9H"], pots=[{"amount": 4, "eligible": [0, 1]}], to_act=0)))
    app.handle_message(protocol.your_turn(1, 0, legal))
    scheduler.run_until_idle()

    assert app.call_button.text == "过牌"
    assert app.raise_button.text == "下注 2"
    app.check_or_call()
    assert network.sent == [protocol.action("check")]


def snapshots(app, monkeypatch):
    seen = []
    render = app._render

    def recording():
        render()
        if app.view and app.view["hand"]:
            pots = sum(pot["amount"] for pot in app.view["hand"]["pots"])
            bets = sum(entry["bet"] for entry in app.view["seats"] if entry)
            seen.append((len(app.view["hand"]["board"]), pots, bets))

    monkeypatch.setattr(app, "_render", recording)
    return seen


def test_street_event_sweeps_bets_into_the_pot_then_deals_the_flop(app, scheduler, monkeypatch):
    seats = [seat(0, "alice", 98, in_hand=True, bet=2, cards=["AS", "KD"]), seat(1, "bob", 98, in_hand=True, bet=2)]
    enter_table(app, state(0, seats, hand(to_act=None)))
    seen = snapshots(app, monkeypatch)

    app.handle_message(protocol.event(1, {"type": "burn"}))
    app.handle_message(protocol.event(1, {"type": "street", "street": "flop", "cards": ["2C", "7D", "9H"]}))
    scheduler.run_until_idle()

    assert (0, 4, 0) in seen
    assert (3, 4, 0) in seen


def test_showdown_reveals_cards_and_award_labels_the_winner(app, scheduler):
    seats = [seat(0, "alice", 90, in_hand=True, cards=["AS", "KD"]), seat(1, "bob", 90, in_hand=True)]
    enter_table(app, state(0, seats, hand(street="river", board=["2C", "7D", "9H", "JS", "QC"], pots=[{"amount": 20, "eligible": [0, 1]}], to_act=None)))

    app.handle_message(protocol.event(1, {"type": "showdown", "seat": 1, "cards": ["9S", "9D"], "best": ["9S", "9D", "9H", "QC", "JS"], "hand": "hand_three_of_a_kind"}))
    app.handle_message(protocol.event(1, {"type": "award", "seat": 1, "amount": 20, "pot": 0}))
    scheduler.run_until_idle()

    assert app.labels[1] == ("三条 · 赢 20", "win")


def test_server_errors_show_in_the_status_line_then_clear(app, scheduler):
    enter_table(app, state(None, [seat(1, "bob")]))

    app.handle_message(protocol.error("invalid", "that seat is taken"))
    assert texts(app, "status") == ["that seat is taken"]
    scheduler.run_until_idle()

    assert texts(app, "status") == ["点击空座入座"]


def test_rebuy_asks_how_much_up_to_the_max_buy_in(app, scheduler):
    network = enter_table(app, state(0, [seat(0, "alice", 50), seat(1, "bob")]))
    app.answers.append(100)

    assert app.rebuy_button.enabled
    app.rebuy()

    assert app.asked == [(1, 150, 150)]
    assert network.sent == [protocol.rebuy(100)]


def test_leave_and_exit(app, scheduler):
    network = enter_table(app, state(0, [seat(0, "alice"), seat(1, "bob")]))

    app.leave_seat()
    app.exit_room()

    assert network.sent == [protocol.leave()]
    assert network.closed
    assert app.screen == "lobby"


def test_exiting_a_hosted_room_stops_its_server(app, scheduler):
    app.create_room()
    scheduler.run_until_idle()
    app.handle_message(protocol.welcome("alice", "tok", CONFIG))

    app.exit_room()

    assert FakeServer.instances[-1].stopped


def test_losing_the_connection_returns_to_the_lobby(app, scheduler):
    enter_table(app, state(0, [seat(0, "alice")]))

    app.handle_message({"type": "_disconnected"})

    assert app.screen == "lobby"
    assert lobby_status(app) == ["与服务器的连接断开了"]


def test_language_toggle_relabels_the_table(app, scheduler):
    your_turn_ready(app, scheduler)

    app.toggle_language()

    assert app.call_button.text == "Call 1"
    assert app.fold_button.text == "Fold"
    assert "Your turn" in texts(app, "status")
