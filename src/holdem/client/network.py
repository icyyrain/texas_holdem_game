"""The client's network thread.

A private asyncio loop runs in a daemon thread. Server messages land in ``incoming`` (a
``queue.Queue``) for the tkinter thread to poll; ``send`` may be called from any thread.
Besides protocol messages, ``incoming`` carries two local ones: ``{"type": "_connect_failed",
"reason": ...}`` and ``{"type": "_disconnected"}``.
"""

from __future__ import annotations

import asyncio
import queue
import threading

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

from holdem import protocol

CONNECT_TIMEOUT = 5.0


class NetworkClient:
    def __init__(self) -> None:
        self.incoming: queue.Queue[protocol.Message] = queue.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._outgoing: asyncio.Queue[str] | None = None
        self._stop: asyncio.Event | None = None
        self._thread: threading.Thread | None = None

    def connect(self, url: str, hello: protocol.Message) -> None:
        self._thread = threading.Thread(target=asyncio.run, args=(self._main(url, hello),), daemon=True)
        self._thread.start()

    def send(self, message: protocol.Message) -> None:
        loop, outgoing = self._loop, self._outgoing
        if loop is not None and outgoing is not None and not loop.is_closed():
            loop.call_soon_threadsafe(outgoing.put_nowait, protocol.encode(message))

    def close(self) -> None:
        loop, stop = self._loop, self._stop
        if loop is not None and stop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(stop.set)

    async def _main(self, url: str, hello: protocol.Message) -> None:
        self._outgoing = asyncio.Queue()
        self._stop = asyncio.Event()
        self._loop = asyncio.get_running_loop()
        try:
            websocket = await asyncio.wait_for(connect(url, max_size=None), CONNECT_TIMEOUT)
        except (OSError, TimeoutError, InvalidURI, InvalidHandshake) as error:
            self.incoming.put({"type": "_connect_failed", "reason": str(error) or type(error).__name__})
            return

        async def read() -> None:
            try:
                async for text in websocket:
                    self.incoming.put(protocol.decode_server(text))
            except (ConnectionClosed, protocol.ProtocolError):
                pass

        async def write() -> None:
            while True:
                await websocket.send(await self._outgoing.get())

        async with websocket:
            await websocket.send(protocol.encode(hello))
            tasks = [asyncio.create_task(job) for job in (read(), write(), self._stop.wait())]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in tasks:
                task.cancel()
        self.incoming.put({"type": "_disconnected"})
