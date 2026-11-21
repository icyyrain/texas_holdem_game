import contextlib
import ipaddress
import queue
import socket
import time

import pytest

from holdem import protocol
from holdem.client import settings
from holdem.client.host import EmbeddedServer, lan_addresses
from holdem.client.network import NetworkClient


@contextlib.contextmanager
def built_in_server(**options):
    """The client's built-in server on a free local port."""
    server = EmbeddedServer(0, host="127.0.0.1", **options)
    server.start()
    assert server.ready.wait(5) and server.error is None
    try:
        yield server
    finally:
        server.stop()


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


def test_network_client_signs_in_creates_a_room_and_sits():
    with built_in_server() as server:
        client = NetworkClient()
        try:
            client.connect(f"ws://127.0.0.1:{server.port}", protocol.hello("alice"))
            assert wait_for(client, "welcome")["token"]
            assert wait_for(client, "rooms")["rooms"] == []
            client.send(protocol.create_room("mine", 1, 2))
            assert wait_for(client, "room_joined")["config"]["big_blind"] == 2
            client.send(protocol.sit(4, 100))
            state = wait_for(client, "table_state")
            while state["your_seat"] is None:
                state = wait_for(client, "table_state")
            assert state["seats"][4]["name"] == "alice"
        finally:
            client.close()
            wait_for(client, "_disconnected")


def test_a_refused_hello_ends_with_error_then_disconnect():
    with built_in_server(password="pw") as server:
        client = NetworkClient()
        client.connect(f"ws://127.0.0.1:{server.port}", protocol.hello("alice", "nope"))
        assert wait_for(client, "error")["code"] == "bad_password"
        wait_for(client, "_disconnected")


def test_unreachable_server_reports_connect_failed():
    with socket.socket() as spare:
        spare.bind(("127.0.0.1", 0))
        port = spare.getsockname()[1]
    client = NetworkClient()
    client.connect(f"ws://127.0.0.1:{port}", protocol.hello("alice"))

    assert wait_for(client, "_connect_failed")["reason"]


def test_the_built_in_server_reports_a_busy_port():
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        server = EmbeddedServer(taken.getsockname()[1], host="127.0.0.1")
        server.start()
        assert server.ready.wait(5)

    assert server.error
    assert server.port is None


def test_lan_addresses_are_ipv4_strings():
    addresses = lan_addresses()

    assert addresses
    for address in addresses:
        assert ipaddress.ip_address(address).version == 4


@pytest.fixture
def no_override(monkeypatch):
    monkeypatch.delenv("HOLDEM_SERVER", raising=False)


def test_server_address_comes_from_the_bundled_file(tmp_path, no_override):
    (tmp_path / settings.SERVER_FILE).write_text("# where to play\n\nwss://poker.example.com:8765\n", encoding="utf-8")

    assert settings.server_url(tmp_path) == "wss://poker.example.com:8765"


def test_without_the_file_the_client_uses_a_local_server(tmp_path, no_override):
    assert settings.server_url(tmp_path) == f"ws://127.0.0.1:{protocol.DEFAULT_PORT}"


def test_the_environment_overrides_the_bundled_file(tmp_path, monkeypatch):
    (tmp_path / settings.SERVER_FILE).write_text("wss://poker.example.com:8765\n", encoding="utf-8")
    monkeypatch.setenv("HOLDEM_SERVER", "ws://10.0.0.5:9000")

    assert settings.server_url(tmp_path) == "ws://10.0.0.5:9000"
