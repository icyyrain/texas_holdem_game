"""Run a headless table: python -m holdem.server --host 0.0.0.0 --port 8765 --password xxx"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

from holdem import protocol
from holdem.core.table import TableConfig
from holdem.server.app import start_server

log = logging.getLogger("holdem.server")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m holdem.server", description="Run a No-Limit Hold'em table.")
    parser.add_argument("--host", default="0.0.0.0", help="address to listen on (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=protocol.DEFAULT_PORT, help=f"default {protocol.DEFAULT_PORT}")
    parser.add_argument(
        "--password", default=os.environ.get("HOLDEM_PASSWORD") or None,
        help="room password (default: the HOLDEM_PASSWORD environment variable, else none)",
    )
    parser.add_argument("--blinds", default="1/2", help="small/big (default 1/2)")
    parser.add_argument("--min-buy-in", type=int, help="default 20 big blinds")
    parser.add_argument("--max-buy-in", type=int, help="default 100 big blinds")
    parser.add_argument("--seats", type=int, default=9, help="2 to 9 (default 9)")
    parser.add_argument("--bots", type=int, default=0, help="random bots to seat (default 0)")
    parser.add_argument("--hand-delay", type=float, default=3.0, help="seconds between hands (default 3)")
    parser.add_argument("--bot-think", type=float, default=1.0, help="seconds a bot waits before acting (default 1)")
    parser.add_argument("--action-timeout", type=float, default=30.0, help="seconds to act before an automatic check or fold (default 30)")
    parser.add_argument(
        "--reconnect-grace", type=float, default=120.0, help="seconds a disconnected player keeps their seat (default 120)"
    )
    args = parser.parse_args(argv)

    try:
        small, big = (int(part) for part in args.blinds.split("/"))
        args.config = TableConfig(
            small_blind=small,
            big_blind=big,
            min_buy_in=args.min_buy_in if args.min_buy_in is not None else 20 * big,
            max_buy_in=args.max_buy_in if args.max_buy_in is not None else 100 * big,
            max_seats=args.seats,
            action_timeout=args.action_timeout,
        )
    except ValueError as error:
        parser.error(f"bad table settings: {error}")
    if not 0 <= args.bots <= args.seats:
        parser.error("--bots must be between 0 and the number of seats")
    if args.reconnect_grace < 0:
        parser.error("--reconnect-grace cannot be negative")
    return args


async def serve_forever(args: argparse.Namespace) -> None:
    server, _ = await start_server(
        args.config,
        host=args.host,
        port=args.port,
        password=args.password,
        bots=args.bots,
        hand_delay=args.hand_delay,
        bot_think=args.bot_think,
        reconnect_grace=args.reconnect_grace,
    )
    config = args.config
    log.info(
        "listening on %s:%d, blinds %d/%d, buy-in %d-%d, %d seats, %gs to act%s",
        args.host, args.port, config.small_blind, config.big_blind, config.min_buy_in, config.max_buy_in,
        config.max_seats, config.action_timeout, ", password set" if args.password else "",
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
