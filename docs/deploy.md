# Running a table on a VPS

The server is one Python process holding one table in memory. It needs Python 3.11 or newer and the `websockets` package, and no GUI libraries. These steps assume Debian 12 or Ubuntu 24.04 (both ship a new enough Python) and use the files in [`deploy/`](../deploy).

The table lives only in memory: restarting the service ends the hand in progress and clears every seat (connected clients reconnect, but have to sit down and buy in again). Clients and the server must come from the same version of this project, because the server turns away a client that speaks a different protocol version.

## 1. Install

```bash
sudo apt install python3 python3-venv git
sudo useradd --system --home /opt/holdem --shell /usr/sbin/nologin holdem
sudo mkdir -p /opt/holdem && sudo chown holdem:holdem /opt/holdem
sudo -u holdem git clone https://github.com/icyyrain/texas_holdem_game.git /opt/holdem/app
sudo -u holdem python3 -m venv /opt/holdem/venv
sudo -u holdem /opt/holdem/venv/bin/pip install /opt/holdem/app
```

Try it in the foreground first (Ctrl+C stops it):

```bash
sudo -u holdem /opt/holdem/venv/bin/python -m holdem.server --host 127.0.0.1 --port 8765
```

## 2. Settings

The room password goes in an environment file rather than on the command line, where any local user could read it with `ps`:

```bash
sudo mkdir -p /etc/holdem
sudo cp /opt/holdem/app/deploy/holdem.env.example /etc/holdem/holdem.env
sudo chown root:holdem /etc/holdem/holdem.env && sudo chmod 640 /etc/holdem/holdem.env
sudo nano /etc/holdem/holdem.env
```

`HOLDEM_PASSWORD` is the room password (leave it empty for an open room). `HOLDEM_ARGS` takes any other server option: `--blinds 1/2`, `--min-buy-in` / `--max-buy-in`, `--seats`, `--bots`, `--action-timeout`, `--reconnect-grace`, `--hand-delay`. `python -m holdem.server --help` lists them all.

## 3. Run it as a service

```bash
sudo cp /opt/holdem/app/deploy/holdem.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now holdem
systemctl status holdem
journalctl -u holdem -f    # the server log: joins, disconnects, hands
```

The unit listens on `127.0.0.1:8765`, so only a reverse proxy on the same machine can reach it. Pick one of the two ways in below.

## 4a. Encrypted, behind a reverse proxy (recommended)

Point a DNS name (here `poker.example.com`) at the VPS, open ports 80 and 443, and put Caddy or nginx in front. Players then join with the full URL `wss://poker.example.com` in the client's host field (the port field is ignored when the host is a URL); the text client takes it as `--host wss://poker.example.com`.

**Caddy** fetches and renews the certificate by itself:

```bash
sudo apt install caddy
sudo cp /opt/holdem/app/deploy/Caddyfile /etc/caddy/Caddyfile   # change the name first
sudo systemctl reload caddy
```

**nginx** needs a certificate, for example from certbot:

```bash
sudo apt install nginx certbot python3-certbot-nginx
sudo certbot certonly --nginx -d poker.example.com
sudo cp /opt/holdem/app/deploy/nginx.conf /etc/nginx/conf.d/holdem.conf   # change the name first
sudo nginx -t && sudo systemctl reload nginx
```

With `ufw`: `sudo ufw allow 80,443/tcp`.

## 4b. Plain ws://, no proxy

For a quick game you can skip the proxy and expose the server directly: put `--host 0.0.0.0` in place of `--host 127.0.0.1` in the unit's `ExecStart`, run `sudo systemctl daemon-reload && sudo systemctl restart holdem`, and open the port (`sudo ufw allow 8765/tcp`, plus your provider's firewall). Players join with the VPS address and port 8765.

Nothing is encrypted this way, including the room password, so prefer 4a for anything that is not a throwaway game.

## Updating

```bash
sudo -u holdem git -C /opt/holdem/app pull
sudo -u holdem /opt/holdem/venv/bin/pip install /opt/holdem/app
sudo systemctl restart holdem
```

Rebuild and hand out the desktop client from the same commit, since a protocol change makes old clients unable to join.
