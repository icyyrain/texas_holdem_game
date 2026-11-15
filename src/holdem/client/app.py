"""tkinter desktop client: a lobby to create or join a room, then the table.

Everything is drawn on one Canvas. Server messages arrive on the network thread's queue and
are polled with ``after``. Events become animation steps; while they play, the action buttons
stay locked and the drawn view (``self.view``) lags the server's ``table_state``
(``self.state``), which replaces it once the queue drains.

If the connection drops at the table, the client reconnects with the token from ``welcome`` and
the server answers with a fresh ``table_state``.
"""

from __future__ import annotations

import base64
import copy
from collections import deque
import queue
import time
import tkinter as tk
from tkinter import simpledialog
from typing import Callable

from holdem import protocol
from holdem.client.canvas_ui import (
    CARD_SHADOW,
    CHIP_STYLES,
    CREAM,
    FELT_CENTER,
    FELT_EDGE,
    GOLD,
    GOLD_DARK,
    LOSE,
    MUTED,
    PANEL,
    RAIL_DARK,
    RAIL_LIGHT,
    WIN,
    CanvasButton,
    create_pill,
    create_rounded_rect,
    draw_chip,
    draw_chip_stack,
    ease_out_cubic,
    lerp_color,
)
from holdem.client.card_images import card_back_path, card_image_path
from holdem.client.host import EmbeddedServer, lan_addresses
from holdem.client.layout import (
    BOTTOM_RAIL_TOP,
    CONTROL_Y,
    HEIGHT,
    TABLE_CENTER,
    TOP_RAIL_BOTTOM,
    WIDTH,
    Point,
    bet_steps,
    nearest_step,
    preset_amount,
    seat_positions,
    toward_center,
)
from holdem.client.network import NetworkClient
from holdem.core.betting import Action
from holdem.core.cards import Card
from holdem.core.table import TableConfig
from holdem.i18n import translate

POLL_MS = 30
TIMER_MS = 100
ERROR_MS = 4000
RECONNECT_MS = 2000
RECONNECT_ATTEMPTS = 30
CARD_W, CARD_H = 96, 134
SMALL_W, SMALL_H = 60, 84
BOARD_Y = 296
BOARD_STEP = 104
POT_Y = 392
DECK_POINT = (TABLE_CENTER[0], BOARD_Y)
HOLE_OFFSETS = ((-17, -26), (17, -26))
BET_FRACTION = 0.42
NUMBER_FONT = "Segoe UI"
FLIP_SCALES = (0.8, 0.6, 0.4, 0.2)
ERROR = "#ff8a7a"
PRESETS = (("btn_half_pot", 0.5), ("btn_three_quarter_pot", 0.75), ("btn_pot", 1.0), ("btn_all_in", None))
JOIN_ERRORS = {
    "bad_password": "error_bad_password",
    "nickname_taken": "error_nickname_taken",
    "version_mismatch": "error_version_mismatch",
}

Step = Callable[[Callable[[], None]], None]
AskInteger = Callable[[str, str, int, int, int], "int | None"]


