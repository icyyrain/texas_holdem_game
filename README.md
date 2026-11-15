# Texas Hold'em Game

A multiplayer No-Limit Texas Hold'em game for play chips only. Players connect a tkinter desktop client to a websocket server, which runs either inside a player's client ("Create Room") or headless on a VPS. See the [design spec](docs/specs/2026-11-15-texas-holdem-design.md).

Version 1 is complete: the game rules, the WebSocket server (with computer players, action timers and reconnecting), the desktop client (also as a Windows exe) and a minimal text client.

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

Type `f` fold, `x` check, `c` call, `b 10` bet 10, `r 30` raise to 30, `a` all-in, `q` quit. Other options: `--stack 200`, `--blinds 1/2`, `--bots 2,3` (the computer plays those seats), `--bot-style random` (bots pick random legal moves instead of playing properly), `--hands 20`, `--seed 7` (repeatable cards and bots).

## Desktop client

```powershell
conda run -n holdem-game python -m holdem.client
```

- **Create Room** starts a server inside the client and shows your LAN IP in the top bar for others to join. You can add bots.
- **Join Room** connects to someone else's room or a VPS server.

The first time you create a room, Windows Firewall asks whether to let the game on the network; allow it on private networks or other players cannot connect.

Click an empty seat to sit down. On your turn, use Fold, Check/Call, and Bet/Raise with the slider or the 1/2 pot, 3/4 pot, Pot and All-in presets. A bar under the player to act shows the time left; when it runs out the server checks or folds for them and sits them out. **Sit out** skips the next hands until you click **I'm back**. If the connection drops, the client reconnects to the same seat on its own. Players on other networks need port forwarding or a virtual LAN such as Tailscale or ZeroTier.

## Server

```powershell
conda run -n holdem-game python -m holdem.server --host 0.0.0.0 --port 8765 --password secret
```

Options: `--blinds 1/2`, `--min-buy-in` / `--max-buy-in` (default 20 / 100 big blinds), `--seats 9`, `--bots 2` (computer players that sit at the table), `--bot-style smart` or `random`, `--hand-delay 3`, `--bot-think 1`, `--action-timeout 30` (seconds per decision), `--reconnect-grace 120` (seconds a disconnected player keeps their seat). The password can also come from the `HOLDEM_PASSWORD` environment variable.

To run a table on a Linux VPS as a systemd service, with `wss://` through Caddy or nginx, see [docs/deploy.md](docs/deploy.md).

## Windows exe

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1
```

This writes `dist\Holdem.exe` (about 15 MB), a single file with the card images inside. It can create rooms as well as join them, so players need nothing else. Build it from the same commit as any server it will join.

## Text client

A bare-bones client for trying the server; open one terminal per player:

```powershell
conda run -n holdem-game python -m holdem.textclient --name alice --host 127.0.0.1 --password secret
```

Type `sit 1 200` to take seat 1 with 200 chips, then play with the same keys as the simulator. `state` shows the table, `leave` stands up, `rebuy 100` adds chips between hands, `away` / `back` sit out and return, `quit` exits. The text client does not reconnect by itself.

## Computer players

Bots (`holdem/strategy.py`) rank their starting hands with the Chen formula before the flop. After it they estimate their equity by Monte Carlo: a few hundred times they deal out the rest of the board and hands for the opponents still in, giving opponents who bet or raised stronger hands, and count how often they win. They call when that beats the pot odds, bet and raise their strong hands, and bluff now and then. They do not track how individual opponents play.

## Card assets

Card artwork is CC0, generated at <https://www.me.uk/cards/>. See [assets/README.md](assets/README.md).
