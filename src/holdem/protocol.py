"""Wire format: one JSON object per WebSocket message, each with a ``type``.

Client messages are validated strictly (known type, exact fields, right types) because they
come from the network. Server messages are built here so both ends agree on their shape.
"""

from __future__ import annotations

import json
from typing import Any

from holdem.core.betting import Action, LegalActions
from holdem.core.table import TableConfig

PROTOCOL_VERSION = 1
DEFAULT_PORT = 8765
MAX_MESSAGE_BYTES = 16 * 1024
MAX_NICKNAME = 20
MAX_SECRET = 64

Message = dict[str, Any]

CLIENT_TYPES = ("hello", "sit", "leave", "action", "rebuy")
SERVER_TYPES = ("welcome", "table_state", "event", "your_turn", "error")


class ProtocolError(ValueError):
    def __init__(self, message: str, code: str = "bad_message"):
        super().__init__(message)
        self.code = code


# Field name -> (allowed types, required). bool is never accepted where int is.
_CLIENT_FIELDS: dict[str, dict[str, tuple[tuple[type, ...], bool]]] = {
    "hello": {
        "version": ((int,), True),
        "nickname": ((str,), True),
        "password": ((str, type(None)), False),
        "token": ((str, type(None)), False),
    },
    "sit": {"seat": ((int,), True), "buy_in": ((int,), True)},
    "leave": {},
    "action": {"action": ((str,), True), "amount": ((int, type(None)), False)},
    "rebuy": {"amount": ((int,), True)},
}


def encode(message: Message) -> str:
    return json.dumps(message, ensure_ascii=False, separators=(",", ":"))


def _parse_object(text: str | bytes) -> Message:
    if isinstance(text, bytes):
        raise ProtocolError("messages must be text")
    if len(text.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise ProtocolError("message is too large")
    try:
        message = json.loads(text)
    except json.JSONDecodeError:
        raise ProtocolError("message is not valid JSON") from None
    if not isinstance(message, dict):
        raise ProtocolError("message must be a JSON object")
    if not isinstance(message.get("type"), str):
        raise ProtocolError("message needs a string type")
    return message


def decode_client(text: str | bytes) -> Message:
    """Parse and validate a message from a client; ProtocolError says what is wrong."""
    message = _parse_object(text)
    kind = message["type"]
    if kind not in _CLIENT_FIELDS:
        raise ProtocolError(f"unknown message type: {kind}")
    fields = _CLIENT_FIELDS[kind]
    extra = set(message) - set(fields) - {"type"}
    if extra:
        raise ProtocolError(f"unexpected fields: {', '.join(sorted(extra))}")
    for name, (types, required) in fields.items():
        if name not in message:
            if required:
                raise ProtocolError(f"{kind} needs {name}")
            message[name] = None
            continue
        value = message[name]
        if isinstance(value, bool) or not isinstance(value, types):
            raise ProtocolError(f"{kind}.{name} has the wrong type")

    if kind == "hello":
        nickname = message["nickname"]
        if not nickname.strip() or len(nickname) > MAX_NICKNAME or not nickname.isprintable():
            raise ProtocolError(f"nickname must be 1 to {MAX_NICKNAME} printable characters")
        for name in ("password", "token"):
            if message[name] is not None and len(message[name]) > MAX_SECRET:
                raise ProtocolError(f"{name} is too long")
    if kind == "action" and message["action"] not in {action.value for action in Action}:
        raise ProtocolError(f"unknown action: {message['action']}")
    return message


def decode_server(text: str | bytes) -> Message:
    """Parse a message from the server, checking only that its type is known."""
    message = _parse_object(text)
    if message["type"] not in SERVER_TYPES:
        raise ProtocolError(f"unknown message type: {message['type']}")
    return message


# Client -> server


def hello(nickname: str, password: str | None = None, token: str | None = None) -> Message:
    return {"type": "hello", "version": PROTOCOL_VERSION, "nickname": nickname, "password": password, "token": token}


def sit(seat: int, buy_in: int) -> Message:
    return {"type": "sit", "seat": seat, "buy_in": buy_in}


def leave() -> Message:
    return {"type": "leave"}


def action(action: Action | str, amount: int | None = None) -> Message:
    return {"type": "action", "action": Action(action).value, "amount": amount}


def rebuy(amount: int) -> Message:
    return {"type": "rebuy", "amount": amount}


# Server -> client


def welcome(player_id: str, token: str, config: TableConfig) -> Message:
    return {
        "type": "welcome",
        "version": PROTOCOL_VERSION,
        "player_id": player_id,
        "token": token,
        "config": {
            "small_blind": config.small_blind,
            "big_blind": config.big_blind,
            "min_buy_in": config.min_buy_in,
            "max_buy_in": config.max_buy_in,
            "max_seats": config.max_seats,
            "action_timeout": config.action_timeout,
        },
    }


def table_state(your_seat: int | None, seats: list[Message | None], hand: Message | None) -> Message:
    """A full snapshot from one player's point of view: only their own hole cards, plus cards shown at showdown."""
    return {"type": "table_state", "your_seat": your_seat, "seats": seats, "hand": hand}


def event(hand_number: int, event: Message) -> Message:
    return {"type": "event", "hand": hand_number, "event": event}


def your_turn(hand_number: int, seat: int, legal: LegalActions, deadline: float | None = None) -> Message:
    return {
        "type": "your_turn",
        "hand": hand_number,
        "seat": seat,
        "actions": sorted(action.value for action in legal.actions),
        "call_amount": legal.call_amount,
        "min_raise_to": legal.min_raise_to,
        "max_raise_to": legal.max_raise_to,
        "deadline": deadline,
    }


def legal_from_turn(message: Message) -> LegalActions:
    """Rebuild LegalActions from a your_turn message (client side)."""
    return LegalActions(
        frozenset(Action(name) for name in message["actions"]),
        message["call_amount"],
        message["min_raise_to"],
        message["max_raise_to"],
    )


def error(code: str, message: str) -> Message:
    return {"type": "error", "code": code, "message": message}
