"""Run the game server: python -m holdem.server --host 0.0.0.0 --port 8765

Players create and join rooms from the lobby; each room has its own blinds, password and bots.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

from holdem import protocol
from holdem.bots import POLICIES
from holdem.server.app import start_server

log = logging.getLogger("holdem.server")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m holdem.server", description="Run a No-Limit Hold'em server with rooms.")
    parser.add_argument("--host", default="0.0.0.0", help="address to listen on (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=protocol.DEFAULT_PORT, help=f"default {protocol.DEFAULT_PORT}")
    parser.add_argument(
        "--password", default=os.environ.get("HOLDEM_PASSWORD") or None,
        help="password for the whole server, on top of room passwords (default: the HOLDEM_PASSWORD "
        "environment variable, else none)",
    )
    parser.add_argument("--max-rooms", type=int, default=10, help="rooms open at once (default 10)")
    parser.add_argument("--max-bots", type=int, default=5, help="bots a room may have (default 5)")
    parser.add_argument(
        "--empty-room-timeout", type=float, default=600.0,
        help="seconds a room stays open with nobody connected (default 600)",
    )
    parser.add_argument("--bot-style", choices=sorted(POLICIES), default="smart", help="how bots play (default smart)")
    parser.add_argument("--hand-delay", type=float, default=3.0, help="seconds between hands (default 3)")
    parser.add_argument("--bot-think", type=float, default=1.0, help="seconds a bot waits before acting (default 1)")
    parser.add_argument("--action-timeout", type=float, default=30.0, help="seconds to act before an automatic check or fold (default 30)")
    parser.add_argument(
        "--reconnect-grace", type=float, default=120.0, help="seconds a disconnected player keeps their seat (default 120)"
    )
    args = parser.parse_args(argv)

    if args.max_rooms < 1:
        parser.error("--max-rooms must be at least 1")
    if not 0 <= args.max_bots <= 8:
        parser.error("--max-bots must be 0 to 8")
    if args.action_timeout <= 0:
        parser.error("--action-timeout must be positive")
    if args.reconnect_grace < 0 or args.empty_room_timeout < 0:
        parser.error("--reconnect-grace and --empty-room-timeout cannot be negative")
    return args


async def serve_forever(args: argparse.Namespace) -> None:
    server, _ = await start_server(
        host=args.host,
        port=args.port,
        password=args.password,
        max_rooms=args.max_rooms,
        max_bots=args.max_bots,
        hand_delay=args.hand_delay,
        bot_think=args.bot_think,
        bot_policy=POLICIES[args.bot_style],
        action_timeout=args.action_timeout,
        reconnect_grace=args.reconnect_grace,
        empty_room_timeout=args.empty_room_timeout,
    )
    log.info(
        "listening on %s:%d, up to %d rooms, %gs to act%s",
        args.host, args.port, args.max_rooms, args.action_timeout, ", server password set" if args.password else "",
    )
    await server.serve_forever()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(serve_forever(args))
    except KeyboardInterrupt:
        log.info("stopped")


if __name__ == "__main__":
    main()
