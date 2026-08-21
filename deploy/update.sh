#!/usr/bin/env bash
#
# Update a deployed bot to the latest committed code and restart it.
#
# Usage, on the host:
#   cd ~/LoopifyBot && bash deploy/update.sh
#
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$APP_DIR/.venv"
SERVICE_NAME="loopify-bot"

cd "$APP_DIR"

if [[ ! -d .git ]]; then
    cat >&2 <<'MSG'
!! This deployment is not a git checkout, so it cannot be updated with git.

   That happens when the code was copied over with rsync. To convert it in
   place, from the app directory:

     git init
     git remote add origin https://github.com/Isma-L154/LoopifyBot.git
     git fetch origin
     git reset --hard origin/main      # discards local edits — check first!

   Your .env and cookies.txt are gitignored and will not be touched.
MSG
    exit 1
fi

# Record where we were, so the comparison below survives fast-forwards,
# no-op pulls and anything that rewrites the reflog.
previous="$(git rev-parse HEAD)"

echo "==> Fetching..."
git pull --ff-only

if [[ "$previous" == "$(git rev-parse HEAD)" ]]; then
    echo "==> Already up to date."
fi

# Only rebuild the venv when the pinned set actually changed.
if git diff --quiet "$previous" HEAD -- requirements.txt; then
    echo "==> requirements.txt unchanged; skipping dependency install."
else
    echo "==> requirements.txt changed; reinstalling dependencies..."
    "$VENV_DIR/bin/pip" install --quiet --upgrade -r requirements.txt
fi

echo "==> Restarting $SERVICE_NAME..."
sudo systemctl restart "$SERVICE_NAME"

sleep 2
if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "==> Running commit $(git rev-parse --short HEAD)."
else
    echo "!! Service is not active. Recent logs:" >&2
    sudo journalctl -u "$SERVICE_NAME" -n 30 --no-pager
    exit 1
fi
