from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable

from holdem.core.betting import NO_ACTIONS, Action, IllegalActionError, LegalActions, legal_actions, resolve_action
from holdem.core.cards import Card, Deck
from holdem.core.evaluator import HandRank, best_hand, hand_name_key
from holdem.core.pots import Pot, build_pots, split_pot, uncalled_excess


class Street(StrEnum):
    PREFLOP = "preflop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"
    SHOWDOWN = "showdown"


_NEXT_STREET = {Street.PREFLOP: Street.FLOP, Street.FLOP: Street.TURN, Street.TURN: Street.RIVER}
_BOARD_CARDS = {Street.FLOP: 3, Street.TURN: 1, Street.RIVER: 1}

Event = dict[str, Any]


def _require_int(value: object, name: str, minimum: int) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be a whole number of at least {minimum}")


@dataclass(frozen=True)
class TableConfig:
    small_blind: int
    big_blind: int
    min_buy_in: int
    max_buy_in: int
    max_seats: int = 9
    action_timeout: float = 30.0

    def __post_init__(self) -> None:
        _require_int(self.small_blind, "small_blind", 1)
        _require_int(self.big_blind, "big_blind", self.small_blind)
        _require_int(self.min_buy_in, "min_buy_in", 1)
        _require_int(self.max_buy_in, "max_buy_in", self.min_buy_in)
        _require_int(self.max_seats, "max_seats", 2)
        if self.max_seats > 9:
            raise ValueError("max_seats must be at most 9")
        if self.action_timeout <= 0:
            raise ValueError("action_timeout must be positive")


@dataclass
class Seat:
    index: int
    player_id: str
    stack: int
    """Chips in front of the player; a running hand takes bets from it and pays winnings into it."""
    away: bool = False
    leaving: bool = False
    """Asked to leave during a hand they are in; the seat is freed once the hand ends."""


@dataclass
class HandPlayer:
    seat: Seat
    hole: tuple[Card, ...] = ()
    street_bet: int = 0
    total_bet: int = 0
    folded: bool = False
    acted_at: int | None = None
    """The current bet when this player last acted this street; None before they act."""

    @property
    def stack(self) -> int:
        return self.seat.stack

    @property
    def can_act(self) -> bool:
        return not self.folded and self.seat.stack > 0


@dataclass(frozen=True)
class HandResult:
    pots: list[tuple[Pot, dict[int, int]]]
    """Each pot with the chips each winner took from it."""
    net: dict[int, int]
    """Chips won minus chips put in, per seat."""
    showdown: dict[int, tuple[HandRank, tuple[Card, ...]]] = field(default_factory=dict)


