import queue
import threading

import pytest

from holdem import protocol
from holdem.client.app import HoldemApp
from holdem.client.canvas_ui import GOLD, MUTED
from holdem.client.layout import HEIGHT, TABLE_CENTER, WIDTH, bet_steps, nearest_step, next_step, preset_amount, seat_positions
from holdem.core.betting import Action, LegalActions
from holdem.core.table import TableConfig

CONFIG = TableConfig(small_blind=1, big_blind=2, min_buy_in=40, max_buy_in=200, max_seats=6)
SERVER = "wss://poker.example.com:8765"


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
    """Stands in for the built-in server behind "Host here"."""

    instances = []
    fail_with = None

    def __init__(self, port):
        self.requested_port = port
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

    def ask_password(title, prompt):
        app.asked.append(prompt)
        return answers.pop(0) if answers else None

    app = HoldemApp(
        network_factory=new_network, server_factory=FakeServer, ask_integer=ask, ask_password=ask_password, server=SERVER
    )
    app.networks, app.answers, app.asked = networks, answers, []
    app.update_idletasks()
    yield app
    app.destroy()


@pytest.fixture
def scheduler(app, monkeypatch):
    fake = FakeScheduler()
    monkeypatch.setattr(app, "_schedule", fake)
    app.sign_out()
    if app.language != "zh":
        app.toggle_language()
    for name, value in (
        ("nickname", "alice"), ("room_name", ""), ("blinds", "1/2"), ("room_password", ""), ("bots", "0"),
        ("lan_port", "8765"), ("lan_address", ""),
    ):
        app.vars[name].set(value)
    app.answers.clear()
    app.asked.clear()
    FakeServer.instances.clear()
    FakeServer.fail_with = None
    return fake


def texts(app, tag="dyn"):
    return [app.canvas.itemcget(item, "text") for item in app.canvas.find_withtag(tag) if app.canvas.type(item) == "text"]


def seat(index, name, stack=100, **flags):
    entry = {"seat": index, "player_id": name, "name": name, "stack": stack, "away": False, "leaving": False,
             "connected": True, "in_hand": False, "folded": False, "all_in": False, "bet": 0, "cards": None}
    return {**entry, **flags}


def hand(**overrides):
    view = {"number": 1, "street": "preflop", "board": [], "pots": [], "current_bet": 2, "to_act": 0, "time_left": 30,
            "button": 0, "small_blind": 0, "big_blind": 1, "complete": False}
    return {**view, **overrides}


def state(your_seat, seats, hand_view=None, max_seats=6):
    entries = [None] * max_seats
    for entry in seats:
        entries[entry["seat"]] = entry
    return protocol.table_state(your_seat, entries, hand_view)


ROOMS = [
    {"id": "ABC234", "name": "周五局", "small_blind": 1, "big_blind": 2, "players": 3, "seats": 9, "locked": False},
    {"id": "XYZ789", "name": "高额桌", "small_blind": 5, "big_blind": 10, "players": 0, "seats": 9, "locked": True},
]


def enter_lobby(app, rooms=()):
    app.sign_in()
    app.handle_message(protocol.welcome("p1", "tok"))
    app.handle_message(protocol.rooms(list(rooms)))
    return app.network


def enter_table(app, table_state):
    network = enter_lobby(app)
    app.handle_message(protocol.room_joined("ABC234", "周五局", CONFIG))
    app.handle_message(table_state)
    return network


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


@pytest.mark.parametrize(
    "low, high, step, amounts",
    [
        (4, 12, 2, [4, 6, 8, 10, 12]),
        (5, 12, 2, [5, 6, 8, 10, 12]),
        (18, 25, 2, [18, 20, 22, 24, 25]),
        (10, 13, 5, [10, 13]),
    ],
)
def test_bet_steps_keep_the_minimum_and_all_in_and_round_to_the_big_blind(low, high, step, amounts):
    steps = bet_steps(low, high, step)

    assert len(steps) == 81
    assert steps == sorted(steps)
    assert sorted(set(steps)) == amounts


def test_bet_steps_with_nothing_to_choose():
    assert bet_steps(20, 20, 2) == [20]