class HoldemApp(tk.Tk):
    def __init__(
        self,
        network_factory: Callable[[], NetworkClient] = NetworkClient,
        server_factory: Callable[..., EmbeddedServer] = EmbeddedServer,
        ask_integer: AskInteger | None = None,
    ) -> None:
        super().__init__()
        self.geometry(f"{WIDTH}x{HEIGHT}")
        self.resizable(False, False)
        self.configure(bg=RAIL_DARK)
        self.network_factory = network_factory
        self.server_factory = server_factory
        self.ask_integer = ask_integer or self._ask_integer
        self.language = "zh"
        self.frame_ms = 16
        self.slide_frames = 10
        self.gap_ms = 60
        self.screen = "lobby"
        self.network: NetworkClient | None = None
        self.server: EmbeddedServer | None = None
        self.connecting = False
        self.reconnecting = False
        self.reconnect_attempts = 0
        self._session = 0  # bumped whenever a room is entered or left, so stale reconnects give up
        self.url = ""
        self.nickname = ""
        self.password: str | None = None
        self.token: str | None = None
        self.clock: Callable[[], float] = time.monotonic
        self.deadline: float | None = None  # when the seat to act runs out of time, on self.clock
        self.lobby_status: tuple[str, dict, bool] | None = None
        self.room_address = ""
        self.config_info: protocol.Message | None = None
        self.state: protocol.Message | None = None
        self.view: protocol.Message | None = None
        self.turn: protocol.Message | None = None
        self.labels: dict[int, tuple[str, str]] = {}
        self.queue: deque[Step] = deque()
        self.animating = False
        self.hidden: set[tuple[str, int]] = set()
        self.status_error: str | None = None
        self._error_token = 0
        self.photos: dict[tuple[str, int], tk.PhotoImage] = {}
        self.widgets: list[tk.Widget] = []  # entries and the slider, destroyed with their screen
        self.vars = {
            name: tk.StringVar(self, value)
            for name, value in (
                ("nickname", ""), ("create_port", str(protocol.DEFAULT_PORT)), ("create_password", ""),
                ("blinds", "1/2"), ("bots", "0"), ("join_host", "127.0.0.1"),
                ("join_port", str(protocol.DEFAULT_PORT)), ("join_password", ""),
            )
        }
        # The raise slider picks an index into raise_steps (big-blind steps, see layout.bet_steps).
        self.raise_index = tk.IntVar(self, 0)
        self.raise_steps: list[int] = [0]

        self.canvas = tk.Canvas(self, width=WIDTH, height=HEIGHT, highlightthickness=0, bd=0, bg=FELT_EDGE)
        self.canvas.pack(fill="both", expand=True)
        self._draw_background()
        self.language_button = CanvasButton(self.canvas, (1140, 28), (80, 32), self.toggle_language, "ghost")
        self._show_lobby()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(POLL_MS, self._poll)
        self.after(TIMER_MS, self._tick_timer)

    def t(self, key: str, **values: object) -> str:
        return translate(self.language, key, **values)

    def _font(self, size: int, weight: str = "normal") -> tuple[str, int, str]:
        family = "Microsoft YaHei UI" if self.language == "zh" else "Segoe UI"
        return (family, -size, weight)

    def _schedule(self, delay: int, callback: Callable[[], None]) -> None:
        self.after(delay, callback)

    def _ask_integer(self, title: str, prompt: str, minimum: int, maximum: int, initial: int) -> int | None:
        return simpledialog.askinteger(
            title, prompt, parent=self, minvalue=minimum, maxvalue=maximum, initialvalue=initial
        )

    # Static drawing

    def _draw_background(self) -> None:
        canvas = self.canvas
        center_x, center_y = TABLE_CENTER
        for index in range(12):
            t = index / 11
            radius_x, radius_y = 560 - 18 * t, 262 - 18 * t
            canvas.create_oval(
                center_x - radius_x, center_y - radius_y, center_x + radius_x, center_y + radius_y,
                fill=lerp_color(RAIL_DARK, RAIL_LIGHT, 1 - abs(2 * t - 1) ** 1.5), outline="", tags="bg",
            )
        steps = 30
        for index in range(steps):
            t = index / (steps - 1)
            radius_x, radius_y = 538 - 400 * t, 240 - 180 * t
            canvas.create_oval(
                center_x - radius_x, center_y - radius_y, center_x + radius_x, center_y + radius_y,
                fill=lerp_color(FELT_EDGE, FELT_CENTER, t ** 0.6), outline="", tags="bg",
            )
        canvas.create_oval(center_x - 512, center_y - 216, center_x + 512, center_y + 216, outline=GOLD_DARK, width=2, tags="bg")
        self._draw_rail(0, TOP_RAIL_BOTTOM, TOP_RAIL_BOTTOM, TOP_RAIL_BOTTOM + 3)
        self._draw_rail(BOTTOM_RAIL_TOP, HEIGHT, BOTTOM_RAIL_TOP, BOTTOM_RAIL_TOP - 3)

    def _draw_rail(self, top: int, bottom: int, trim_y: int, shadow_y: int) -> None:
        height = bottom - top
        for offset in range(height):
            t = offset / (height - 1)
            color = lerp_color(RAIL_DARK, RAIL_LIGHT, 1 - abs(2 * t - 1) ** 1.5)
            self.canvas.create_line(0, top + offset, WIDTH, top + offset, fill=color, tags="bg")
        self.canvas.create_line(0, shadow_y, WIDTH, shadow_y, fill=CARD_SHADOW, width=4, tags="bg")
        self.canvas.create_line(0, trim_y, WIDTH, trim_y, fill=GOLD_DARK, width=2, tags="bg")

    def _draw_title(self) -> None:
        canvas = self.canvas
        canvas.delete("title")
        logo = canvas.create_text(
            32, 28, text="TEXAS HOLD'EM", anchor="w", font=("Georgia", -26, "bold italic"), fill=GOLD, tags="title"
        )
        canvas.create_text(
            canvas.bbox(logo)[2] + 14, 30, text=self.t("subtitle"), anchor="w", font=self._font(16), fill=CREAM,
            tags="title",
        )

    def _apply_language(self) -> None:
        self.title(self.t("window_title"))
        self._draw_title()
        self.language_button.set_text(self.t("btn_language"), self._font(14, "bold"))
        if self.screen == "lobby":
            self._draw_lobby_text()
        else:
            self._label_table_buttons()
            self._render()
            self._update_controls()

    def toggle_language(self) -> None:
        self.language = "en" if self.language == "zh" else "zh"
        self._apply_language()

    # Lobby

    def _show_lobby(self, status: tuple[str, dict, bool] | None = None) -> None:
        self.screen = "lobby"
        self.connecting = False
        self.lobby_status = status
        canvas = self.canvas
        canvas.delete("table_ui", "dyn", "sprite", "lobby")
        for widget in self.widgets:
            widget.destroy()
        self.widgets = []

        for x0 in (170, 630):
            create_rounded_rect(canvas, x0, 214, x0 + 400, 566, 18, fill=PANEL, outline=GOLD_DARK, width=2, tags="lobby")
        self._lobby_entry("nickname", 600, 150, 220)
        rows = (("create_port", 330), ("create_password", 375), ("blinds", 420), ("bots", 465))
        for name, y in rows:
            self._lobby_entry(name, 450, y, 150, secret=name.endswith("password"))
        for name, y in (("join_host", 330), ("join_port", 375), ("join_password", 420)):
            self._lobby_entry(name, 910, y, 150, secret=name.endswith("password"))
        self.create_button = CanvasButton(canvas, (370, 522), (190, 46), self.create_room, "primary", tags=("lobby",))
        self.join_button = CanvasButton(canvas, (830, 522), (190, 46), self.join_room, "primary", tags=("lobby",))
        self._apply_language()

    def _lobby_entry(self, name: str, x: float, y: float, width: int, secret: bool = False) -> None:
        entry = tk.Entry(
            self.canvas, textvariable=self.vars[name], justify="center", font=(NUMBER_FONT, -17, "bold"),
            bg="#0f3a24", fg=CREAM, insertbackground=GOLD, relief="flat", highlightthickness=1,
            highlightbackground=GOLD_DARK, highlightcolor=GOLD, show="•" if secret else "",
        )
        self.canvas.create_window(x, y, window=entry, width=width, height=32, tags="lobby")
        self.widgets.append(entry)

    def _draw_lobby_text(self) -> None:
        canvas = self.canvas
        canvas.delete("lobby_text")
        tags = ("lobby", "lobby_text")
        canvas.create_text(480, 150, text=self.t("label_nickname"), anchor="e", font=self._font(17, "bold"), fill=GOLD, tags=tags)
        for x, title, hint in ((370, "btn_create_room", "lobby_create_hint"), (830, "btn_join_room", "lobby_join_hint")):
            canvas.create_text(x, 246, text=self.t(title), font=self._font(22, "bold"), fill=GOLD, tags=tags)
            canvas.create_text(x, 278, text=self.t(hint), font=self._font(13), fill=MUTED, tags=tags)
        labels = (
            (360, 330, "label_port"), (360, 375, "label_password"), (360, 420, "label_blinds"), (360, 465, "label_bots"),
            (820, 330, "label_host"), (820, 375, "label_port"), (820, 420, "label_password"),
        )
        for x, y, key in labels:
            canvas.create_text(x, y, text=self.t(key), anchor="e", font=self._font(15, "bold"), fill=CREAM, tags=tags)
        canvas.create_text(830, 465, text=self.t("lobby_password_hint"), font=self._font(13), fill=MUTED, tags=tags)
        if self.lobby_status:
            key, values, is_error = self.lobby_status
            canvas.create_text(
                WIDTH / 2, 598, text=self.t(key, **values), font=self._font(16, "bold"), fill=ERROR if is_error else CREAM,
                tags=(*tags, "lobby_status"),
            )
        self.create_button.set_text(self.t("btn_create_room"), self._font(17, "bold"))
        self.join_button.set_text(self.t("btn_join_room"), self._font(17, "bold"))
        self.create_button.set_enabled(not self.connecting)
        self.join_button.set_enabled(not self.connecting)

    def _set_lobby_status(self, key: str, is_error: bool = True, **values: object) -> None:
        self.lobby_status = (key, values, is_error)
        self._draw_lobby_text()

    def _read_port(self, name: str) -> int | None:
        text = self.vars[name].get().strip()
        if text.isascii() and text.isdigit() and 1 <= int(text) <= 65535:
            return int(text)
        return None

    def _read_nickname(self) -> str | None:
        nickname = self.vars["nickname"].get().strip()
        if not nickname or len(nickname) > protocol.MAX_NICKNAME or not nickname.isprintable():
            self._set_lobby_status("error_nickname")
            return None
        return nickname

    def create_room(self) -> None:
        nickname = self._read_nickname()
        if nickname is None:
            return
        port = self._read_port("create_port")
        if port is None:
            self._set_lobby_status("error_port")
            return
        try:
            small, big = (int(part) for part in self.vars["blinds"].get().split("/"))
            config = TableConfig(small, big, min_buy_in=20 * big, max_buy_in=100 * big)
        except ValueError:
            self._set_lobby_status("error_blinds")
            return
        bots_text = self.vars["bots"].get().strip()
        if not (bots_text.isascii() and bots_text.isdigit() and int(bots_text) <= 8):
            self._set_lobby_status("error_bots")
            return
        password = self.vars["create_password"].get() or None
        self.server = self.server_factory(config, port, password, int(bots_text))
        self.server.start()
        self.connecting = True
        self._set_lobby_status("status_starting_server", is_error=False)
        self._wait_for_server(nickname, password)

    def _wait_for_server(self, nickname: str, password: str | None) -> None:
        server = self.server
        if server is None:
            return
        if not server.ready.is_set():
            self._schedule(50, lambda: self._wait_for_server(nickname, password))
            return
        if server.error:
            self.server = None
            self.connecting = False
            self._set_lobby_status("error_server_failed", reason=server.error)
            return
        self.room_address = f"{lan_addresses()[0]}:{server.port}"
        self._connect(f"ws://127.0.0.1:{server.port}", nickname, password)

    def join_room(self) -> None:
        nickname = self._read_nickname()
        if nickname is None:
            return
        host = self.vars["join_host"].get().strip()
        port = self._read_port("join_port")
        if port is None:
            self._set_lobby_status("error_port")
            return
        self.room_address = host if "://" in host else f"{host}:{port}"
        url = host if "://" in host else f"ws://{host}:{port}"
        self._connect(url, nickname, self.vars["join_password"].get() or None)

    def _connect(self, url: str, nickname: str, password: str | None) -> None:
        self.connecting = True
        self._session += 1
        self.url, self.nickname, self.password, self.token = url, nickname, password, None
        self.network = self.network_factory()
        self.network.connect(url, protocol.hello(nickname, password))
        self._set_lobby_status("status_connecting", is_error=False)

    def _connection_lost(self) -> None:
        """At the table, keep trying to get back in with our token; give up after a while."""
        self.network = None
        self.turn = None
        if self.reconnect_attempts >= RECONNECT_ATTEMPTS:
            self._leave_room(("error_disconnected", {}, True))
            return
        self.reconnecting = True
        self.reconnect_attempts += 1
        session = self._session
        self._schedule(RECONNECT_MS, lambda: self._reconnect(session))
        self._render()
        self._update_controls()

    def _reconnect(self, session: int) -> None:
        if session != self._session or not self.reconnecting:
            return
        self.network = self.network_factory()
        self.network.connect(self.url, protocol.hello(self.nickname, self.password, self.token))

    def _leave_room(self, status: tuple[str, dict, bool] | None) -> None:
        self._session += 1
        self.reconnecting = False
        self.reconnect_attempts = 0
        self.deadline = None
        if self.network is not None:
            self.network.close()
            self.network = None
        if self.server is not None:
            self.server.stop()
            self.server = None
        self.state = self.view = self.turn = self.config_info = None
        self.queue.clear()
        self.animating = False
        self.hidden.clear()
        self.labels.clear()
        self.status_error = None
        self._show_lobby(status)

    def exit_room(self) -> None:
        self._leave_room(None)

    def close(self) -> None:
        if self.network is not None:
            self.network.close()
        if self.server is not None:
            self.server.stop()
        self.destroy()

    # Messages

    def _poll(self) -> None:
        try:
            while self.network is not None:
                try:
                    message = self.network.incoming.get_nowait()
                except queue.Empty:
                    break
                self.handle_message(message)
        finally:
            self.after(POLL_MS, self._poll)

    def handle_message(self, message: protocol.Message) -> None:
        kind = message["type"]
        if kind in ("_connect_failed", "_disconnected") and self.screen == "table":
            self._connection_lost()
        elif kind == "_connect_failed":
            self._leave_room(("error_connect_failed", {"reason": message["reason"]}, True))
        elif kind == "_disconnected":
            self._leave_room(("error_disconnected", {}, True))
        elif kind == "error":
            if message["code"] == "replaced":
                self._leave_room(("error_replaced", {}, True))
            elif self.screen == "lobby" or self.reconnecting:
                # The server refused the hello and is about to hang up.
                key = JOIN_ERRORS.get(message["code"])
                status = (key, {}, True) if key else ("error_connect_failed", {"reason": message["message"]}, True)
                self._leave_room(status)
            else:
                self._show_error(message["message"])
        elif kind == "welcome":
            self.config_info = message["config"]
            self.token = message["token"]
            if self.screen == "table":
                # Back in after a dropped connection; the table_state that follows brings us up to date.
                self.reconnecting = False
                self.reconnect_attempts = 0
                self.labels.clear()
            else:
                self._show_table()
        elif kind == "table_state":
            self.state = message
            hand = message["hand"]
            time_left = hand["time_left"] if hand else None
            self.deadline = self.clock() + time_left if time_left is not None else None
            if not self.animating:
                self._sync_view()
                self._render()
            self._update_controls()
        elif kind == "event":
            self._enqueue(self._steps_for(message["hand"], message["event"]))
        elif kind == "your_turn":
            self.turn = message
            if message["min_raise_to"] is not None:
                self.raise_steps = self._steps_for_turn(message)
                self.raise_index.set(0)
            self._update_controls()

    def _show_error(self, text: str) -> None:
        self.status_error = text
        self._error_token += 1
        token = self._error_token

        def clear() -> None:
            if token == self._error_token:
                self.status_error = None
                self._render()

        self._schedule(ERROR_MS, clear)
        self._render()

    # Table screen

    def _show_table(self) -> None:
        canvas = self.canvas
        canvas.delete("lobby")
        for widget in self.widgets:
            widget.destroy()
        self.widgets = []
        self.screen = "table"
        self.connecting = False
        tags = ("table_ui",)
        self.rebuy_button = CanvasButton(canvas, (836, 28), (120, 32), self.rebuy, "ghost", tags)
        self.leave_button = CanvasButton(canvas, (950, 28), (90, 32), self.leave_seat, "ghost", tags)
        self.exit_button = CanvasButton(canvas, (1046, 28), (90, 32), self.exit_room, "ghost", tags)
        self.away_button = CanvasButton(canvas, (492, CONTROL_Y), (100, 40), self.toggle_away, "ghost", tags)
        self.fold_button = CanvasButton(canvas, (612, CONTROL_Y), (104, 46), self.fold, "secondary", tags)
        self.call_button = CanvasButton(canvas, (737, CONTROL_Y), (132, 46), self.check_or_call, "secondary", tags)
        self.raise_button = CanvasButton(canvas, (884, CONTROL_Y), (150, 46), self.bet_or_raise, "primary", tags)
        self.preset_buttons = [
            CanvasButton(canvas, (962 + 68 * index, 604), (62, 28), lambda f=fraction: self.preset(f), "ghost", tags)
            for index, (_, fraction) in enumerate(PRESETS)
        ]
        self.raise_scale = tk.Scale(
            canvas, from_=0, to=1, orient=tk.HORIZONTAL, variable=self.raise_index, showvalue=False, length=210,
            command=lambda _value: self._update_controls(), bg=PANEL, troughcolor="#0f3a24", activebackground=GOLD,
            highlightthickness=0, sliderrelief="flat", bd=0,
        )
        canvas.create_window(1076, CONTROL_Y, window=self.raise_scale, tags="table_ui")
        self.widgets.append(self.raise_scale)
        self._label_table_buttons()
        self._render()
        self._update_controls()

    def _label_table_buttons(self) -> None:
        if self.screen != "table":
            return
        small = self._font(13, "bold")
        self.rebuy_button.set_text(self.t("btn_rebuy"), small)
        self.leave_button.set_text(self.t("btn_leave"), small)
        self.exit_button.set_text(self.t("btn_exit"), small)
        self.fold_button.set_text(self.t("btn_fold"), self._font(17, "bold"))
        for button, (key, _) in zip(self.preset_buttons, PRESETS):
            button.set_text(self.t(key), self._font(12, "bold"))

    def _my_seat(self) -> protocol.Message | None:
        if self.state is None or self.state["your_seat"] is None:
            return None
        return self.state["seats"][self.state["your_seat"]]

    def _active_turn(self) -> protocol.Message | None:
        if self.turn is None or self.animating or self.state is None or self.reconnecting:
            return None
        hand = self.state["hand"]
        if hand is None or hand["to_act"] != self.turn["seat"] or self.state["your_seat"] != self.turn["seat"]:
            return None
        return self.turn

    def _update_controls(self) -> None:
        if self.screen != "table":
            return
        turn = self._active_turn()
        actions = set(turn["actions"]) if turn else set()
        self.fold_button.set_enabled("fold" in actions)
        call_font = self._font(16, "bold")
        if "check" in actions or not turn:
            self.call_button.set_text(self.t("btn_check"), call_font)
            self.call_button.set_enabled("check" in actions)
        else:
            self.call_button.set_text(self.t("btn_call", amount=turn["call_amount"]), call_font)
            self.call_button.set_enabled(True)

        can_raise = bool(actions & {"bet", "raise"})
        key = "btn_bet" if "bet" in actions else "btn_raise"
        if can_raise:
            steps = self._steps_for_turn(turn)
            if steps != self.raise_steps:
                amount = self._raise_amount()
                self.raise_steps = steps
                self._set_raise_amount(amount)
            # Only an all-in is possible when there is a single step; the slider has nothing to choose then.
            self.raise_scale.config(from_=0, to=len(steps) - 1, state=tk.NORMAL if len(steps) > 1 else tk.DISABLED)
            index = min(max(self.raise_index.get(), 0), len(steps) - 1)
            if index != self.raise_index.get():
                self.raise_index.set(index)
            amount = steps[index]
            label = self.t("label_all_in", amount=amount) if amount == turn["max_raise_to"] else self.t(key, amount=amount)
            self.raise_button.set_text(label, self._font(15, "bold"))
        else:
            self.raise_scale.config(state=tk.DISABLED)
            self.raise_button.set_text(self.t(key, amount="").strip(), self._font(15, "bold"))
        self.raise_button.set_enabled(can_raise)
        for button in self.preset_buttons:
            button.set_enabled(can_raise)

        seat = self._my_seat()
        in_hand = seat is not None and seat["in_hand"]
        max_buy_in = self.config_info["max_buy_in"] if self.config_info else 0
        online = not self.reconnecting
        self.rebuy_button.set_enabled(online and seat is not None and not in_hand and seat["stack"] < max_buy_in)
        self.leave_button.set_enabled(online and seat is not None)
        away = seat is not None and seat["away"]
        self.away_button.set_text(self.t("btn_sit_back" if away else "btn_sit_out"), self._font(14, "bold"))
        self.away_button.set_enabled(online and seat is not None)

    def _steps_for_turn(self, turn: protocol.Message) -> list[int]:
        big_blind = self.config_info["big_blind"] if self.config_info else 1
        return bet_steps(turn["min_raise_to"], turn["max_raise_to"], big_blind)

    def _raise_amount(self) -> int:
        index = min(max(self.raise_index.get(), 0), len(self.raise_steps) - 1)
        return self.raise_steps[index]

    def _set_raise_amount(self, amount: int) -> None:
        self.raise_index.set(nearest_step(self.raise_steps, amount))

    # Table actions

    def _send(self, message: protocol.Message) -> None:
        if self.network is not None:
            self.network.send(message)

    def _send_action(self, action: str, amount: int | None = None) -> None:
        if self._active_turn() is None:
            return
        self._send(protocol.action(action, amount))
        self.turn = None
        self._update_controls()

    def fold(self) -> None:
        self._send_action("fold")

    def check_or_call(self) -> None:
        turn = self._active_turn()
        if turn is not None:
            self._send_action("check" if "check" in turn["actions"] else "call")

    def bet_or_raise(self) -> None:
        turn = self._active_turn()
        if turn is not None:
            self._send_action("bet" if "bet" in turn["actions"] else "raise", self._raise_amount())

    def preset(self, fraction: float | None) -> None:
        turn = self._active_turn()
        if turn is None or turn["min_raise_to"] is None:
            return
        low, high = turn["min_raise_to"], turn["max_raise_to"]
        if fraction is None:
            amount = high
        else:
            seats = [seat for seat in self.state["seats"] if seat is not None]
            pot = sum(pot["amount"] for pot in self.state["hand"]["pots"]) + sum(seat["bet"] for seat in seats)
            my_bet = self.state["seats"][turn["seat"]]["bet"]
            amount = preset_amount(fraction, pot, my_bet, turn["call_amount"], low, high)
        self._set_raise_amount(amount)
        self._update_controls()

    def click_seat(self, index: int) -> None:
        if self.state is None or self.state["your_seat"] is not None or self.config_info is None:
            return
        low, high = self.config_info["min_buy_in"], self.config_info["max_buy_in"]
        amount = self.ask_integer(self.t("dialog_buy_in_title"), self.t("dialog_buy_in", min=low, max=high), low, high, high)
        if amount is not None:
            self._send(protocol.sit(index, amount))

    def rebuy(self) -> None:
        seat = self._my_seat()
        if seat is None or self.config_info is None:
            return
        room = self.config_info["max_buy_in"] - seat["stack"]
        if room < 1:
            return
        amount = self.ask_integer(self.t("dialog_rebuy_title"), self.t("dialog_rebuy", max=room), 1, room, room)
        if amount is not None:
            self._send(protocol.rebuy(amount))

    def leave_seat(self) -> None:
        if self._my_seat() is not None:
            self._send(protocol.leave())

    def toggle_away(self) -> None:
        seat = self._my_seat()
        if seat is not None:
            self._send(protocol.away(not seat["away"]))

    # Events and animation

    def _sync_view(self) -> None:
        self.view = copy.deepcopy(self.state)

    def _enqueue(self, steps: list[Step]) -> None:
        if not steps:
            return
        self.queue.extend(steps)
        if not self.animating:
            self.animating = True
            self._update_controls()
            # Start on a later tick so a table_state polled in the same batch is kept for the end.
            self._schedule(0, self._next_step)

    def _next_step(self) -> None:
        if self.queue:
            self.queue.popleft()(lambda: self._schedule(self.gap_ms, self._next_step))
            return
        self.animating = False
        self.hidden.clear()
        self.canvas.delete("sprite")
        self._sync_view()
        self._render()
        self._update_controls()

    def _instant(self, change: Callable[[], None]) -> Step:
        def step(done: Callable[[], None]) -> None:
            if self.view is not None:
                change()
                self._render()
            done()

        return step

    def _view_seat(self, index: int) -> protocol.Message | None:
        return self.view["seats"][index] if self.view is not None else None

    def _steps_for(self, hand_number: int, event: protocol.Message) -> list[Step]:
        kind = event["type"]
        seat_index = event.get("seat")

        if kind == "hand_start":
            def start() -> None:
                self.labels.clear()
                self.view["hand"] = {
                    "number": hand_number, "street": "preflop", "board": [], "pots": [], "current_bet": 0,
                    "to_act": None, "button": event["button"], "small_blind": event["small_blind"],
                    "big_blind": event["big_blind"], "complete": False,
                }
                for seat in self.view["seats"]:
                    if seat is not None:
                        seat.update(bet=0, cards=None, in_hand=False, folded=False, all_in=False)
            return [self._instant(start)]

        if kind in ("blind", "action"):
            def act() -> None:
                seat = self._view_seat(seat_index)
                if seat is None:
                    return
                if kind == "blind":
                    key = "label_small_blind" if event["blind"] == "small" else "label_big_blind"
                    seat["bet"] += event["amount"]
                    self.labels[seat_index] = (self.t(key, amount=event["amount"]), "neutral")
                elif event["action"] == "fold":
                    seat["folded"] = True
                    self.labels[seat_index] = (self.t("btn_fold"), "neutral")
                elif event["action"] == "check":
                    self.labels[seat_index] = (self.t("btn_check"), "neutral")
                else:
                    seat["bet"] = event["to"]
                    amount = event["amount"] if event["action"] == "call" else event["to"]
                    key = "label_all_in" if event["all_in"] else f"btn_{event['action']}"
                    self.labels[seat_index] = (self.t(key, amount=amount), "neutral")
                seat["stack"] -= event["amount"]
                seat["all_in"] = event["all_in"]
            return [self._instant(act)]

        if kind == "hole_cards":
            return [self._deal_hole_step(seat_index, event.get("cards"))]
        if kind == "street":
            return [self._collect_step(), self._instant(self.labels.clear), self._board_step(event["cards"])]
        if kind == "uncalled_returned":
            def refund() -> None:
                seat = self._view_seat(seat_index)
                seat["bet"] -= event["amount"]
                seat["stack"] += event["amount"]
            return [self._instant(refund)]
        if kind == "showdown":
            return [self._collect_step(), self._show_step(seat_index, event["cards"], self.t(event["hand"]))]
        if kind == "award":
            return [self._collect_step(), self._award_step(seat_index, event["amount"])]
        if kind == "hand_end":
            return [self._instant(lambda: self.view["hand"] and self.view["hand"].update(complete=True))]
        return []

    def _positions(self) -> list[Point]:
        view = self.view
        return seat_positions(len(view["seats"]), view["your_seat"])

    def _hole_points(self, index: int) -> list[Point]:
        x, y = self._positions()[index]
        return [(x + dx, y + dy) for dx, dy in HOLE_OFFSETS]

    def _deal_hole_step(self, index: int, cards: list[str] | None) -> Step:
        def step(done: Callable[[], None]) -> None:
            seat = self._view_seat(index)
            if seat is None:
                done()
                return
            targets = self._hole_points(index)

            def land() -> None:
                seat["in_hand"] = True
                seat["cards"] = cards
                self.hidden.discard(("seat", index))
                self._land(done)

            def arrived() -> None:
                if cards:
                    self._flip([(point, code, True) for point, code in zip(targets, cards)], land)
                else:
                    land()

            self.hidden.add(("seat", index))
            self._slide([(DECK_POINT, target, self._card_sprite(None, True)) for target in targets], arrived)

        return step

    def _board_point(self, slot: int) -> Point:
        return TABLE_CENTER[0] + (slot - 2) * BOARD_STEP, BOARD_Y

    def _board_step(self, cards: list[str]) -> Step:
        def step(done: Callable[[], None]) -> None:
            board = self.view["hand"]["board"] if self.view and self.view["hand"] else None
            if board is None:
                done()
                return
            targets = [self._board_point(len(board) + offset) for offset in range(len(cards))]

            def land() -> None:
                board.extend(cards)
                self._land(done)

            self._slide(
                [(DECK_POINT, target, self._card_sprite(None, False)) for target in targets],
                lambda: self._flip([(target, code, False) for target, code in zip(targets, cards)], land),
            )

        return step

    def _collect_step(self) -> Step:
        """Sweep every bet in front of the players into the pot."""

        def step(done: Callable[[], None]) -> None:
            if self.view is None or self.view["hand"] is None:
                done()
                return
            positions = self._positions()
            bets = [(index, seat["bet"]) for index, seat in enumerate(self.view["seats"]) if seat and seat["bet"] > 0]
            if not bets:
                done()
                return
            moves = [(toward_center(positions[index], BET_FRACTION), (TABLE_CENTER[0], POT_Y), self._chip_sprite(bet)) for index, bet in bets]
            for index, _ in bets:
                self.view["seats"][index]["bet"] = 0
            self._render()

            def land() -> None:
                pots = self.view["hand"]["pots"]
                total = sum(pot["amount"] for pot in pots) + sum(bet for _, bet in bets)
                self.view["hand"]["pots"] = [{"amount": total, "eligible": []}]
                self._land(done)

            self._slide(moves, land)

        return step

    def _show_step(self, index: int, cards: list[str], hand_name: str) -> Step:
        def step(done: Callable[[], None]) -> None:
            seat = self._view_seat(index)
            if seat is None:
                done()
                return

            def land() -> None:
                seat["cards"] = cards
                self.labels[index] = (hand_name, "neutral")
                self.hidden.discard(("seat", index))
                self._land(done)

            if seat["cards"] == cards:
                land()
                return
            self.hidden.add(("seat", index))
            self._render()
            self._flip([(point, code, True) for point, code in zip(self._hole_points(index), cards)], land)

        return step

    def _award_step(self, index: int, amount: int) -> Step:
        def step(done: Callable[[], None]) -> None:
            seat = self._view_seat(index)
            if seat is None or self.view["hand"] is None:
                done()
                return
            pots = self.view["hand"]["pots"]
            total = sum(pot["amount"] for pot in pots)
            self.view["hand"]["pots"] = [{"amount": max(total - amount, 0), "eligible": []}] if total > amount else []
            target = toward_center(self._positions()[index], BET_FRACTION)
            self._render()

            def land() -> None:
                seat["stack"] += amount
                name = self.labels.get(index, ("", ""))[0]
                won = self.t("label_win", amount=amount)
                self.labels[index] = (f"{name} · {won}" if name else won, "win")
                self._land(done)

            self._slide([((TABLE_CENTER[0], POT_Y), target, self._chip_sprite(amount))], land)

        return step

    def _land(self, done: Callable[[], None]) -> None:
        self.canvas.delete("sprite")
        self._render()
        done()

    def _card_sprite(self, code: str | None, small: bool) -> Callable[[float, float], None]:
        return lambda x, y: self._draw_card(x, y, code, small, tags=("sprite",))

    def _chip_sprite(self, amount: int) -> Callable[[float, float], None]:
        return lambda x, y: draw_chip_stack(self.canvas, x, y, amount, ("sprite",), radius=13, thickness=4)

    def _slide(self, moves: list[tuple[Point, Point, Callable[[float, float], None]]], done: Callable[[], None]) -> None:
        def frame(index: int) -> None:
            t = ease_out_cubic(index / self.slide_frames)
            self.canvas.delete("sprite")
            for (start_x, start_y), (end_x, end_y), draw in moves:
                draw(start_x + (end_x - start_x) * t, start_y + (end_y - start_y) * t)
            if index < self.slide_frames:
                self._schedule(self.frame_ms, lambda: frame(index + 1))
            else:
                done()

        frame(0)

    def _flip(self, cards: list[tuple[Point, str, bool]], done: Callable[[], None]) -> None:
        frames = [(scale, False) for scale in FLIP_SCALES] + [(scale, True) for scale in reversed(FLIP_SCALES)]

        def frame(index: int) -> None:
            self.canvas.delete("sprite")
            if index == len(frames):
                done()
                return
            scale, face_up = frames[index]
            for (x, y), code, small in cards:
                self._draw_card(x, y, code if face_up else None, small, scale, ("sprite",))
            self._schedule(self.frame_ms, lambda: frame(index + 1))

        frame(0)

    # Rendering

    def _photo(self, code: str | None, small: bool, scale: float = 1.0) -> tk.PhotoImage:
        path = card_image_path(Card.from_code(code), small=small) if code else card_back_path(small=small)
        level = round(scale * 5)
        key = (str(path), level)
        if key not in self.photos:
            if level == 5:
                data = base64.b64encode(path.read_bytes()).decode("ascii")
                self.photos[key] = tk.PhotoImage(master=self, data=data, format="png")
            else:
                self.photos[key] = self._photo(code, small).zoom(level, 1).subsample(5, 1)
        return self.photos[key]

    def _draw_card(
        self, x: float, y: float, code: str | None, small: bool, scale: float = 1.0, tags: tuple[str, ...] = ("dyn",)
    ) -> None:
        width, height = (SMALL_W, SMALL_H) if small else (CARD_W, CARD_H)
        if scale == 1.0:
            create_rounded_rect(
                self.canvas, x - width / 2 + 2, y - height / 2 + 3, x + width / 2 + 2, y + height / 2 + 3, 6,
                fill=CARD_SHADOW, outline="", tags=tags,
            )
        self.canvas.create_image(x, y, image=self._photo(code, small, scale), tags=tags)

    def _render(self) -> None:
        canvas = self.canvas
        canvas.delete("dyn")
        if self.screen != "table" or self.view is None:
            return
        self._draw_room_info()
        self._draw_board()
        positions = self._positions()
        for index, seat in enumerate(self.view["seats"]):
            if seat is None:
                self._draw_empty_seat(index, positions[index])
            else:
                self._draw_seat(index, seat, positions[index])
        self._draw_balance()
        self._draw_status()
        self._draw_timer()
        canvas.tag_raise("sprite")

    def _timer_seat(self) -> int | None:
        """The seat whose action timer is shown: the one to act, once the animations have caught up."""
        if self.screen != "table" or self.animating or self.deadline is None or self.state is None:
            return None
        hand = self.state["hand"]
        if hand is None or hand["complete"] or hand["to_act"] is None or self.view is None:
            return None
        return hand["to_act"] if self.view["seats"][hand["to_act"]] is not None else None

    def _draw_timer(self) -> None:
        self.canvas.delete("timer")
        index = self._timer_seat()
        if index is None or self.config_info is None:
            return
        fraction = min(max((self.deadline - self.clock()) / self.config_info["action_timeout"], 0.0), 1.0)
        x, y = self._positions()[index]
        tags = ("dyn", "timer")
        self.canvas.create_rectangle(x - 58, y + 33, x + 58, y + 36, fill=RAIL_DARK, outline="", tags=tags)
        if fraction > 0:
            self.canvas.create_rectangle(
                x - 58, y + 33, x - 58 + 116 * fraction, y + 36, fill=lerp_color(LOSE, GOLD, fraction), outline="",
                tags=tags,
            )

    def _tick_timer(self) -> None:
        try:
            self._draw_timer()
            self.canvas.tag_raise("sprite")
        finally:
            self.after(TIMER_MS, self._tick_timer)

    def _draw_room_info(self) -> None:
        if self.config_info is None:
            return
        parts = [self.t("blinds", small=self.config_info["small_blind"], big=self.config_info["big_blind"])]
        if self.room_address:
            parts.append(self.t("room_address", address=self.room_address))
        # Right-aligned against the rail buttons so it never runs into the title.
        self.canvas.create_text(
            762, 30, text="   ·   ".join(parts), anchor="e", font=self._font(14), fill=CREAM, tags="dyn",
        )

    def _draw_board(self) -> None:
        hand = self.view["hand"]
        if hand is None:
            return
        for slot, code in enumerate(hand["board"]):
            x, y = self._board_point(slot)
            self._draw_card(x, y, code, small=False)
        pots = [pot["amount"] for pot in hand["pots"] if pot["amount"] > 0]
        if pots:
            parts = [self.t("pot", amount=pots[0])] + [self.t("side_pot", amount=amount) for amount in pots[1:]]
            create_pill(
                self.canvas, TABLE_CENTER[0], POT_Y, "   ".join(parts), self._font(15, "bold"), PANEL, GOLD_DARK, GOLD,
                ("dyn",), padding=(16, 4),
            )

    def _draw_empty_seat(self, index: int, position: Point) -> None:
        canvas = self.canvas
        x, y = position
        clickable = self.state is not None and self.state["your_seat"] is None
        tag = f"sit-{index}"
        tags = ("dyn", tag)
        canvas.create_oval(x - 52, y - 22, x + 52, y + 26, outline=GOLD_DARK, width=2, dash=(4, 3), fill=PANEL, tags=tags)
        canvas.create_text(x, y - 6 if clickable else y + 2, text=self.t("seat_empty"), font=self._font(13, "bold"), fill=MUTED, tags=tags)
        if clickable:
            canvas.create_text(x, y + 12, text=self.t("seat_click_to_sit"), font=self._font(12), fill=GOLD, tags=tags)
            canvas.tag_bind(tag, "<Button-1>", lambda _event, seat=index: self.click_seat(seat))
            canvas.tag_bind(tag, "<Enter>", lambda _event: canvas.config(cursor="hand2"))
            canvas.tag_bind(tag, "<Leave>", lambda _event: canvas.config(cursor=""))
        else:
            canvas.tag_unbind(tag, "<Button-1>")

    def _draw_seat(self, index: int, seat: protocol.Message, position: Point) -> None:
        canvas = self.canvas
        x, y = position
        hand = self.view["hand"]
        live = hand is not None and not hand["complete"]
        to_act = live and self.state is not None and self.state["hand"] is not None and not self.animating \
            and self.state["hand"]["to_act"] == index
        mine = index == self.view["your_seat"]

        if ("seat", index) not in self.hidden:
            if seat["cards"]:
                for (cx, cy), code in zip(self._hole_points(index), seat["cards"]):
                    self._draw_card(cx, cy, code, small=True)
            elif seat["in_hand"] and not seat["folded"]:
                for cx, cy in self._hole_points(index):
                    self._draw_card(cx, cy, None, small=True)

        offline = not seat["connected"]
        dim = seat["folded"] or seat["away"] or offline
        create_rounded_rect(
            canvas, x - 66, y - 11, x + 66, y + 39, 10, fill=PANEL,
            outline=GOLD if to_act else (GOLD if mine else GOLD_DARK), width=3 if to_act else 1, tags=("dyn",),
        )
        note = "seat_offline" if offline else "seat_away" if seat["away"] else None
        name = seat["name"] + (f" ({self.t(note)})" if note else "")
        canvas.create_text(x, y + 3, text=name, font=self._font(14, "bold"), fill=MUTED if dim else CREAM, tags="dyn")
        canvas.create_text(
            x, y + 24, text=str(seat["stack"]), font=(NUMBER_FONT, -15, "bold"), fill=MUTED if dim else GOLD, tags="dyn"
        )

        label = self.labels.get(index)
        if label:
            text, tone = label
            color = WIN if tone == "win" else CREAM
            create_pill(canvas, x, y + 56, text, self._font(12, "bold"), PANEL, GOLD_DARK if tone != "win" else WIN, color, ("dyn",), padding=(10, 2))

        if seat["bet"] > 0:
            bx, by = toward_center(position, BET_FRACTION)
            draw_chip_stack(canvas, bx - 14, by + 6, seat["bet"], ("dyn",), radius=13, thickness=4)
            canvas.create_text(bx + 4, by, text=str(seat["bet"]), anchor="w", font=(NUMBER_FONT, -14, "bold"), fill=CREAM, tags="dyn")

        if hand is not None and hand["button"] == index:
            # Beside the nameplate, clear of the hole cards.
            dx, dy = x + (86 if x <= TABLE_CENTER[0] else -86), y + 14
            canvas.create_oval(dx - 12, dy - 12, dx + 12, dy + 12, fill="#f6efd9", outline="#a8862f", width=2, tags="dyn")
            canvas.create_text(dx, dy, text="D", font=("Georgia", -13, "bold"), fill="#2a1a08", tags="dyn")

    def _draw_balance(self) -> None:
        seat = self._my_seat()
        if seat is None:
            return
        canvas = self.canvas
        draw_chip(canvas, 52, CONTROL_Y, 20, CHIP_STYLES[1], ("dyn",))
        canvas.create_text(84, CONTROL_Y - 13, text=self.t("chips"), anchor="w", font=self._font(13, "bold"), fill=GOLD, tags="dyn")
        canvas.create_text(84, CONTROL_Y + 11, text=str(seat["stack"]), anchor="w", font=(NUMBER_FONT, -24, "bold"), fill=CREAM, tags="dyn")

    def _status(self) -> tuple[str, str]:
        if self.reconnecting:
            return self.t("status_reconnecting"), ERROR
        if self.status_error:
            return self.status_error, ERROR
        state = self.state
        if state is None:
            return "", CREAM
        seat = self._my_seat()
        if seat is None:
            return self.t("status_pick_seat"), CREAM
        hand = state["hand"]
        if hand is not None and not hand["complete"]:
            # Also while the last animations play; the buttons unlock once they finish.
            if hand["to_act"] is not None and hand["to_act"] == state["your_seat"]:
                return self.t("status_your_turn"), GOLD
            if hand["to_act"] is not None and state["seats"][hand["to_act"]] is not None:
                return self.t("status_waiting_for", name=state["seats"][hand["to_act"]]["name"]), CREAM
        if seat["stack"] == 0:
            return self.t("status_sitting_out"), CREAM
        if seat["away"]:
            return self.t("status_away"), GOLD
        ready = sum(1 for other in state["seats"] if other is not None and other["stack"] > 0 and not other["away"])
        if ready < 2:
            return self.t("status_waiting"), CREAM
        return "", CREAM

    def _draw_status(self) -> None:
        text, color = self._status()
        if text:
            self.canvas.create_text(
                212, CONTROL_Y, text=text, anchor="w", width=220, font=self._font(15, "bold"), fill=color,
                tags=("dyn", "status"),
            )


def main() -> None:
    app = HoldemApp()
    app.mainloop()


if __name__ == "__main__":
    main()
