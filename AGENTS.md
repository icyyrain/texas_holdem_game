# Agent Notes

Design spec: `docs/specs/2026-11-15-texas-holdem-design.md`.

## Commands

- Tests: `conda run -n holdem-game pytest`
- Rules simulator: `conda run -n holdem-game python -m holdem.sim --players 3` (hot-seat, all cards shown; `--bots 1,2,3 --hands 20 --seed 7` runs unattended). `conda run` pipes output through the console code page, so card suits can show as mojibake; run the env's `python.exe` directly to see them.
- Windows exe: `powershell -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1` (writes `dist\Holdem.exe`; needs the client entry point `src\holdem\client\app.py`)
- `scripts/prepare_card_images.py` needs cairo DLLs from the conda env, so run it through `conda run` (activation puts `Library\bin` on PATH).

## Architecture

- `holdem.core` is the only source of game rules and does no I/O. Chips are integers.
- `core/table.py`: `Table` owns seats and the button; `HandState` runs one hand. Seat numbers increase clockwise. A running hand moves chips in and out of the shared `Seat.stack` objects, so table stacks are always current.
- BET/RAISE amounts are the player's street total ("raise to"), not the increment. Illegal moves raise `IllegalActionError` (a `ValueError`) and leave the state unchanged.
- Re-raise rights: a player who already acted may raise again only if the bet grew by at least the last full raise since then (`HandState._can_raise`), which covers both short all-ins and short all-ins that add up to a full raise.
- `HandState.events` is the hand log (`blind`, `hole_cards`, `action`, `burn`, `street`, `uncalled_returned`, `showdown`, `award`, ...), with cards as codes. `hole_cards` events are private to their seat; the server must filter them. `burn` carries no card; burned cards stay in `HandState.burned` and are never sent to clients.
- Simplifications for v1: the button moves to the next player with chips (no dead button), and a short all-in big blind still sets the full big blind as the price to call.
- Cards use two-character codes (`"AS"`, `"TH"`); `Card.code` is also the PNG name in `assets/cards`. `Card.value` runs 2–14 (ace high).
- `Deck.shuffle()` uses `secrets.SystemRandom` by default; tests build rigged decks with `Deck([...])` (cards are dealt from the end of the list). Hole cards go one per round starting at the small blind; then one card is burned before the flop, the turn and the river. `tests/test_hand.py::rigged_deck` builds such decks.
- UI strings live in `i18n.py` (`zh` and `en` must define identical keys and placeholders; `test_i18n.py` enforces this).
- `client/canvas_ui.py` (colors, rounded rects, chips, `CanvasButton`) is copied unchanged from blackjack_game.
- Card faces load from PNG via `PhotoImage(data=...)`; loading with `file=` failed intermittently under pytest output capture (seen in blackjack_game).
- tkinter is touched only on the main thread; the network thread passes messages through a `queue.Queue` polled with `root.after`.
