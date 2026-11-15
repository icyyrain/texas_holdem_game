"""Hot-seat simulator for checking the rules by hand.

Every seat plays from this terminal and all hole cards are shown. Seats listed in
``--bots`` are played by the computer (``--bot-style smart`` or ``random``).
"""

from __future__ import annotations

import argparse
import random
import re
import sys
from typing import Callable, Iterable, Mapping

from holdem.bots import POLICIES
from holdem.core.betting import Action, LegalActions
from holdem.core.cards import Card, Deck
from holdem.core.table import Event, HandState, Table, TableConfig
from holdem.strategy import situation_for
from holdem.i18n import translate

FOLD, CHECK, CALL, BET, RAISE, ALL_IN = Action

ALIASES = {
    "f": FOLD,
    "fold": FOLD,
    "x": CHECK,
    "k": CHECK,
    "check": CHECK,
    "c": CALL,
    "call": CALL,
    "b": BET,
    "bet": BET,
    "r": RAISE,
    "raise": RAISE,
    "a": ALL_IN,
    "allin": ALL_IN,
    "all-in": ALL_IN,
    "all_in": ALL_IN,
}
QUIT_WORDS = {"q", "quit", "exit"}

class Quit(Exception):
    pass


def cards_text(codes: Iterable[str | Card]) -> str:
    return " ".join((code if isinstance(code, Card) else Card.from_code(code)).display() for code in codes)


def describe_options(legal: LegalActions) -> str:
    options = []
    if FOLD in legal.actions:
        options.append("fold (f)")
    if CHECK in legal.actions:
        options.append("check (x)")
    if CALL in legal.actions:
        options.append(f"call {legal.call_amount} (c)")
    for action, label, key in ((BET, "bet", "b"), (RAISE, "raise to", "r")):
        if action in legal.actions:
            low, high = legal.min_raise_to, legal.max_raise_to
            span = str(low) if low == high else f"{low}-{high}"
            options.append(f"{label} {span} ({key} N)")
    if ALL_IN in legal.actions:
        options.append("all-in (a)")
    return " / ".join(options)


def parse_action(text: str, legal: LegalActions) -> tuple[Action, int | None]:
    """Turn ``"r 10"``, ``"call"``, ``"x"``... into an action; ValueError explains bad input."""
    words = text.strip().lower().split()
    if not words or words[0] not in ALIASES:
        raise ValueError(f"Unknown action. Choose: {describe_options(legal)}")
    action = ALIASES[words[0]]
    if action not in legal.actions:
        raise ValueError(f"You cannot {action} now. Choose: {describe_options(legal)}")
    if action in (BET, RAISE):
        if len(words) != 2 or not re.fullmatch(r"[0-9]+", words[1]):
            raise ValueError(f"Give an amount, for example: {words[0]} {legal.min_raise_to}")
        amount = int(words[1])
        if not legal.min_raise_to <= amount <= legal.max_raise_to:
            raise ValueError(f"{action} must go to between {legal.min_raise_to} and {legal.max_raise_to}")
        return action, amount
    if len(words) != 1:
        raise ValueError(f"{action} takes no amount")
    return action, None


def describe_event(event: Event, names: Mapping[int, str]) -> str | None:
    kind = event["type"]
    name = names.get(event.get("seat"))
    all_in = " (all-in)" if event.get("all_in") else ""
    if kind == "blind":
        return f"{name} posts {event['blind']} blind {event['amount']}{all_in}"
    if kind == "hole_cards":
        if "cards" not in event:
            return f"{name} is dealt two cards"
        return f"{name} is dealt {cards_text(event['cards'])}"
    if kind == "action":
        action = event["action"]
        if action == FOLD:
            return f"{name} folds"
        if action == CHECK:
            return f"{name} checks"
        if action == CALL:
            return f"{name} calls {event['amount']}{all_in}"
        if action == BET:
            return f"{name} bets {event['to']}{all_in}"
        return f"{name} raises to {event['to']}{all_in}"
    if kind == "street":
        return f"--- {event['street'].capitalize()}: {cards_text(event['cards'])}"
    if kind == "uncalled_returned":
        return f"Uncalled {event['amount']} returned to {name}"
    if kind == "showdown":
        hand_name = translate("en", event["hand"])
        return f"{name} shows {cards_text(event['cards'])}: {hand_name} ({cards_text(event['best'])})"
    if kind == "award":
        pot = "the main pot" if event["pot"] == 0 else f"side pot {event['pot']}"
        return f"{name} wins {event['amount']} from {pot}"
    return None


