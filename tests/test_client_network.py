import ipaddress
import queue
import socket
import time

from holdem import protocol
from holdem.client.host import EmbeddedServer, lan_addresses
from holdem.client.network import NetworkClient
from holdem.core.table import TableConfig

CONFIG = TableConfig(small_blind=1, big_blind=2, min_buy_in=40, max_buy_in=200)


def wait_for(client, kind, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            message = client.incoming.get(timeout=0.1)
        except queue.Empty:
            continue
        if message["type"] == kind:
            return message
    raise AssertionError(f"no {kind} message")


def test_embedded_server_and_network_client_talk():
    server = EmbeddedServer(CONFIG, port=0, host="127.0.0.1", password="pw")
    server.start()
    assert server.ready.wait(5)
    assert server.error is None and server.port

    client = NetworkClient()
    try:
        client.connect(f"ws://127.0.0.1:{server.port}", protocol.hello("alice", "pw"))
        welcome = wait_for(client, "welcome")
        assert welcome["config"]["big_blind"] == 2
        client.send(protocol.sit(4, 100))
        state = wait_for(client, "table_state")
        while state["your_seat"] is None:
            state = wait_for(client, "table_state")
        assert state["seats"][4]["name"] == "alice"
    finally:
        client.close()
        wait_for(client, "_disconnected")
        server.stop()


def test_wrong_password_ends_with_error_then_disconnect():
    server = EmbeddedServer(CONFIG, port=0, host="127.0.0.1", password="pw")
    server.start()
    server.ready.wait(5)
    client = NetworkClient()
    try:
        client.connect(f"ws://127.0.0.1:{server.port}", protocol.hello("alice", "nope"))
        assert wait_for(client, "error")["code"] == "bad_password"
        wait_for(client, "_disconnected")
    finally:
        server.stop()


def test_unreachable_server_reports_connect_failed():
    with socket.socket() as spare:
        spare.bind(("127.0.0.1", 0))
        port = spare.getsockname()[1]
    client = NetworkClient()
    client.connect(f"ws://127.0.0.1:{port}", protocol.hello("alice"))

    assert wait_for(client, "_connect_failed")["reason"]


def test_embedded_server_reports_a_busy_port():
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        server = EmbeddedServer(CONFIG, port=taken.getsockname()[1], host="127.0.0.1")
        server.start()
        assert server.ready.wait(5)

    assert server.error
    assert server.port is None


def test_lan_addresses_are_ipv4_strings():
    addresses = lan_addresses()

    assert addresses
    for address in addresses:
        assert ipaddress.ip_address(address).version == 4
