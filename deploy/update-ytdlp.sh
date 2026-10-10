#!/usr/bin/env bash
#
# Keep yt-dlp current. Run by loopify-ytdlp-update.timer, not by hand.
#
# YouTube changes its player and extractors constantly, so a yt-dlp that is a
# few weeks old starts failing to resolve videos and a few months old fails
# outright (a 2026.3.3 build returned HTTP 403 on every YouTube URL). Nothing
# else in the venv is upgraded here — the rest is pinned on purpose.
#
# Runs as root so it can restart the service, but drops to the app user for the
# pip install so the venv does not end up owned by root.
#
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$APP_DIR/.venv"
SERVICE_NAME="loopify-bot"
# The venv's owner is the user the bot runs as.
APP_USER="$(stat -c '%U' "$VENV_DIR")"

version() {
    runuser -u "$APP_USER" -- "$VENV_DIR/bin/python" -m yt_dlp --version 2>/dev/null || echo "none"
}

before="$(version)"

# A failed upgrade (offline, PyPI down, a broken release) must leave the working
# version in place. Never trade a running bot for a newer dependency.
if ! runuser -u "$APP_USER" -- "$VENV_DIR/bin/pip" install --quiet --upgrade "yt-dlp[default]"; then
    echo "yt-dlp upgrade failed; keeping $before"
    exit 0
fi

after="$(version)"

if [[ "$before" == "$after" ]]; then
    echo "yt-dlp already current ($after)"
    exit 0
fi

# Only restart when something actually changed, so playback is never
# interrupted for a no-op.
echo "yt-dlp $before -> $after; restarting $SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
