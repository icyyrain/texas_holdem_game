"""Minimal terminal client for trying a server from several windows.

    python -m holdem.textclient --name alice [--host 127.0.0.1] [--port 8765] [--password xxx]

Seats are numbered from 1 here; the protocol numbers them from 0.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import threading

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from holdem import protocol
from holdem.sim import ALIASES, QUIT_WORDS, cards_text, describe_event, describe_options, parse_action

HELP = (
    "Commands: sit SEAT BUYIN · leave · rebuy AMOUNT · away · back · state · help · quit. "
    "On your turn: f, x, c, b N, r N, a."
)


class TextClient:
    """Turns server messages into lines of text, and typed commands into client messages."""

    def __init__(self) -> None:
        self.state: protocol.Message | None = None
        self.turn: protocol.Message | None = None
        self.config: protocol.Message | None = None

    def names(self) -> dict[int, str]:
        if self.state is None:
            return {}
        return {seat["seat"]: seat["name"] for seat in self.state["seats"] if seat is not None}

    def on_message(self, message: protocol.Message) -> list[str]:
        kind = message["type"]
        if kind == "welcome":
            self.config = config = message["config"]
            return [
                f"Connected. Blinds {config['small_blind']}/{config['big_blind']}, buy-in "
                f"{config['min_buy_in']}-{config['max_buy_in']}, {config['max_seats']} seats.",
                HELP,
            ]
        if kind == "table_state":
            first = self.state is None
            self.state = message
            hand = message["hand"]
            if hand is None or hand["to_act"] != message["your_seat"]:
                self.turn = None
            return self.render_state() if first else []
        if kind == "event":
            event = message["event"]
            if event["type"] == "hand_start":
                return ["", f"=== Hand {message['hand']} · button {self.names().get(event['button'], '?')}"]
            line = describe_event(event, self.names())
            return [line] if line else []
        if kind == "your_turn":
            self.turn = message
            return [self.turn_prompt()]
        if kind == "error":
            return [f"! {message['message']}"]
        return []

    def turn_prompt(self) -> str:
        seat = self.state["seats"][self.turn["seat"]] if self.state else None
        cards = f" with {cards_text(seat['cards'])}" if seat and seat["cards"] else ""
        time_left = f" ({self.turn['time_left']:.0f}s)" if self.turn["time_left"] is not None else ""
        return f"Your turn{cards}{time_left}: {describe_options(protocol.legal_from_turn(self.turn))}"

    def render_state(self) -> list[str]:
        if self.state is None:
            return ["Not connected yet."]
        lines = []
        hand = self.state["hand"]
        if hand is not None:
            board = cards_text(hand["board"]) or "(none)"
            pot = sum(pot["amount"] for pot in hand["pots"])
            lines.append(f"Hand {hand['number']} · {hand['street']} · board {board} · pot {pot}")
        for index, seat in enumerate(self.state["seats"]):
            if seat is None:
                lines.append(f"  Seat {index + 1}: empty")
                continue
            notes = [
                note for note, on in (
                    ("you", index == self.state["your_seat"]),
                    ("button", hand is not None and hand["button"] == index),
                    ("to act", hand is not None and hand["to_act"] == index),
                    ("folded", seat["folded"]),
                    ("all-in", seat["all_in"]),
                    ("away", seat["away"]),
                    ("offline", not seat["connected"]),
                    ("leaving", seat["leaving"]),
                ) if on
            ]
            bet = f" bet {seat['bet']}" if seat["bet"] else ""
            cards = f" {cards_text(seat['cards'])}" if seat["cards"] else ""
            extra = f" ({', '.join(notes)})" if notes else ""
            lines.append(f"  Seat {index + 1}: {seat['name']} {seat['stack']}{bet}{cards}{extra}")
        return lines

    def command(self, text: str) -> tuple[protocol.Message | None, list[str]]:
        """A message to send (or None) and lines to show for one typed command."""
        words = text.split()
        if not words:
            return None, []
        head = words[0].lower()
        if head in ("help", "?"):
            return None, [HELP]
        if head == "state":
            return None, self.render_state()
        if head == "leave":
            return protocol.leave(), []
        if head in ("away", "back"):
            return protocol.away(head == "away"), []
        if head in ("sit", "rebuy"):
            numbers = words[1:]
            expected = 2 if head == "sit" else 1
            if len(numbers) != expected or not all(word.isascii() and word.isdigit() for word in numbers):
                return None, ["Usage: sit SEAT BUYIN" if head == "sit" else "Usage: rebuy AMOUNT"]
            if head == "sit":
                return protocol.sit(int(numbers[0]) - 1, int(numbers[1])), []
            return protocol.rebuy(int(numbers[0])), []
        if head in ALIASES:
            if self.turn is None:
                return None, ["It is not your turn."]
            try:
                action, amount = parse_action(text, protocol.legal_from_turn(self.turn))
            except ValueError as error:
                return None, [str(error)]
            return protocol.action(action, amount), []
        return None, [f"Unknown command. {HELP}"]


async def run(url: str, name: str, password: str | None, lines: asyncio.Queue, out=print) -> int:
    """Play until the user quits (a None or quit line) or the server goes away."""
    client = TextClient()
    try:
        websocket = await connect(url)
    except OSError as error:
        out(f"Cannot connect to {url}: {error}")
        return 1

    async def read() -> None:
        try:
            async for text in websocket:
                for line in client.on_message(protocol.decode_server(text)):
                    out(line)
        except ConnectionClosed:
            pass

    async with websocket:
        await websocket.send(protocol.encode(protocol.hello(name, password)))
        reader = asyncio.create_task(read())
        while True:
            typed = asyncio.create_task(lines.get())
            await asyncio.wait({typed, reader}, return_when=asyncio.FIRST_COMPLETED)
            if not typed.done():
                typed.cancel()
                out("Disconnected from the server.")
                return 1
            text = typed.result()
            if text is None or text.strip().lower() in QUIT_WORDS:
                break
            message, output = client.command(text)
            for line in output:
                out(line)
            if message is not None:
                await websocket.send(protocol.encode(message))
        reader.cancel()
    return 0


def read_stdin(loop: asyncio.AbstractEventLoop, lines: asyncio.Queue) -> None:
    """Feed typed lines into ``lines`` from a daemon thread, so a blocked read never stops exit."""

    def pump() -> None:
        for line in sys.stdin:
            # Windows PowerShell puts a byte-order mark in front of text piped to stdin.
            loop.call_soon_threadsafe(lines.put_nowait, line.rstrip("\r\n").replace("﻿", ""))
        loop.call_soon_threadsafe(lines.put_nowait, None)

    threading.Thread(target=pump, daemon=True).start()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="python -m holdem.textclient", description="Join a Hold'em table from the terminal.")
    parser.add_argument("--name", required=True, help=f"nickname, up to {protocol.MAX_NICKNAME} characters")
    parser.add_argument("--host", default="127.0.0.1", help="server address, or a full ws:// / wss:// URL")
    parser.add_argument("--port", type=int, default=protocol.DEFAULT_PORT)
    parser.add_argument("--password")
    args = parser.parse_args()
    url = args.host if "://" in args.host else f"ws://{args.host}:{args.port}"

    async def start() -> int:
        lines: asyncio.Queue = asyncio.Queue()
        read_stdin(asyncio.get_running_loop(), lines)
        return await run(url, args.name, args.password, lines, out=lambda line: print(line, flush=True))

    try:
        raise SystemExit(asyncio.run(start()))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
