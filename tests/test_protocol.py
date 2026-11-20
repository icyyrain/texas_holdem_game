import json

import pytest

from holdem import protocol
from holdem.core.betting import Action, LegalActions
from holdem.core.table import TableConfig


@pytest.mark.parametrize(
    "message",
    [
        protocol.hello("alice"),
        protocol.hello("鲍勃", password="secret", token="abc"),
        protocol.create_room("周五局", 1, 2),
        protocol.create_room("High stakes", 50, 100, password="pw", bots=3),
        protocol.join_room("ABC234"),
        protocol.join_room("ABC234", "pw"),
        protocol.leave_room(),
        protocol.sit(3, 200),
        protocol.leave(),
        protocol.action(Action.FOLD),
        protocol.action("raise", 40),
        protocol.rebuy(100),
        protocol.away(True),
        protocol.away(False),
    ],
)
def test_client_messages_round_trip(message):
    assert protocol.decode_client(protocol.encode(message)) == message


def test_optional_client_fields_default_to_none():
    assert protocol.decode_client('{"type":"hello","version":3,"nickname":"a"}') == protocol.hello("a")
    assert protocol.decode_client('{"type":"join_room","room":"ABC234"}') == protocol.join_room("ABC234")
    assert protocol.decode_client('{"type":"action","action":"call"}') == protocol.action("call")


LEGAL = LegalActions(frozenset({Action.FOLD, Action.CALL, Action.RAISE}), call_amount=2, min_raise_to=4, max_raise_to=100)


@pytest.mark.parametrize(
    "message",
    [
        protocol.welcome("p1", "tok"),
        protocol.rooms([{"id": "ABC234", "name": "周五局", "small_blind": 1, "big_blind": 2, "players": 3, "seats": 9, "locked": True}]),
        protocol.room_joined("ABC234", "周五局", TableConfig(1, 2, 40, 200)),
        protocol.room_left(),
        protocol.table_state(2, [None, {"seat": 1, "name": "bob", "cards": None}], {"street": "flop", "board": ["AS"]}),
        protocol.event(4, {"type": "action", "seat": 1, "action": "call", "amount": 2, "to": 2, "all_in": False}),
        protocol.your_turn(4, 1, LEGAL, time_left=12.5),
        protocol.error("illegal_action", "not your turn"),
    ],
)
def test_server_messages_round_trip(message):
    assert protocol.decode_server(protocol.encode(message)) == message


def test_your_turn_rebuilds_legal_actions():
    message = protocol.decode_server(protocol.encode(protocol.your_turn(1, 0, LEGAL)))

    assert protocol.legal_from_turn(message) == LEGAL


def test_welcome_carries_the_version_and_room_joined_the_config():
    assert protocol.welcome("p1", "tok")["version"] == protocol.PROTOCOL_VERSION
    assert protocol.room_joined("ABC234", "x", TableConfig(1, 2, 40, 200, max_seats=6))["config"]["max_seats"] == 6


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[1, 2]",
        '"hello"',
        "{}",
        '{"type": 3}',
        '{"type":"shout"}',
        '{"type":"welcome","player_id":"x"}',
        '{"type":"leave","extra":1}',
        '{"type":"sit","seat":1}',
        '{"type":"sit","seat":"1","buy_in":100}',
        '{"type":"sit","seat":1.0,"buy_in":100}',
        '{"type":"sit","seat":true,"buy_in":100}',
        '{"type":"rebuy","amount":null}',
        '{"type":"create_room","name":"x","small_blind":1,"big_blind":2}',
        '{"type":"create_room","name":"","small_blind":1,"big_blind":2,"bots":0}',
        '{"type":"create_room","name":"' + "x" * 25 + '","small_blind":1,"big_blind":2,"bots":0}',
        '{"type":"create_room","name":"x","small_blind":1,"big_blind":2,"bots":true}',
        '{"type":"create_room","name":"x","small_blind":1,"big_blind":2,"bots":0,"password":"' + "x" * 65 + '"}',
        '{"type":"join_room"}',
        '{"type":"join_room","room":""}',
        '{"type":"join_room","room":"' + "A" * 17 + '"}',
        '{"type":"leave_room","room":"ABC234"}',
        '{"type":"away"}',
        '{"type":"away","away":1}',
        '{"type":"away","away":"no"}',
        '{"type":"action","action":"dance"}',
        '{"type":"action","action":"bet","amount":"10"}',
        '{"type":"hello","version":1}',
        '{"type":"hello","version":1,"nickname":""}',
        '{"type":"hello","version":1,"nickname":"   "}',
        '{"type":"hello","version":1,"nickname":"a\\nb"}',
        '{"type":"hello","version":1,"nickname":"' + "x" * 21 + '"}',
        '{"type":"hello","version":1,"nickname":"a","password":"' + "x" * 65 + '"}',
        '{"type":"hello","version":"1","nickname":"a"}',
    ],
)
def test_malformed_client_messages_are_rejected(text):
    with pytest.raises(protocol.ProtocolError) as error:
        protocol.decode_client(text)
    assert error.value.code == "bad_message"


def test_oversized_and_binary_messages_are_rejected():
    big = json.dumps({"type": "leave", "pad": "x" * protocol.MAX_MESSAGE_BYTES})
    with pytest.raises(protocol.ProtocolError):
        protocol.decode_client(big)
    with pytest.raises(protocol.ProtocolError):
        protocol.decode_client(b'{"type":"leave"}')


def test_server_decoder_rejects_unknown_types():
    with pytest.raises(protocol.ProtocolError):
        protocol.decode_server('{"type":"hello","version":1,"nickname":"a"}')
