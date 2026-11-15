# Texas Hold'em Game

A multiplayer No-Limit Texas Hold'em game for play chips only. Players connect a tkinter desktop client to a websocket server, which runs either inside a player's client ("Create Room") or headless on a VPS. See the [design spec](docs/specs/2026-11-15-texas-holdem-design.md).

Work in progress: the game rules (`holdem.core`: hand evaluation, betting, side pots, full hand flow) are done and tested; the server and client are not written yet.

## Setup

```powershell
conda env update -f environment.yml --prune
```

## Test

```powershell
conda run -n holdem-game pytest
```

## Card assets

Card artwork is CC0, generated at <https://www.me.uk/cards/>. See [assets/README.md](assets/README.md).
