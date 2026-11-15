# Agent Notes

Design spec: `docs/specs/2026-11-15-texas-holdem-design.md`.

## Commands

- Tests: `conda run -n holdem-game pytest`
- Desktop client: `conda run -n holdem-game python -m holdem.client`
- Server: `conda run -n holdem-game python -m holdem.server --port 8765 --bots 2`; text client: `python -m holdem.textclient --name alice` (seats are 1-based in the text client, 0-based in the protocol).
- Rules simulator: `conda run -n holdem-game python -m holdem.sim --players 3` (hot-seat, all cards shown; `--bots 1,2,3 --hands 20 --seed 7` runs unattended). `conda run` pipes output through the console code page, so card suits can show as mojibake; run the env's `python.exe` directly to see them.
- Windows exe: `powershell -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1` (writes `dist\Holdem.exe`; not tried yet, packaging is milestone 8)
- `scripts/prepare_card_images.py` needs cairo DLLs from the conda env, so run it through `conda run` (activation puts `Library\bin` on PATH).

## Architecture

- `holdem.core` is the only source of game rules and does no I/O. Chips are integers.
- `core/table.py`: `Table` owns seats and the button; `HandState` runs one hand. Seat numbers increase clockwise. A running hand moves chips in and out of the shared `Seat.stack` objects, so table stacks are always current.
- BET/RAISE amounts are the player's street total ("raise to"), not the increment. Illegal moves raise `IllegalActionError` (a `ValueError`) and leave the state unchanged.
- Re-raise rights: a player who already acted may raise again only if the bet grew by at least the last full raise since then (`HandState._can_raise`), which covers both short all-ins and short all-ins that add up to a full raise.
- `HandState.events` is the hand log (`blind`, `hole_cards`, `action`, `burn`, `street`, `uncalled_returned`, `showdown`, `award`, ...), with cards as codes. `hole_cards` events are private to their seat; the server must filter them. `burn` carries no card; burned cards stay in `HandState.burned` and are never sent to clients.
- Simplifications for v1: the button moves to the next player with chips (no dead button), and a short all-in big blind still sets the full big blind as the price to call.
- Cards use two-character codes (`"AS"`, `"TH"`); `Card.code` is also the PNG name in `assets/cards`. `Card.value` runs 2–14 (ace high).
- `protocol.py` defines every message. `decode_client` validates strictly (exact fields, no bools as ints, size limit); server messages are built with its helper functions.
- `server/room.py` `Room` is the network-free game host. Anything that plays a seat is a `Participant` with one method, `deliver(message)`: `server/app.py` `Connection` wraps a WebSocket, `bots.py` `BotPlayer` plays in-process. Participants answer through `room.handle(player_id, message)` and must never call back into the room from inside `deliver`; bots schedule their move with `room.schedule`.
- `Room` sends each participant its own view: `table_state` shows only their hole cards (plus cards shown at showdown) and other players' `hole_cards` events arrive without cards. Seats whose player left, is away or disconnected are auto-played (check, else fold).
- Until milestone 7 (reconnect tokens, action timers) a disconnect gives up the seat. Room tests drive time with a fake `schedule`; server tests start a real server on port 0.
- `Deck.shuffle()` uses `secrets.SystemRandom` by default; tests build rigged decks with `Deck([...])` (cards are dealt from the end of the list). Hole cards go one per round starting at the small blind; then one card is burned before the flop, the turn and the river. `tests/test_hand.py::rigged_deck` builds such decks.
- UI strings live in `i18n.py` (`zh` and `en` must define identical keys and placeholders; `test_i18n.py` enforces this).
- `client/canvas_ui.py` (colors, rounded rects, chips, `CanvasButton`) is copied unchanged from blackjack_game.
- Card faces load from PNG via `PhotoImage(data=...)`; loading with `file=` failed intermittently under pytest output capture (seen in blackjack_game). `assets/cards` is 96x134 (board), `assets/cards_small` 60x84 (seats); both are rendered from the SVGs by `scripts/prepare_card_images.py`.
- tkinter is touched only on the main thread; `client/network.py` runs its own asyncio loop in a daemon thread and passes messages through a `queue.Queue` polled with `after`. It adds two local message types, `_connect_failed` and `_disconnected`.
- `client/host.py` `EmbeddedServer` is "Create Room": the normal `start_server` on its own thread and loop.
- `client/app.py` `HoldemApp`: server events become animation steps (`_steps_for`). While they play, `self.view` (what is drawn) lags `self.state` (the last `table_state`) and the action buttons stay locked; when the queue drains, the view is replaced by a copy of the state. Steps start on a later tick so a `table_state` polled in the same batch is used at the end.
- `client/layout.py` holds the tkinter-free geometry and pot-preset math. Your seat is always drawn at the bottom; seat numbers run clockwise, so the next seat is on screen left.
- GUI tests (`tests/test_gui.py`) pass a fake network, fake server and fake `ask_integer` into `HoldemApp`, replace `app._schedule` with a fake scheduler, and feed server messages through `app.handle_message`.