class HandState:
    """One hand from the blinds to the payout. Seats are numbered clockwise."""

    def __init__(self, config: TableConfig, seats: list[Seat], button: int, deck: Deck):
        if len(seats) < 2:
            raise ValueError("a hand needs at least two players")
        self.config = config
        self.deck = deck
        self.players = {seat.index: HandPlayer(seat) for seat in sorted(seats, key=lambda seat: seat.index)}
        self.order = list(self.players)
        if button not in self.players:
            raise ValueError("the button must be one of the players")
        self.button = button
        # Heads-up the button posts the small blind and acts first preflop.
        self.small_blind_seat = button if len(self.order) == 2 else self._next_seat(button)
        self.big_blind_seat = self._next_seat(self.small_blind_seat)

        self.street = Street.PREFLOP
        self.board: list[Card] = []
        self.burned: list[Card] = []
        self.current_bet = 0
        self.min_raise = config.big_blind
        self.to_act: int | None = None
        self.events: list[Event] = []
        self._result: HandResult | None = None

        self._log(
            "hand_start", button=button, small_blind=self.small_blind_seat, big_blind=self.big_blind_seat
        )
        self._post_blind(self.small_blind_seat, config.small_blind, "small")
        self._post_blind(self.big_blind_seat, config.big_blind, "big")
        # A short big blind still sets the full price to call.
        self.current_bet = config.big_blind
        self._deal_hole_cards()
        self._advance(after=self.big_blind_seat)

    @property
    def pots(self) -> list[Pot]:
        """Pots from finished streets; this street's bets are still in front of the players."""
        collected = {index: player.total_bet - player.street_bet for index, player in self.players.items()}
        return build_pots(collected, self._live_seats())

    def is_complete(self) -> bool:
        return self._result is not None

    def results(self) -> HandResult:
        if self._result is None:
            raise ValueError("the hand is not over")
        return self._result

    def legal_actions(self, seat: int) -> LegalActions:
        if seat != self.to_act:
            return NO_ACTIONS
        player = self.players[seat]
        return legal_actions(
            stack=player.stack,
            street_bet=player.street_bet,
            current_bet=self.current_bet,
            min_raise=self.min_raise,
            can_raise=self._can_raise(player),
        )

    def apply(self, seat: int, action: Action | str, amount: object = None) -> None:
        """Play ``action`` for ``seat``; BET and RAISE ``amount`` is the street total to go to."""
        if self.is_complete():
            raise IllegalActionError("the hand is over")
        if seat != self.to_act:
            raise IllegalActionError("it is not this seat's turn")
        try:
            action = Action(action)
        except ValueError:
            raise IllegalActionError(f"unknown action: {action!r}") from None
        player = self.players[seat]
        target = resolve_action(
            action, amount, self.legal_actions(seat), stack=player.stack, street_bet=player.street_bet
        )

        if target is None:
            player.folded = True
            self._log("action", seat=seat, action=Action.FOLD.value, amount=0, to=player.street_bet, all_in=False)
        else:
            kind = self._action_kind(target)
            chips = target - player.street_bet
            if target > self.current_bet:
                raise_size = target - self.current_bet
                # An all-in short of a full raise does not change the minimum raise.
                if raise_size >= self.min_raise:
                    self.min_raise = raise_size
                self.current_bet = target
            self._put(player, chips)
            self._log("action", seat=seat, action=kind.value, amount=chips, to=target, all_in=player.stack == 0)
        player.acted_at = self.current_bet
        self._advance(after=seat)

    def _action_kind(self, target: int) -> Action:
        """What an action really was, so an ALL_IN is logged as the call, bet or raise it made."""
        if target > self.current_bet:
            return Action.BET if self.current_bet == 0 else Action.RAISE
        if target == self.players[self.to_act].street_bet:
            return Action.CHECK
        return Action.CALL

    def _can_raise(self, player: HandPlayer) -> bool:
        # A player who already acted may raise again only if the bet has since grown by at
        # least a full raise; incomplete all-ins can add up to one.
        return player.acted_at is None or self.current_bet - player.acted_at >= self.min_raise

    def _needs_action(self, player: HandPlayer) -> bool:
        return player.can_act and (player.acted_at is None or player.street_bet < self.current_bet)

    def _round_complete(self) -> bool:
        live = [player for player in self.players.values() if not player.folded]
        if len(live) <= 1:
            return True
        active = [player for player in live if player.stack > 0]
        if not active:
            return True
        if len(active) == 1:
            # Everyone else is all-in or folded: nobody is left to bet against.
            (player,) = active
            others = max(other.street_bet for other in self.players.values() if other is not player)
            if player.street_bet >= others:
                return True
        return not any(self._needs_action(player) for player in active)

    def _advance(self, after: int) -> None:
        while True:
            if not self._round_complete():
                self.to_act = next(
                    seat for seat in self._seats_after(after) if self._needs_action(self.players[seat])
                )
                return
            self.to_act = None
            self._end_betting_round()
            if len(self._live_seats()) == 1:
                self._finish(showdown=False)
                return
            if self.street == Street.RIVER:
                self._finish(showdown=True)
                return
            # With fewer than two players able to bet, this keeps dealing to the river.
            self._deal_street(_NEXT_STREET[self.street])
            after = self.button

    def _end_betting_round(self) -> None:
        excess = uncalled_excess({index: player.street_bet for index, player in self.players.items()})
        if excess:
            seat, chips = excess
            player = self.players[seat]
            player.seat.stack += chips
            player.street_bet -= chips
            player.total_bet -= chips
            self._log("uncalled_returned", seat=seat, amount=chips)
        for player in self.players.values():
            player.street_bet = 0
            player.acted_at = None
        self.current_bet = 0
        self.min_raise = self.config.big_blind

    def _finish(self, showdown: bool) -> None:
        live = self._live_seats()
        order = self._seats_after(self.button)
        ranks: dict[int, HandRank] = {}
        shown: dict[int, tuple[HandRank, tuple[Card, ...]]] = {}
        if showdown:
            self.street = Street.SHOWDOWN
            for seat in order:
                if seat in live:
                    rank, five = best_hand([*self.players[seat].hole, *self.board])
                    ranks[seat] = rank
                    shown[seat] = (rank, five)
                    self._log(
                        "showdown",
                        seat=seat,
                        cards=[card.code for card in self.players[seat].hole],
                        best=[card.code for card in five],
                        hand=hand_name_key(rank),
                    )

        won = dict.fromkeys(self.order, 0)
        awards = []
        for index, pot in enumerate(build_pots({seat: p.total_bet for seat, p in self.players.items()}, live)):
            shares = split_pot(pot, ranks, order)
            for seat, chips in shares.items():
                self.players[seat].seat.stack += chips
                won[seat] += chips
                self._log("award", seat=seat, amount=chips, pot=index)
            awards.append((pot, shares))
        net = {seat: won[seat] - player.total_bet for seat, player in self.players.items()}
        self._result = HandResult(awards, net, shown)
        self._log("hand_end")

    def _post_blind(self, seat: int, blind: int, kind: str) -> None:
        player = self.players[seat]
        chips = min(blind, player.stack)
        self._put(player, chips)
        self._log("blind", seat=seat, blind=kind, amount=chips, all_in=player.stack == 0)

    def _deal_hole_cards(self) -> None:
        deal_order = [self.small_blind_seat, *self._seats_after(self.small_blind_seat)[:-1]]
        cards: dict[int, list[Card]] = {seat: [] for seat in deal_order}
        for _ in range(2):
            for seat in deal_order:
                cards[seat].append(self.deck.deal())
        for seat in deal_order:
            self.players[seat].hole = tuple(cards[seat])
            self._log("hole_cards", seat=seat, cards=[card.code for card in cards[seat]])

    def _deal_street(self, street: Street) -> None:
        self.street = street
        # One card goes face down before each street; nobody ever sees it.
        self.burned.append(self.deck.deal())
        self._log("burn")
        cards = [self.deck.deal() for _ in range(_BOARD_CARDS[street])]
        self.board.extend(cards)
        self._log("street", street=street.value, cards=[card.code for card in cards])

    def _put(self, player: HandPlayer, chips: int) -> None:
        player.seat.stack -= chips
        player.street_bet += chips
        player.total_bet += chips

    def _live_seats(self) -> list[int]:
        return [seat for seat, player in self.players.items() if not player.folded]

    def _seats_after(self, seat: int) -> list[int]:
        """Every seat in the hand clockwise, starting left of ``seat`` and ending with it."""
        start = self.order.index(seat) + 1
        return self.order[start:] + self.order[:start]

    def _next_seat(self, seat: int) -> int:
        return self._seats_after(seat)[0]

    def _log(self, kind: str, **data: Any) -> None:
        self.events.append({"type": kind, **data})


