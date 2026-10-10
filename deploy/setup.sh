#!/usr/bin/env bash
#
# LoopifyBot — host provisioning script for Ubuntu 24.04/26.04, ARM or x86
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
PYTHON="python3.14"

echo "==> LoopifyBot setup"
echo "    user : $APP_USER"
echo "    dir  : $APP_DIR"

# ── 1. System dependencies ────────────────────────────────────────────
echo "==> Installing system packages (ffmpeg, $PYTHON, venv, git, unzip)..."
sudo apt-get update -y

# Ubuntu 26.04 ships python3.14; 24.04 only has 3.12, so there it comes from
# the deadsnakes PPA, installed alongside the system Python rather than over it.
if ! apt-cache show "$PYTHON" >/dev/null 2>&1; then
    echo "==> $PYTHON is not in this release's archive; adding the deadsnakes PPA..."
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y software-properties-common
    sudo add-apt-repository -y ppa:deadsnakes/ppa
    # unattended-upgrades only installs from origins it is told about; without
    # this the bot's interpreter would never get a security patch.
    echo 'Unattended-Upgrade::Origins-Pattern { "origin=LP-PPA-deadsnakes,codename=${distro_codename}"; };' \
        | sudo tee /etc/apt/apt.conf.d/52loopify-deadsnakes >/dev/null
fi

sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ffmpeg "$PYTHON" "$PYTHON-venv" git unzip curl

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
# A venv is bound to the interpreter that built it, so one from another Python
# (or one whose interpreter was removed) is rebuilt rather than reused. The bot
# runs from it, so stop the bot first instead of deleting files under it.
if [[ -d "$VENV_DIR" && "$("$VENV_DIR/bin/python" --version 2>&1)" != "$("$PYTHON" --version)" ]]; then
    echo "==> Existing venv is on another Python; stopping $SERVICE_NAME and rebuilding it..."
    sudo systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    rm -rf "$VENV_DIR"
fi

echo "==> Creating virtual environment..."
"$PYTHON" -m venv "$VENV_DIR"
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
