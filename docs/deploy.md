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

Server settings live in an environment file; a server password, if you set one, stays off the command line, where any local user could read it with `ps`:

```bash
sudo mkdir -p /etc/holdem
sudo cp /opt/holdem/app/deploy/holdem.env.example /etc/holdem/holdem.env
sudo chown root:holdem /etc/holdem/holdem.env && sudo chmod 640 /etc/holdem/holdem.env
sudo nano /etc/holdem/holdem.env
```

Players open rooms themselves, each with its own blinds, password and bots, so there is little to set here. `HOLDEM_PASSWORD` is an optional password for the whole server (leave it empty; room passwords cover most needs). `HOLDEM_ARGS` takes any other server option: `--max-rooms`, `--max-bots`, `--empty-room-timeout`, `--action-timeout`, `--reconnect-grace`, `--hand-delay`. `python -m holdem.server --help` lists them all.

## 3. Run it as a service

```bash
sudo cp /opt/holdem/app/deploy/holdem.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now holdem
systemctl status holdem
journalctl -u holdem -f    # the server log: joins, disconnects, hands
```

The unit listens on `127.0.0.1:8765`, so only a reverse proxy on the same machine can reach it. Pick one of the ways in below.

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

Nothing is encrypted this way, including room passwords, so prefer 4a for anything that is not a throwaway game.

## 4c. Encrypted when port 443 is already taken

If something else on the VPS already owns 443 (a proxy, another website), run the encrypted front end on another port, for example `wss://poker.example.com:8765`. Two things decide how:

- Cloudflare's proxy (orange cloud) only passes ports 443, 2053, 2083, 2087, 2096 and 8443. A DNS-only (grey cloud) record works with any port.
- Caddy's automatic certificates need port 80 or 443. If another tool already renews a certificate for the same name on port 80 (acme.sh in standalone mode, for instance), keep Caddy off port 80 and reuse that certificate, as below. If port 80 is free for good, drop the `auto_https off` and `tls` lines and Caddy gets its own certificate.

Move the game server to another local port, so Caddy can take 8765: add `--port 8766` to `HOLDEM_ARGS` in `/etc/holdem/holdem.env` and `sudo systemctl restart holdem`.

Write the Caddy files before installing Caddy, because the package starts it at once with a default site on port 80. `/etc/caddy/Caddyfile`:

```
{
	# No certificates of its own and nothing on port 80.
	auto_https off
}

https://poker.example.com:8765 {
	tls /run/credentials/caddy.service/fullchain.pem /run/credentials/caddy.service/privkey.pem
	reverse_proxy 127.0.0.1:8766
}
```

The existing certificate usually belongs to root. systemd can hand Caddy a copy without changing its permissions; `/etc/systemd/system/caddy.service.d/holdem-cert.conf` (use your certificate's paths):

```ini
[Service]
LoadCredential=fullchain.pem:/root/cert/poker.example.com/fullchain.pem
LoadCredential=privkey.pem:/root/cert/poker.example.com/privkey.pem
```

That copy is taken when Caddy starts, so Caddy has to restart after each renewal. A path unit does it: `/etc/systemd/system/holdem-cert-reload.path`

```ini
[Unit]
Description=Watch the certificate for renewals

[Path]
PathChanged=/root/cert/poker.example.com/fullchain.pem
Unit=holdem-cert-reload.service

[Install]
WantedBy=multi-user.target
```

and `/etc/systemd/system/holdem-cert-reload.service`

```ini
[Unit]
Description=Restart Caddy so it loads the renewed certificate

[Service]
Type=oneshot
ExecStart=/usr/bin/systemctl try-restart caddy.service
```

Then install and start everything (`--force-confold` keeps your Caddyfile instead of the package's):

```bash
sudo apt install -o Dpkg::Options::=--force-confold caddy
sudo systemctl daemon-reload && sudo systemctl restart caddy
sudo systemctl enable --now holdem-cert-reload.path
ss -ltn 'sport = :80'    # should list nothing
```

Players join with `wss://poker.example.com:8765`. When Caddy restarts for a renewal, connections drop for a moment and clients reconnect to their seats by themselves.

## Updating

```bash
sudo -u holdem git -C /opt/holdem/app pull
sudo -u holdem /opt/holdem/venv/bin/pip install /opt/holdem/app
sudo systemctl restart holdem
```

Rebuild and hand out the desktop client from the same commit, since a protocol change makes old clients unable to join.
