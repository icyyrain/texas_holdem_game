# Texas Hold'em Game

A multiplayer No-Limit Texas Hold'em game for play chips only. Players connect a tkinter desktop client to a websocket server, which runs either inside a player's client ("Create Room") or headless on a VPS. See the [design spec](docs/specs/2026-11-15-texas-holdem-design.md).

Work in progress: the game rules (`holdem.core`: hand evaluation, betting, side pots, full hand flow) are done and tested, with a terminal simulator to play them by hand; the server and client are not written yet.

## Setup

```powershell
conda env update -f environment.yml --prune
```

## Test

```powershell
conda run -n holdem-game pytest
```

## Rules simulator

A hot-seat terminal game for checking the rules by hand: every seat plays from one terminal and all hole cards are shown.

```powershell
conda run -n holdem-game python -m holdem.sim --players 3
```

Type `f` fold, `x` check, `c` call, `b 10` bet 10, `r 30` raise to 30, `a` all-in, `q` quit. Other options: `--stack 200`, `--blinds 1/2`, `--bots 2,3` (those players act randomly), `--hands 20`, `--seed 7` (repeatable cards and bots).

## Card assets

Card artwork is CC0, generated at <https://www.me.uk/cards/>. See [assets/README.md](assets/README.md).