def test_bet_steps_are_exponential():
    steps = bet_steps(2, 194, 2)

    # A pot-sized bet of 12 sits well into the slider instead of squeezed against the left end.
    assert 0.3 < nearest_step(steps, 12) / (len(steps) - 1) < 0.45
    assert steps[len(steps) // 2] < 194 / 5
    assert steps[-1] - steps[-2] > 5


@pytest.mark.parametrize("amount, index", [(1, 0), (4, 0), (5, 0), (6, 3), (7, 3), (9, 4), (11, 5), (99, 6)])
def test_nearest_step_picks_the_first_position_of_the_closest_amount(amount, index):
    assert nearest_step([4, 4, 4, 6, 8, 10, 12], amount) == index


@pytest.mark.parametrize(
    "index, direction, expected", [(0, 1, 3), (1, 1, 3), (3, 1, 4), (6, 1, 6), (6, -1, 5), (3, -1, 0), (4, -1, 3), (0, -1, 0)]
)
def test_next_step_skips_positions_with_the_same_amount(index, direction, expected):
    assert next_step([4, 4, 4, 6, 8, 10, 12], index, direction) == expected


# Signing in and the room list


def lobby_status(app):
    return texts(app, "lobby_status")


def test_signing_in_needs_a_nickname(app, scheduler):
    app.vars["nickname"].set("   ")

    app.sign_in()

    assert lobby_status(app) == ["请输入昵称"]
    assert app.network is None


def test_signing_in_says_hello_to_the_fixed_server(app, scheduler):
    app.sign_in()

    assert app.network.connected == (SERVER, protocol.hello("alice"))
    assert lobby_status(app) == ["正在连接…"]
    assert not app.sign_in_button.enabled


def test_a_refused_hello_stays_on_the_sign_in_screen_with_the_reason(app, scheduler):
    app.sign_in()
    network = app.network

    app.handle_message(protocol.error("nickname_taken", "taken"))

    assert network.closed
    assert app.screen == "login"
    assert lobby_status(app) == ["这个昵称已经有人用了"]


def test_failed_connection_is_reported(app, scheduler):
    app.sign_in()

    app.handle_message({"type": "_connect_failed", "reason": "refused"})

    assert app.screen == "login"
    assert lobby_status(app) == ["无法连接：refused"]


def test_welcome_shows_the_room_list(app, scheduler):
    enter_lobby(app, ROOMS)

    assert app.screen == "rooms"
    shown = texts(app, "room_rows")
    assert ["周五局", "盲注 1 / 2", "3/9 人"] == shown[:3]
    assert "需密码" in shown and "高额桌" in shown
    assert app.vars["room_name"].get() == "alice的房间"


def test_an_empty_list_invites_you_to_create_a_room(app, scheduler):
    enter_lobby(app)

    assert texts(app, "room_rows") == ["还没有房间，在右边创建一个吧"]


def test_the_list_updates_while_you_watch(app, scheduler):
    enter_lobby(app, ROOMS)

    app.handle_message(protocol.rooms(ROOMS[1:]))

    assert "周五局" not in texts(app, "room_rows")


def test_clicking_an_open_room_joins_it(app, scheduler):
    network = enter_lobby(app, ROOMS)

    app.enter_room(ROOMS[0])
    app.enter_room(ROOMS[0])  # a second click while waiting does nothing

    assert network.sent == [protocol.join_room("ABC234")]
    assert lobby_status(app) == ["正在进入房间…"]
    app.handle_message(protocol.room_joined("ABC234", "周五局", CONFIG))
    assert app.screen == "table"


def test_a_locked_room_asks_for_its_password(app, scheduler):
    network = enter_lobby(app, ROOMS)
    app.answers.append("pw")

    app.enter_room(ROOMS[1])

    assert app.asked == ["「高额桌」需要密码才能进入"]
    assert network.sent == [protocol.join_room("XYZ789", "pw")]
    app.handle_message(protocol.error("bad_password", "wrong room password"))
    assert app.screen == "rooms"
    assert lobby_status(app) == ["房间密码不对"]


def test_cancelling_the_password_sends_nothing(app, scheduler):
    network = enter_lobby(app, ROOMS)

    app.enter_room(ROOMS[1])

    assert network.sent == []


def test_create_room_sends_the_settings(app, scheduler):
    network = enter_lobby(app)
    app.vars["blinds"].set("5/10")
    app.vars["bots"].set("2")
    app.vars["room_password"].set("pw")

    app.create_room()

    assert network.sent == [protocol.create_room("alice的房间", 5, 10, password="pw", bots=2)]
    assert not app.create_button.enabled


@pytest.mark.parametrize(
    "name, blinds, bots, message",
    [
        ("x", "x", "0", "盲注要写成「小盲/大盲」，例如 1/2"),
        ("x", "2/1", "0", "盲注要写成「小盲/大盲」，例如 1/2"),
        ("x", "1/2", "6", "电脑玩家人数要在 0 到 5 之间"),
        ("   ", "1/2", "0", "请输入房间名（最多 24 个字）"),
        ("x" * 25, "1/2", "0", "请输入房间名（最多 24 个字）"),
    ],
)
def test_create_room_checks_the_settings_first(app, scheduler, name, blinds, bots, message):
    network = enter_lobby(app)
    app.vars["room_name"].set(name)
    app.vars["blinds"].set(blinds)
    app.vars["bots"].set(bots)

    app.create_room()

    assert lobby_status(app) == [message]
    assert network.sent == []


@pytest.mark.parametrize(
    "code, message", [("too_many_rooms", "服务器上的房间已满，请稍后再试"), ("room_not_found", "这个房间已经关闭了")]
)
def test_room_errors_show_on_the_room_list(app, scheduler, code, message):
    enter_lobby(app, ROOMS)
    app.create_room()

    app.handle_message(protocol.error(code, "whatever"))

    assert lobby_status(app) == [message]
    assert app.create_button.enabled


def test_host_here_starts_the_built_in_server_then_enters_its_lobby(app, scheduler, monkeypatch):
    monkeypatch.setattr("holdem.client.app.lan_addresses", lambda: ["192.168.1.20"])
    app.vars["lan_port"].set("9100")

    app.host_here()
    scheduler.run_until_idle()

    assert FakeServer.instances[-1].requested_port == 9100
    assert app.network.connected == ("ws://127.0.0.1:9100", protocol.hello("alice"))
    app.handle_message(protocol.welcome("p1", "tok"))
    app.handle_message(protocol.rooms([]))
    assert app.screen == "rooms"
    assert texts(app, "lan_address") == ["本机开服中 · 局域网地址 192.168.1.20:9100"]


def test_host_here_checks_the_port_and_reports_a_server_that_cannot_start(app, scheduler):
    app.vars["lan_port"].set("70000")
    app.host_here()
    assert lobby_status(app) == ["端口必须是 1 到 65535 之间的整数"]
    assert not FakeServer.instances

    app.vars["lan_port"].set("8765")
    FakeServer.fail_with = "port in use"
    app.host_here()
    scheduler.run_until_idle()

    assert lobby_status(app) == ["无法开服：port in use"]
    assert app.network is None
    assert app.sign_in_button.enabled


@pytest.mark.parametrize(
    "typed, url",
    [
        ("192.168.1.20", "ws://192.168.1.20:8765"),
        ("192.168.1.20:9100", "ws://192.168.1.20:9100"),
        ("wss://poker.example.com:8765", "wss://poker.example.com:8765"),
    ],
)
def test_connect_goes_to_the_typed_address(app, scheduler, typed, url):
    app.vars["lan_address"].set(typed)

    app.connect_to()

    assert app.network.connected == (url, protocol.hello("alice"))


@pytest.mark.parametrize("typed", ["", ":8765", "192.168.1.20:port", "192.168.1.20:99999"])
def test_connect_rejects_addresses_it_cannot_use(app, scheduler, typed):
    app.vars["lan_address"].set(typed)

    app.connect_to()

    assert lobby_status(app) == ["地址要写成 IP 或「IP:端口」，例如 192.168.1.5:8765"]
    assert app.network is None


def test_signing_out_stops_the_server_we_host(app, scheduler):
    app.host_here()
    scheduler.run_until_idle()
    app.handle_message(protocol.welcome("p1", "tok"))

    app.sign_out()

    assert FakeServer.instances[-1].stopped
    assert app.lan_address == ""


def test_reconnecting_goes_back_to_the_same_server(app, scheduler):
    app.vars["lan_address"].set("192.168.1.20")
    app.connect_to()
    app.handle_message(protocol.welcome("p1", "tok"))
    app.handle_message(protocol.rooms([]))

    app.handle_message({"type": "_disconnected"})
    scheduler.run_until_idle()

    assert app.network.connected == ("ws://192.168.1.20:8765", protocol.hello("alice", None, "tok"))


def test_signing_out_goes_back_to_the_sign_in_screen(app, scheduler):
    network = enter_lobby(app, ROOMS)

    app.sign_out()

    assert network.closed
    assert app.screen == "login"


# Table


def test_room_joined_and_state_open_the_table(app, scheduler):
    enter_table(app, state(None, [seat(1, "bob")]))

    assert app.screen == "table"
    shown = texts(app)
    assert any(text.startswith("周五局") for text in shown)
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

    app.raise_index.set(app.raise_steps.index(30))
    app._update_controls()
    assert app.raise_button.text == "加注到 30"
    app.bet_or_raise()

    assert network.sent == [protocol.action("raise", 30)]


def test_the_slider_moves_in_big_blind_steps_and_keeps_the_minimum_and_all_in(app, scheduler):
    legal = LegalActions(frozenset({Action.FOLD, Action.CALL, Action.RAISE, Action.ALL_IN}), call_amount=4, min_raise_to=9, max_raise_to=97)
    your_turn_ready(app, scheduler, legal)

    assert app.raise_steps == bet_steps(9, 97, 2)
    assert (app.raise_scale.cget("from"), app.raise_scale.cget("to")) == (0, len(app.raise_steps) - 1)
    assert app.raise_button.text == "加注到 9"
    # The arrow keys always reach the next different amount, one big blind at a time down here.
    for expected in ("加注到 10", "加注到 12", "加注到 14"):
        app.step_raise(1)
        assert app.raise_button.text == expected
    app.step_raise(-1)
    assert app.raise_button.text == "加注到 12"
    app.raise_index.set(len(app.raise_steps) - 1)
    app._update_controls()
    assert app.raise_button.text == "全下 97"


def test_pot_presets_snap_to_the_nearest_step(app, scheduler):
    your_turn_ready(app, scheduler)

    app.preset(0.75)

    # 1 + 1 + round(0.75 * 4) = 5 sits between the steps 4 and 6 and goes down; a pot raise lands on 6.
    assert app.raise_button.text == "加注到 4"
    app.preset(1.0)
    assert app.raise_button.text == "加注到 6"


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
    assert app._raise_amount() == 6


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


def drawn_images(app):
    return {app.canvas.itemcget(item, "image") for item in app.canvas.find_withtag("dyn") if app.canvas.type(item) == "image"}


def test_your_folded_cards_turn_grey(app, scheduler):
    live = seat(0, "alice", 98, in_hand=True, cards=["AS", "KD"])
    enter_table(app, state(0, [live, seat(1, "bob", 98, in_hand=True)], hand(to_act=1)))
    normal = {str(app._photo(code, small=True)) for code in ("AS", "KD")}
    assert normal <= drawn_images(app)

    folded = {**live, "folded": True}
    app.handle_message(state(0, [folded, seat(1, "bob", 98, in_hand=True)], hand(to_act=1)))

    greyed = {str(app._photo(code, small=True, grey=True)) for code in ("AS", "KD")}
    assert greyed <= drawn_images(app)
    assert not normal & drawn_images(app)
    red, green, blue = app._photo("AS", small=True, grey=True).get(30, 40)
    assert red == green == blue and red < 200


def flop_bet_ready(app, scheduler, pot):
    legal = LegalActions(frozenset({Action.FOLD, Action.CHECK, Action.BET, Action.ALL_IN}), min_raise_to=2, max_raise_to=98)
    seats = [seat(0, "alice", 98, in_hand=True, cards=["AS", "KD"]), seat(1, "bob", 98, in_hand=True)]
    pots = [{"amount": pot, "eligible": [0, 1]}]
    enter_table(app, state(0, seats, hand(street="flop", board=["2C", "7D", "9H"], pots=pots, to_act=0)))
    app.handle_message(protocol.your_turn(1, 0, legal))
    scheduler.run_until_idle()


def mark_x(app, key):
    line = next(item for item in app.canvas.find_withtag(f"mark-{key}") if app.canvas.type(item) == "line")
    return app.canvas.coords(line)[0]


def test_pot_fractions_are_marked_above_the_slider(app, scheduler):
    flop_bet_ready(app, scheduler, pot=12)

    # 3/4 of the pot (9) would touch its neighbours' labels, so it is the one left out.
    assert set(texts(app, "marks")) == {"1/4", "1/2", "满池"}
    steps = app.raise_steps
    slider = int(app.raise_scale.cget("sliderlength"))
    left, span = 1076 - 105 + slider / 2, 210 - slider
    assert mark_x(app, "mark_half_pot") == pytest.approx(left + span * steps.index(6) / (len(steps) - 1))
    xs = [mark_x(app, key) for key in ("mark_quarter_pot", "mark_half_pot", "mark_pot")]
    assert xs == sorted(xs)
    labels = sorted(app.canvas.bbox(item) for item in app.canvas.find_withtag("marks") if app.canvas.type(item) == "text")
    assert all(left_label[2] < right_label[0] for left_label, right_label in zip(labels, labels[1:]))

    app.preset(0.5)
    assert app.raise_button.text == "下注 6"
    assert (mark_color(app, "mark_half_pot"), mark_color(app, "mark_pot")) == (GOLD, MUTED)


def test_crowded_marks_keep_the_most_useful_ones(app, scheduler):
    flop_bet_ready(app, scheduler, pot=40)

    shown = texts(app, "marks")
    assert "满池" in shown and "1/2" in shown
    labels = sorted(app.canvas.bbox(item) for item in app.canvas.find_withtag("marks") if app.canvas.type(item) == "text")
    assert all(left_label[2] < right_label[0] for left_label, right_label in zip(labels, labels[1:]))
    app.preset(1.0)
    assert app.raise_button.text == "下注 40"



def mark_color(app, key):
    text = next(item for item in app.canvas.find_withtag(f"mark-{key}") if app.canvas.type(item) == "text")
    return app.canvas.itemcget(text, "fill")


def test_marks_outside_the_range_are_left_out(app, scheduler):
    flop_bet_ready(app, scheduler, pot=4)

    # 1/4 and 1/2 of 4 are at or below the minimum bet of 2.
    assert set(texts(app, "marks")) <= {"3/4", "满池"}
    assert "满池" in texts(app, "marks")


def test_the_quarter_pot_preset(app, scheduler):
    flop_bet_ready(app, scheduler, pot=40)

    app.preset(0.25)

    assert app.raise_button.text == "下注 10"


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


def test_leave_the_seat_then_the_room(app, scheduler):
    network = enter_table(app, state(0, [seat(0, "alice"), seat(1, "bob")]))

    app.leave_seat()
    app.exit_room()
    assert network.sent == [protocol.leave(), protocol.leave_room()]
    app.handle_message(protocol.room_left())
    app.handle_message(protocol.rooms(ROOMS))

    assert not network.closed
    assert app.screen == "rooms"
    assert app.state is None
    assert "周五局" in texts(app, "room_rows")


def test_a_room_list_at_the_table_means_the_room_closed(app, scheduler):
    enter_table(app, state(0, [seat(0, "alice")]))

    app.handle_message(protocol.rooms(ROOMS[1:]))

    assert app.screen == "rooms"
    assert lobby_status(app) == ["你所在的房间已经关闭了"]


def test_losing_the_connection_reconnects_with_the_token(app, scheduler):
    enter_table(app, heads_up_preflop())
    app.handle_message(protocol.your_turn(1, 0, LEGAL))
    scheduler.run_until_idle()

    app.handle_message({"type": "_disconnected"})

    assert app.screen == "table"
    assert texts(app, "status") == ["连接断开，正在重连…"]
    assert not app.call_button.enabled and not app.leave_button.enabled
    scheduler.run_until_idle()
    assert app.network.connected == (SERVER, protocol.hello("alice", None, "tok"))

    app.handle_message(protocol.welcome("p1", "tok"))
    app.handle_message(protocol.room_joined("ABC234", "周五局", CONFIG))
    app.handle_message(heads_up_preflop())
    app.handle_message(protocol.your_turn(1, 0, LEGAL, time_left=12))

    assert not app.reconnecting
    assert app.call_button.enabled
    assert "轮到你了" in texts(app, "status")


def test_reconnecting_gives_up_after_many_failures(app, scheduler):
    enter_table(app, state(0, [seat(0, "alice")]))

    app.handle_message({"type": "_disconnected"})
    for _ in range(30):
        scheduler.run_until_idle()
        app.handle_message({"type": "_connect_failed", "reason": "refused"})

    assert app.screen == "login"
    assert lobby_status(app) == ["与服务器的连接断开了"]


def test_a_refused_rejoin_returns_to_the_lobby(app, scheduler):
    enter_table(app, state(0, [seat(0, "alice")]))
    app.handle_message({"type": "_disconnected"})
    scheduler.run_until_idle()

    app.handle_message(protocol.error("nickname_taken", "taken"))

    assert app.screen == "login"
    assert lobby_status(app) == ["这个昵称已经有人用了"]


def test_being_replaced_by_another_connection_returns_to_the_lobby(app, scheduler):
    enter_table(app, state(0, [seat(0, "alice")]))

    app.handle_message(protocol.error("replaced", "you joined again"))

    assert app.screen == "login"
    assert lobby_status(app) == ["你用这个昵称在别处登录了"]


def test_losing_the_connection_in_the_lobby_reconnects_too(app, scheduler):
    enter_lobby(app, ROOMS)

    app.handle_message({"type": "_disconnected"})
    assert lobby_status(app) == ["连接断开，正在重连…"]
    scheduler.run_until_idle()
    app.handle_message(protocol.welcome("p1", "tok"))
    app.handle_message(protocol.rooms(ROOMS))

    assert app.screen == "rooms"
    assert lobby_status(app) == []
    assert "周五局" in texts(app, "room_rows")


def test_sit_out_and_come_back(app, scheduler):
    network = enter_table(app, state(0, [seat(0, "alice"), seat(1, "bob")]))
    assert app.away_button.text == "暂离"

    app.toggle_away()
    app.handle_message(state(0, [seat(0, "alice", away=True), seat(1, "bob")]))

    assert app.away_button.text == "回来"
    assert texts(app, "status") == ["你已暂离，点击「回来」继续"]
    assert "alice (暂离)" in texts(app)
    app.toggle_away()
    assert network.sent == [protocol.away(True), protocol.away(False)]


def test_offline_players_are_marked(app, scheduler):
    enter_table(app, state(0, [seat(0, "alice"), seat(1, "bob", connected=False, away=True)]))

    assert "bob (离线)" in texts(app)


def test_the_action_timer_shrinks_under_the_seat_to_act(app, scheduler, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(app, "clock", lambda: now[0])
    seats = [seat(0, "alice", in_hand=True), seat(1, "bob", in_hand=True)]
    enter_table(app, state(0, seats, hand(to_act=1, time_left=15)))

    now[0] = 107.5
    app._draw_timer()

    bars = [app.canvas.coords(item) for item in app.canvas.find_withtag("timer")]
    assert len(bars) == 2
    full, left = bars
    assert full[2] - full[0] == 116
    assert left[2] - left[0] == pytest.approx(116 * 7.5 / 30)
    x, _ = seat_positions(6, 0)[1]
    assert full[0] == x - 58

    app.handle_message(state(0, seats, hand(to_act=1, time_left=None, complete=True)))
    assert not app.canvas.find_withtag("timer")


def test_language_toggle_relabels_the_table(app, scheduler):
    your_turn_ready(app, scheduler)

    app.toggle_language()

    assert app.call_button.text == "Call 1"
    assert app.fold_button.text == "Fold"
    assert "Your turn" in texts(app, "status")
