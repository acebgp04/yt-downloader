# Deploying to the Proxmox Ubuntu VM (video.acetools.dev)

Architecture: Gunicorn (single worker, multiple threads — required because job
progress is tracked in an in-memory dict, see `jobs.py`) behind a Cloudflare
Tunnel. No inbound firewall ports needed; the tunnel makes an outbound
connection from the VM to Cloudflare.

## 1. One-time VM prep

Run as a user with sudo on the Ubuntu VM:

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip ffmpeg git

# Dedicated, unprivileged user to run the app
sudo useradd --system --create-home --shell /usr/sbin/nologin ytdl
```

## 2. Get the code onto the VM

The GitHub repo is private, so the VM needs its own auth. Simplest option —
authenticate `gh` on the VM the same way we did locally (device code flow,
no browser needed on the VM itself):

```bash
sudo -u ytdl -H bash -c '
  curl -sS https://webi.sh/gh | sh   # or: sudo apt install gh
  gh auth login                       # follow the device-code prompt
  cd /opt || true
'
sudo mkdir -p /opt/yt-downloader
sudo chown ytdl:ytdl /opt/yt-downloader
sudo -u ytdl gh repo clone acebgp04/yt-downloader /opt/yt-downloader
```

(A deploy key is the more "correct" long-term approach for a server identity,
but `gh auth login` is fine to start with.)

## 3. Python environment

```bash
sudo -u ytdl -H bash -c '
  cd /opt/yt-downloader
  python3 -m venv venv
  ./venv/bin/pip install --upgrade pip
  ./venv/bin/pip install -r requirements.txt
'
```

## 4. Production `.env`

Create `/opt/yt-downloader/.env` (owned by `ytdl`, mode `600`) based on
`.env.example`, with these production-specific changes:

```
FLASK_SECRET_KEY=<generate a new one — do NOT reuse the dev value>
STRIPE_SECRET_KEY=sk_test_...      # switch to sk_live_... only when ready to charge real cards
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_PRICE_ID=price_...
STRIPE_WEBHOOK_SECRET=whsec_...    # from step 7 below — a NEW value, not the local `stripe listen` one
BASE_URL=https://video.acetools.dev
GMAIL_ADDRESS=...
GMAIL_APP_PASSWORD=...
```

Generate a fresh secret key:
```bash
sudo -u ytdl /opt/yt-downloader/venv/bin/python3 -c "import secrets; print(secrets.token_hex(32))"
```

```bash
sudo chmod 600 /opt/yt-downloader/.env
```

## 5. Sanity-check it runs

```bash
sudo -u ytdl -H bash -c 'cd /opt/yt-downloader && ./venv/bin/python app.py'
```
Confirm it starts cleanly and `app.db` gets created, then Ctrl+C.

## 6. systemd service

```bash
sudo cp /opt/yt-downloader/deploy/yt-downloader.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now yt-downloader
sudo systemctl status yt-downloader
sudo journalctl -u yt-downloader -f   # watch logs
```

## 7. Cloudflare Tunnel

```bash
# Install cloudflared (Ubuntu/Debian package)
curl -L https://pkg.cloudflare.com/cloudflare-main.gpg | sudo gpg --dearmor -o /usr/share/keyrings/cloudflare-main.gpg
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared jammy main' | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt install cloudflared

# Auth (prints a URL — open it on any device logged into your Cloudflare account)
cloudflared tunnel login

# Create the tunnel — this generates a credentials JSON and prints the tunnel ID
cloudflared tunnel create yt-downloader

# Route the domain to this tunnel (requires video.acetools.dev's zone to be on Cloudflare)
cloudflared tunnel route dns yt-downloader video.acetools.dev
```

Copy `deploy/cloudflared-config.yml` to `/etc/cloudflared/config.yml`, filling
in the `<TUNNEL_ID>` printed above (and matching credentials-file path — it's
usually already at `/root/.cloudflared/<TUNNEL_ID>.json` or wherever
`tunnel create` put it; move/reference it from there).

```bash
sudo cloudflared service install
sudo systemctl enable --now cloudflared
sudo systemctl status cloudflared
```

Visit `https://video.acetools.dev` — should hit the app through the tunnel.

## 8. Point Stripe's webhook at production

In the Stripe dashboard (same test mode, or live mode once you're ready):
**Developers → Webhooks → Add endpoint**
- URL: `https://video.acetools.dev/stripe/webhook`
- Events: `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`

Copy the signing secret it gives you into `.env`'s `STRIPE_WEBHOOK_SECRET`
(this replaces the local `stripe listen` secret — they're different), then:

```bash
sudo systemctl restart yt-downloader
```

Do a real test subscription against production to confirm the webhook fires
(check `journalctl -u yt-downloader` for the POST to `/stripe/webhook`).

## Redeploying after code changes

```bash
sudo -u ytdl -H bash -c '
  cd /opt/yt-downloader
  git pull
  ./venv/bin/pip install -r requirements.txt
'
sudo systemctl restart yt-downloader
```

## Known constraints carried over from local dev

- **Must stay at 1 Gunicorn worker.** Job progress lives in an in-memory
  dict (`jobs.py`); running more than one worker process would split
  requests across dicts that don't share state, breaking progress polling
  and downloads silently.
- **SQLite** (`app.db`) is fine at this scale but doesn't love heavy
  concurrent writes — not a concern with 1 worker, but worth knowing if you
  ever need to scale beyond a single process (would mean Postgres + moving
  job state to Redis at the same time).
- Stripe test-mode keys remain in place until you deliberately switch to
  `sk_live_...`/`pk_live_...` — no real charges will happen until then.
