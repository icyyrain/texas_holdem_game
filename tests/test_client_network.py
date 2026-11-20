import asyncio
import queue
import socket
import threading
import time

import pytest

from holdem import protocol
from holdem.client import settings
from holdem.client.network import NetworkClient
from holdem.server.app import start_server


class ThreadedServer:
    """A real game server on its own thread and loop, like the VPS but on a free local port."""

    def __init__(self, **options):
        self.options = options
        self.ready = threading.Event()
        self.port = None
        self._loop = None
        self._stop = None
        self._thread = threading.Thread(target=asyncio.run, args=(self._main(),), daemon=True)

    async def _main(self):
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        server, _ = await start_server(host="127.0.0.1", port=0, **self.options)
        self.port = server.sockets[0].getsockname()[1]
        self.ready.set()
        async with server:
            await self._stop.wait()

    def __enter__(self):
        self._thread.start()
        assert self.ready.wait(5)
        return self

    def __exit__(self, *exc):
        self._loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(5)


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
    with ThreadedServer() as server:
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
    with ThreadedServer(password="pw") as server:
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
