#!/usr/bin/env bash
#
# Write the systemd units and reload. The single source of truth for how this
# bot runs — both setup.sh (first install) and update.sh (every update) call it,
# so the running configuration always matches what is committed.
#
# Idempotent and cheap: two small files plus a daemon-reload. It does NOT start
# or restart anything; the caller decides that.
#
# Usage:
#   bash deploy/install-units.sh
#
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$APP_DIR/.venv"
SERVICE_NAME="loopify-bot"
UPDATER_NAME="loopify-ytdlp-update"
# Whoever owns the checkout is who the bot runs as.
APP_USER="$(stat -c '%U' "$APP_DIR")"

echo "==> Installing systemd units (user: $APP_USER, dir: $APP_DIR)"

sudo tee "/etc/systemd/system/${SERVICE_NAME}.service" >/dev/null <<UNIT
[Unit]
Description=LoopifyBot Discord Music Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
WorkingDirectory=$APP_DIR
# No EnvironmentFile on purpose. The bot reads .env with python-dotenv, and
# having systemd parse the same file too means two parsers with different rules
# for quoting and inline comments — they disagree silently, and the result looks
# like a bad credential rather than a parsing problem.
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
# Cap resources so a runaway can't take down a small box.
MemoryMax=768M
TasksMax=256

[Install]
WantedBy=multi-user.target
UNIT

# yt-dlp is the one dependency that must NOT be pinned: YouTube changes its
# player constantly and a stale build stops resolving videos within weeks.
chmod +x "$APP_DIR/deploy/update-ytdlp.sh"

sudo tee "/etc/systemd/system/${UPDATER_NAME}.service" >/dev/null <<UNIT
[Unit]
Description=Refresh yt-dlp for LoopifyBot
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
# Runs as root to restart the service; drops to $APP_USER for the pip install.
ExecStart=$APP_DIR/deploy/update-ytdlp.sh
UNIT

sudo tee "/etc/systemd/system/${UPDATER_NAME}.timer" >/dev/null <<UNIT
[Unit]
Description=Daily yt-dlp refresh for LoopifyBot

[Timer]
OnCalendar=daily
# Spread the load rather than hitting PyPI at midnight with everyone else.
RandomizedDelaySec=2h
# Catch up after downtime — important on a machine that isn't on 24/7.
Persistent=true

[Install]
WantedBy=timers.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl enable --now "${UPDATER_NAME}.timer"
