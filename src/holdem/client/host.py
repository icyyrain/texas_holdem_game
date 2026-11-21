"""The server behind "Host here": the normal game server, lobby and all, on its own thread and loop."""

from __future__ import annotations

import asyncio
import socket
import threading

from holdem.server.app import start_server


class EmbeddedServer:
    def __init__(self, port: int, host: str = "0.0.0.0", **options: object):
        """``options`` go to ``start_server`` (room limits, timers, ...)."""
        self.host = host
        self.requested_port = port
        self.options = options
        self.ready = threading.Event()
        """Set once listening (``port`` is known) or once starting failed (``error`` says why)."""
        self.port: int | None = None
        self.error: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=asyncio.run, args=(self._main(),), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._loop is not None and self._stop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread is not None:
            self._thread.join(timeout=3)

    async def _main(self) -> None:
        self._stop = asyncio.Event()
        self._loop = asyncio.get_running_loop()
        try:
            server, _ = await start_server(host=self.host, port=self.requested_port, **self.options)
        except OSError as error:
            self.error = error.strerror or str(error)
            self.ready.set()
            return
        self.port = server.sockets[0].getsockname()[1]
        self.ready.set()
        async with server:
            await self._stop.wait()


def lan_addresses() -> list[str]:
    """This machine's IPv4 addresses that other players could connect to (best guess first)."""
    addresses = []
    try:
        # Connecting a UDP socket sends nothing; it only picks the outgoing interface.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            addresses.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        for address in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not address.startswith("127.") and address not in addresses:
                addresses.append(address)
    except OSError:
        pass
    return [address for address in addresses if address != "0.0.0.0"] or ["127.0.0.1"]
