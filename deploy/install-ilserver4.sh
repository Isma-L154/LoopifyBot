#!/usr/bin/env bash
#
# Deploy one commit of the bot to ilserver4. Not run by hand: ilserver4-deploy
# (Isma-L154/Project_ilserver4) runs it as root, from its own export of that
# commit, whenever main changes one of its inputs in deploy/manifest.
#
# update.sh does the work, as the checkout's owner, so git and pip never leave
# root-owned files behind in it. It runs from this export rather than from the
# checkout, so a change to update.sh applies in the same deployment, and from a
# root-owned copy because the export is only readable by root.
#
set -euo pipefail

APP_DIR=/home/ashesofil/LoopifyBot
COMMIT="${DEPLOY_COMMIT:?set by ilserver4-deploy: the commit being deployed}"
APP_USER="$(stat -c '%U' "$APP_DIR")"

script="$(mktemp)"
trap 'rm -f "$script"' EXIT
install -m 0644 deploy/update.sh "$script"

runuser -u "$APP_USER" -- env APP_DIR="$APP_DIR" bash "$script" "$COMMIT"