def format_table(hand: HandState, names: Mapping[int, str]) -> str:
    pot = sum(pot.amount for pot in hand.pots)
    board = cards_text(hand.board) or "(none)"
    lines = [f"{hand.street.value.capitalize()} · board {board} · pot {pot} · current bet {hand.current_bet}"]
    for seat, player in hand.players.items():
        marker = ">" if seat == hand.to_act else " "
        if seat == hand.button:
            position = "D"
        elif seat == hand.small_blind_seat:
            position = "SB"
        elif seat == hand.big_blind_seat:
            position = "BB"
        else:
            position = ""
        status = "folded" if player.folded else "all-in" if player.stack == 0 else ""
        lines.append(
            f"{marker} {position:<3}{names[seat]:<4} stack {player.stack:>5}  bet {player.street_bet:>4}"
            f"  {cards_text(player.hole)}  {status}".rstrip()
        )
    return "\n".join(lines)


def ask_action(
    name: str, legal: LegalActions, input_fn: Callable[[str], str], out: Callable[..., None]
) -> tuple[Action, int | None]:
    while True:
        # Windows PowerShell puts a byte-order mark in front of text piped to stdin.
        text = input_fn(f"{name}: {describe_options(legal)} > ").replace("﻿", "")
        if text.strip().lower() in QUIT_WORDS:
            raise Quit
        try:
            return parse_action(text, legal)
        except ValueError as error:
            out(str(error))


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m holdem.sim",
        description="Hot-seat No-Limit Hold'em: every seat plays from this terminal and all cards are shown.",
    )
    parser.add_argument("--players", type=int, default=3, help="2 to 9 (default 3)")
    parser.add_argument("--stack", type=int, default=200, help="starting chips (default 200)")
    parser.add_argument("--blinds", default="1/2", help="small/big (default 1/2)")
    parser.add_argument("--bots", default="", help="comma-separated player numbers the computer plays, e.g. 2,3")
    parser.add_argument("--bot-style", choices=sorted(POLICIES), default="smart", help="how bots play (default smart)")
    parser.add_argument("--hands", type=int, help="stop after this many hands")
    parser.add_argument("--seed", type=int, help="seed the shuffle and the bots for a repeatable game")
    args = parser.parse_args(argv)

    if not 2 <= args.players <= 9:
        parser.error("--players must be 2 to 9")
    try:
        args.small_blind, args.big_blind = (int(part) for part in args.blinds.split("/"))
    except ValueError:
        parser.error("--blinds must look like 1/2")
    if not 1 <= args.small_blind <= args.big_blind:
        parser.error("--blinds must be positive with the small blind no bigger than the big blind")
    if args.stack < 1:
        parser.error("--stack must be at least 1")
    if args.hands is not None and args.hands < 1:
        parser.error("--hands must be at least 1")
    try:
        args.bot_seats = {int(part) - 1 for part in args.bots.split(",") if part.strip()}
    except ValueError:
        parser.error("--bots must be player numbers like 2,3")
    if not all(0 <= seat < args.players for seat in args.bot_seats):
        parser.error(f"--bots must be player numbers from 1 to {args.players}")
    return args


def run(
    argv: list[str] | None = None,
    input_fn: Callable[[str], str] = input,
    out: Callable[..., None] = print,
) -> int:
    args = parse_args(argv)
    rng = random.Random(args.seed)
    config = TableConfig(args.small_blind, args.big_blind, min_buy_in=1, max_buy_in=args.stack, max_seats=args.players)
    if args.seed is None:
        table = Table(config)
    else:
        def seeded_deck() -> Deck:
            deck = Deck.standard()
            deck.shuffle(rng)
            return deck

        table = Table(config, deck_factory=seeded_deck)
    names = {}
    for seat in range(args.players):
        names[seat] = f"P{seat + 1}"
        table.sit(names[seat], seat, args.stack)
    humans = set(names) - args.bot_seats

    hands_played = 0
    try:
        while args.hands is None or hands_played < args.hands:
            if sum(1 for seat in table.seats if seat is not None and seat.stack > 0) < 2:
                out("Not enough players with chips left.")
                break
            hands_played += 1
            hand = table.start_hand()
            out("")
            out(f"=== Hand {hands_played} · button {names[hand.button]} · blinds {args.small_blind}/{args.big_blind}")
            shown = 0
            while True:
                for event in hand.events[shown:]:
                    line = describe_event(event, names)
                    if line:
                        out(line)
                shown = len(hand.events)
                if hand.is_complete():
                    break
                seat = hand.to_act
                legal = hand.legal_actions(seat)
                if seat in args.bot_seats:
                    action, amount = POLICIES[args.bot_style](situation_for(hand, seat), rng)
                else:
                    out(format_table(hand, names))
                    action, amount = ask_action(names[seat], legal, input_fn, out)
                hand.apply(seat, action, amount)
            table.finish_hand()

            if humans and hands_played != args.hands:
                if input_fn("Enter for the next hand, q to quit: ").strip().lower() in QUIT_WORDS:
                    break
    except (Quit, EOFError):
        if table.hand_in_progress:
            out("Hand abandoned; chips already bet are not in the stacks below.")

    out("Final stacks: " + ", ".join(f"{names[seat.index]}={seat.stack}" for seat in table.seats if seat is not None))
    return 0


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(run())


if __name__ == "__main__":
    main()
