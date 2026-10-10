#!/usr/bin/env bash
#
# LoopifyBot — host provisioning script for Ubuntu 22.04/24.04, ARM or x86
# (a self-hosted machine or an EC2 instance).
#
# Idempotent: safe to re-run. Installs system deps, creates a Python venv,
# installs requirements and registers a systemd service that keeps the bot
# running, restarts it on failure and starts it on boot.
#
# Usage (as the user that owns the checkout, from the repo root):
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
        # A private directory, not a fixed name in /tmp: this binary ends up
        # in /usr/local/bin via sudo, so nobody else may get to place it first.
        DENO_TMP="$(mktemp -d)"
        curl -fsSL -o "$DENO_TMP/deno.zip" \
            "https://github.com/denoland/deno/releases/latest/download/deno-${DENO_TARGET}.zip"
        unzip -o "$DENO_TMP/deno.zip" -d "$DENO_TMP" >/dev/null
        sudo install -m 755 "$DENO_TMP/deno" /usr/local/bin/deno
        rm -rf "$DENO_TMP"
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

# ── 4. systemd units ─────────────────────────────────────────
# The unit definitions live in install-units.sh, which update.sh also runs, so
# the deployed configuration never drifts from what is committed.
bash "$APP_DIR/deploy/install-units.sh"

echo ""
echo "==> Done. Manage the bot with:"
echo "    sudo systemctl start   $SERVICE_NAME"
echo "    sudo systemctl status  $SERVICE_NAME"
echo "    sudo journalctl -u $SERVICE_NAME -f   # live logs"
echo ""
echo "    bash deploy/update.sh                 # pull latest code & restart"
echo "    systemctl list-timers '*ytdlp*'       # when yt-dlp refreshes next"
