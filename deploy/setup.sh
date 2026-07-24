#!/usr/bin/env bash
#
# LoopifyBot — EC2 provisioning script (Ubuntu 22.04/24.04 on ARM t4g or x86).
#
# Idempotent: safe to re-run. Installs system deps, creates a Python venv,
# installs requirements and registers a systemd service that keeps the bot
# running, restarts it on failure and starts it on boot.
#
# Usage (as the default 'ubuntu' user, from the repo root):
#   bash deploy/setup.sh
#
set -euo pipefail

APP_USER="${SUDO_USER:-$USER}"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$APP_DIR/.venv"
SERVICE_NAME="loopify-bot"

echo "==> LoopifyBot setup"
echo "    user : $APP_USER"
echo "    dir  : $APP_DIR"

# ── 1. System dependencies ────────────────────────────────────────────
echo "==> Installing system packages (ffmpeg, python3, venv, git, unzip)..."
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ffmpeg python3 python3-venv python3-pip git unzip curl

# ── 1b. Deno (JS runtime for yt-dlp's YouTube signature solving) ───────
# YouTube's web clients require solving a JS "nsig" challenge; yt-dlp uses a
# JavaScript runtime for it. Without one, many YouTube tracks won't resolve.
if ! command -v deno >/dev/null 2>&1; then
    echo "==> Installing Deno (JS runtime)..."
    ARCH="$(uname -m)"   # aarch64 or x86_64
    case "$ARCH" in
        aarch64) DENO_TARGET="aarch64-unknown-linux-gnu" ;;
        x86_64)  DENO_TARGET="x86_64-unknown-linux-gnu" ;;
        *) echo "!! Unknown arch $ARCH — skipping Deno"; DENO_TARGET="" ;;
    esac
    if [[ -n "$DENO_TARGET" ]]; then
        curl -fsSL -o /tmp/deno.zip \
            "https://github.com/denoland/deno/releases/latest/download/deno-${DENO_TARGET}.zip"
        unzip -o /tmp/deno.zip -d /tmp >/dev/null
        sudo mv -f /tmp/deno /usr/local/bin/deno
        sudo chmod +x /usr/local/bin/deno
        rm -f /tmp/deno.zip
    fi
fi
command -v deno >/dev/null 2>&1 && echo "==> Deno: $(deno --version | head -1)"

# ── 2. Python virtual environment ─────────────────────────────────────
echo "==> Creating virtual environment..."
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip wheel
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"

# ── 3. .env check & lock-down ─────────────────────────────────────────
if [[ -f "$APP_DIR/.env" ]]; then
    chmod 600 "$APP_DIR/.env"          # secrets readable only by the owner
    echo "==> Locked down .env (chmod 600)."
else
    echo "!!  WARNING: $APP_DIR/.env not found."
    echo "    Copy .env.example to .env and fill in DISCORD_TOKEN before starting."
fi

# ── 4. systemd service ────────────────────────────────────────────────
echo "==> Installing systemd service '$SERVICE_NAME'..."
sudo tee "/etc/systemd/system/${SERVICE_NAME}.service" >/dev/null <<UNIT
[Unit]
Description=LoopifyBot Discord Music Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
# Keep caches inside the (writable) app dir since HOME is read-only below.
Environment=XDG_CACHE_HOME=$APP_DIR/.cache
Environment=DENO_DIR=$APP_DIR/.cache/deno
ExecStart=$VENV_DIR/bin/python $APP_DIR/main.py
Restart=on-failure
RestartSec=5
# Give the bot a moment to shut down cleanly.
KillSignal=SIGINT
TimeoutStopSec=15
# ── Sandboxing / hardening ──
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$APP_DIR
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictNamespaces=true
LockPersonality=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
# Cap resources so a runaway can't take down a 1 GB box.
MemoryMax=768M
TasksMax=256

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"

echo ""
echo "==> Done. Manage the bot with:"
echo "    sudo systemctl start   $SERVICE_NAME"
echo "    sudo systemctl status  $SERVICE_NAME"
echo "    sudo journalctl -u $SERVICE_NAME -f   # live logs"