def _shuffled_deck() -> Deck:
    deck = Deck.standard()
    deck.shuffle()
    return deck


class Table:
    """Seats, the button and the current hand for one cash-game table."""

    def __init__(self, config: TableConfig, deck_factory: Callable[[], Deck] = _shuffled_deck):
        self.config = config
        self.seats: list[Seat | None] = [None] * config.max_seats
        self.button: int | None = None
        self.hand: HandState | None = None
        self._deck_factory = deck_factory

    @property
    def hand_in_progress(self) -> bool:
        return self.hand is not None and not self.hand.is_complete()

    def seat_of(self, player_id: str) -> Seat | None:
        return next((seat for seat in self.seats if seat is not None and seat.player_id == player_id), None)

    def sit(self, player_id: str, seat: int, buy_in: int) -> Seat:
        if type(seat) is not int or not 0 <= seat < self.config.max_seats:
            raise ValueError(f"seat must be 0 to {self.config.max_seats - 1}")
        if self.seats[seat] is not None:
            raise ValueError("that seat is taken")
        if self.seat_of(player_id) is not None:
            raise ValueError("player is already seated")
        _require_int(buy_in, "buy_in", self.config.min_buy_in)
        if buy_in > self.config.max_buy_in:
            raise ValueError(f"buy_in must be at most {self.config.max_buy_in}")
        self.seats[seat] = Seat(seat, player_id, buy_in)
        return self.seats[seat]

    def leave(self, player_id: str) -> bool:
        """Free the player's seat; True if it is free now, False if it frees when the hand ends."""
        seat = self._require_seat(player_id)
        if self._in_current_hand(seat):
            seat.leaving = True
            return False
        self.seats[seat.index] = None
        return True

    def rebuy(self, player_id: str, amount: int) -> None:
        seat = self._require_seat(player_id)
        if self._in_current_hand(seat):
            raise ValueError("cannot rebuy during a hand")
        _require_int(amount, "amount", 1)
        if seat.stack + amount > self.config.max_buy_in:
            raise ValueError(f"stack cannot exceed {self.config.max_buy_in}")
        seat.stack += amount

    def set_away(self, player_id: str, away: bool) -> None:
        self._require_seat(player_id).away = away

    def finish_hand(self) -> list[str]:
        """Free the seats of players who left during the hand; returns their ids."""
        if self.hand_in_progress:
            raise ValueError("the hand is not over")
        removed = []
        for index, seat in enumerate(self.seats):
            if seat is not None and seat.leaving:
                self.seats[index] = None
                removed.append(seat.player_id)
        return removed

    def start_hand(self, deck: Deck | None = None) -> HandState:
        if self.hand_in_progress:
            raise ValueError("a hand is already in progress")
        self.finish_hand()
        players = [seat for seat in self.seats if seat is not None and not seat.away and seat.stack > 0]
        if len(players) < 2:
            raise ValueError("need at least two players with chips")
        # The button moves to the next player clockwise; no dead-button rule.
        indices = [seat.index for seat in players]
        if self.button is None:
            self.button = indices[0]
        else:
            self.button = next((index for index in indices if index > self.button), indices[0])
        self.hand = HandState(self.config, players, self.button, deck or self._deck_factory())
        return self.hand

    def _in_current_hand(self, seat: Seat) -> bool:
        if not self.hand_in_progress:
            return False
        player = self.hand.players.get(seat.index)
        return player is not None and player.seat is seat

    def _require_seat(self, player_id: str) -> Seat:
        seat = self.seat_of(player_id)
        if seat is None:
            raise ValueError("player is not seated")
        return seat
