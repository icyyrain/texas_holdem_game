# Texas Hold'em Game

A multiplayer No-Limit Texas Hold'em game for play chips only. Players connect a tkinter desktop client to a websocket server, which runs either inside a player's client ("Create Room") or headless on a VPS. See the [design spec](docs/specs/2026-11-15-texas-holdem-design.md).

Work in progress: the game rules, the WebSocket server (with random bots), the desktop client and a minimal text client work; reconnecting, action timers and packaging are not done yet.

![Table screenshot](assets/table.png)

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

## Desktop client

```powershell
conda run -n holdem-game python -m holdem.client
```

- **Create Room** starts a server inside the client and shows your LAN IP in the top bar for others to join. You can add bots.
- **Join Room** connects to someone else's room or a VPS server.

Click an empty seat to sit down. On your turn, use Fold, Check/Call, and Bet/Raise with the slider or the 1/2 pot, 3/4 pot, Pot and All-in presets. Players on other networks need port forwarding or a virtual LAN such as Tailscale or ZeroTier.

## Server

```powershell
conda run -n holdem-game python -m holdem.server --host 0.0.0.0 --port 8765 --password secret
```

Options: `--blinds 1/2`, `--min-buy-in` / `--max-buy-in` (default 20 / 100 big blinds), `--seats 9`, `--bots 2` (random bots that sit at the table), `--hand-delay 3`, `--bot-think 1`.

## Text client

A bare-bones client for trying the server; open one terminal per player:

```powershell
conda run -n holdem-game python -m holdem.textclient --name alice --host 127.0.0.1 --password secret
```

Type `sit 1 200` to take seat 1 with 200 chips, then play with the same keys as the simulator. `state` shows the table, `leave` stands up, `rebuy 100` adds chips between hands, `quit` exits.

## Card assets

Card artwork is CC0, generated at <https://www.me.uk/cards/>. See [assets/README.md](assets/README.md).
