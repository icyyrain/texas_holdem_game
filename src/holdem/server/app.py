"""WebSocket front end: each connection becomes a lobby session (and a room participant once it joins a room)."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from holdem import protocol
from holdem.bots import Policy
from holdem.core.cards import Deck
from holdem.server.lobby import Lobby, Session
from holdem.server.room import JoinError
from holdem.strategy import smart_action

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
    def __init__(self, lobby: Lobby):
        self.lobby = lobby
        self.connections: dict[str, Connection] = {}

    async def handler(self, websocket: ServerConnection) -> None:
        connection = Connection(websocket)
        sender = asyncio.create_task(connection.pump())
        session = None
        try:
            session = await self._handshake(connection)
            if session is not None:
                old = self.connections.get(session.player_id)
                self.connections[session.player_id] = connection
                if old is not None:
                    # The same player joined again with their token, so the old connection is stale.
                    old.deliver(protocol.error("replaced", "you joined again from another connection"))
                    old.close(hang_up="replaced")
                await self._serve(session, connection)
        except ConnectionClosed:
            pass
        finally:
            if session is not None:
                self.lobby.disconnect(session.player_id, connection)
                if self.connections.get(session.player_id) is connection:
                    del self.connections[session.player_id]
            connection.close()
            await sender

    async def _handshake(self, connection: Connection) -> Session | None:
        try:
            text = await asyncio.wait_for(connection.websocket.recv(), HELLO_TIMEOUT)
            message = protocol.decode_client(text)
            if message["type"] != "hello":
                raise protocol.ProtocolError("the first message must be hello")
            return self.lobby.join(
                connection, message["nickname"], message["password"], message["version"], message["token"]
            )
        except TimeoutError:
            return None
        except (protocol.ProtocolError, JoinError) as error:
            connection.deliver(protocol.error(error.code, str(error)))
            return None

    async def _serve(self, session: Session, connection: Connection) -> None:
        async for text in connection.websocket:
            if session.participant is not connection:
                return  # replaced by a newer connection
            try:
                message = protocol.decode_client(text)
                if message["type"] == "hello":
                    raise protocol.ProtocolError("already joined")
            except protocol.ProtocolError as error:
                connection.deliver(protocol.error(error.code, str(error)))
                continue
            try:
                self.lobby.handle(session.player_id, message)
            except Exception:
                log.exception("failed to handle %s from %s", message["type"], session.name)
                connection.deliver(protocol.error("server_error", "the server could not handle that"))


async def start_server(
    host: str = "0.0.0.0",
    port: int = protocol.DEFAULT_PORT,
    password: str | None = None,
    max_rooms: int = 10,
    max_bots: int = 5,
    hand_delay: float = 3.0,
    bot_think: float = 1.0,
    bot_policy: Policy = smart_action,
    action_timeout: float = 30.0,
    reconnect_grace: float = 120.0,
    empty_room_timeout: float = 600.0,
    deck_factory: Callable[[], Deck] | None = None,
) -> tuple[Server, Lobby]:
    """Start listening on the running loop. ``port=0`` picks a free port (see ``server.sockets``)."""
    loop = asyncio.get_running_loop()
    lobby = Lobby(
        loop.call_later, clock=loop.time, password=password, max_rooms=max_rooms, max_bots=max_bots,
        hand_delay=hand_delay, bot_think=bot_think, bot_policy=bot_policy, action_timeout=action_timeout,
        reconnect_grace=reconnect_grace, empty_room_timeout=empty_room_timeout, deck_factory=deck_factory,
    )
    server = await serve(GameServer(lobby).handler, host, port, max_size=protocol.MAX_MESSAGE_BYTES)
    return server, lobby
