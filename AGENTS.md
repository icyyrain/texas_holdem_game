# Agent Notes

Design spec: `docs/specs/2026-11-15-texas-holdem-design.md`.

## Commands

- Tests: `conda run -n holdem-game pytest`
- Windows exe: `powershell -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1` (writes `dist\Holdem.exe`; needs the client entry point `src\holdem\client\app.py`)
- `scripts/prepare_card_images.py` needs cairo DLLs from the conda env, so run it through `conda run` (activation puts `Library\bin` on PATH).

## Architecture

- `holdem.core` is the only source of game rules and does no I/O. Chips are integers.
- Cards use two-character codes (`"AS"`, `"TH"`); `Card.code` is also the PNG name in `assets/cards`. `Card.value` runs 2–14 (ace high).
- `Deck.shuffle()` uses `secrets.SystemRandom` by default; tests build rigged decks with `Deck([...])` (cards are dealt from the end of the list).
- UI strings live in `i18n.py` (`zh` and `en` must define identical keys and placeholders; `test_i18n.py` enforces this).
- `client/canvas_ui.py` (colors, rounded rects, chips, `CanvasButton`) is copied unchanged from blackjack_game.
- Card faces load from PNG via `PhotoImage(data=...)`; loading with `file=` failed intermittently under pytest output capture (seen in blackjack_game).
- tkinter is touched only on the main thread; the network thread passes messages through a `queue.Queue` polled with `root.after`.
