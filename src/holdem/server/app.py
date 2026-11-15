"""WebSocket front end: each connection becomes a room participant."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from holdem import protocol
from holdem.core.cards import Deck
from holdem.core.table import TableConfig
from holdem.server.room import JoinError, Member, Room

log = logging.getLogger(__name__)

HELLO_TIMEOUT = 10.0
# A client this far behind is not reading; drop it rather than buffer without limit.
MAX_OUTBOX = 2000


class Connection:
    """A connected client as a room participant. Messages are queued and sent in order."""

    def __init__(self, websocket: ServerConnection):
        self.websocket = websocket
        self.outbox: asyncio.Queue[str | None] = asyncio.Queue()
        self._dropped = False
        self._hang_up: str | None = None

    def deliver(self, message: protocol.Message) -> None:
        if self._dropped:
            return
        if self.outbox.qsize() >= MAX_OUTBOX:
            self._dropped = True
            log.warning("dropping %s: not reading messages", self.websocket.remote_address)
            asyncio.get_running_loop().create_task(self.websocket.close(1008, "not reading messages"))
            return
        self.outbox.put_nowait(protocol.encode(message))

    def close(self, hang_up: str | None = None) -> None:
        """Stop sending. With ``hang_up``, close the socket with that reason once the queue is sent."""
        if hang_up is not None and self._hang_up is None:
            self._hang_up = hang_up
        self.outbox.put_nowait(None)

    async def pump(self) -> None:
        try:
            while (text := await self.outbox.get()) is not None:
                await self.websocket.send(text)
            if self._hang_up is not None:
                await self.websocket.close(1000, self._hang_up)
        except ConnectionClosed:
            pass


class GameServer:
    def __init__(self, room: Room):
        self.room = room
        self.connections: dict[str, Connection] = {}

    async def handler(self, websocket: ServerConnection) -> None:
        connection = Connection(websocket)
        sender = asyncio.create_task(connection.pump())
        member = None
        try:
            member = await self._handshake(connection)
            if member is not None:
                old = self.connections.get(member.player_id)
                self.connections[member.player_id] = connection
                if old is not None:
                    # The same player joined again with their token, so the old connection is stale.
                    old.deliver(protocol.error("replaced", "you joined again from another connection"))
                    old.close(hang_up="replaced")
                await self._serve(member, connection)
        except ConnectionClosed:
            pass
        finally:
            if member is not None:
                self.room.disconnect(member.player_id, connection)
                if self.connections.get(member.player_id) is connection:
                    del self.connections[member.player_id]
            connection.close()
            await sender

    async def _handshake(self, connection: Connection) -> Member | None:
        try:
            text = await asyncio.wait_for(connection.websocket.recv(), HELLO_TIMEOUT)
            message = protocol.decode_client(text)
            if message["type"] != "hello":
                raise protocol.ProtocolError("the first message must be hello")
            return self.room.join(
                connection, message["nickname"], message["password"], message["version"], message["token"]
            )
        except TimeoutError:
            return None
        except (protocol.ProtocolError, JoinError) as error:
            connection.deliver(protocol.error(error.code, str(error)))
            return None

    async def _serve(self, member: Member, connection: Connection) -> None:
        async for text in connection.websocket:
            if member.participant is not connection:
                return  # replaced by a newer connection
            try:
                message = protocol.decode_client(text)
                if message["type"] == "hello":
                    raise protocol.ProtocolError("already joined")
            except protocol.ProtocolError as error:
                connection.deliver(protocol.error(error.code, str(error)))
                continue
            try:
                self.room.handle(member.player_id, message)
            except Exception:
                log.exception("failed to handle %s from %s", message["type"], member.name)
                connection.deliver(protocol.error("server_error", "the server could not handle that"))


async def start_server(
    config: TableConfig,
    host: str = "0.0.0.0",
    port: int = protocol.DEFAULT_PORT,
    password: str | None = None,
    bots: int = 0,
    hand_delay: float = 3.0,
    bot_think: float = 1.0,
    deck_factory: Callable[[], Deck] | None = None,
    reconnect_grace: float = 120.0,
) -> tuple[Server, Room]:
    """Start listening on the running loop. ``port=0`` picks a free port (see ``server.sockets``)."""
    loop = asyncio.get_running_loop()
    room = Room(
        config, loop.call_later, password=password, hand_delay=hand_delay, deck_factory=deck_factory,
        clock=loop.time, reconnect_grace=reconnect_grace,
    )
    for number in range(1, bots + 1):
        room.add_bot(f"Bot {number}", think=bot_think)
    server = await serve(GameServer(room).handler, host, port, max_size=protocol.MAX_MESSAGE_BYTES)
    return server, room
