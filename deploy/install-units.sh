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
UPDATER_BIN="/usr/local/sbin/$UPDATER_NAME"
# Whoever owns the checkout is who the bot runs as.
APP_USER="$(stat -c '%U' "$APP_DIR")"

# The bot may only reach the public internet. !play hands user links to yt-dlp,
# which follows redirects and resolves DNS on its own, so the application's
# check (services.media.is_public_url) cannot stop a link from ending up on
# localhost or the LAN. The kernel refuses those connections no matter how the
# address was reached. Covered: 0.0.0.0/8 (Linux routes it to the host itself),
# loopback, link-local (cloud metadata lives there), RFC 1918, CGNAT,
# benchmarking, multicast, and IPv6 ULA. tests/test_service_sandbox.py keeps it
# covering every private range and no public one.
PRIVATE_NETWORKS="0.0.0.0/8 127.0.0.0/8 ::1/128 169.254.0.0/16 fe80::/10 224.0.0.0/4 ff00::/8 10.0.0.0/8 100.64.0.0/10 172.16.0.0/12 192.168.0.0/16 198.18.0.0/15 fc00::/7"
# DNS must still work, so the resolvers in resolv.conf are let back through.
# On Ubuntu that is only the systemd-resolved stub, 127.0.0.53, which answers
# DNS and nothing else.
RESOLVERS="$(awk '$1 == "nameserver" { sub(/%.*/, "", $2); print $2 }' /etc/resolv.conf | tr '\n' ' ')"
for resolver in $RESOLVERS; do
    case "$resolver" in
        127.*|::1) ;;
        *) echo "!! DNS resolver $resolver is not on loopback: every service on that host stays reachable from the bot." >&2 ;;
    esac
done
if [ -z "$RESOLVERS" ]; then
    echo "!! No nameserver in /etc/resolv.conf; the bot will not be able to resolve names." >&2
fi

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
# Must stay above cogs.music.VOICE_CONNECT_TIMEOUT. discord.py reuses the voice
# *connect* timeout as the deadline for Discord to confirm a departure, and
# Client.close() waits that out for every voice client. With 15s here and 20s
# there, systemd SIGKILLed a shutdown that was going to finish — which skipped
# the reaping of FFmpeg and yt-dlp. A clean stop still takes milliseconds; this
# is only the ceiling. See issue #35, and tests/test_shutdown.py enforces it.
TimeoutStopSec=30
# ── Sandboxing / hardening ──
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
# Only the caches, and the cookies yt-dlp writes back. Never the code: the bot
# parses untrusted media, and a bot that can rewrite main.py keeps whatever got
# in across restarts.
ReadWritePaths=$APP_DIR/.cache -$APP_DIR/cookies.txt
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictNamespaces=true
LockPersonality=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
# Public internet only; see PRIVATE_NETWORKS above. Allow wins over Deny.
IPAddressDeny=$PRIVATE_NETWORKS
IPAddressAllow=$RESOLVERS
# Cap resources so a runaway can't take down a small box.
MemoryMax=768M
TasksMax=256

[Install]
WantedBy=multi-user.target
UNIT

# Owned by $APP_USER even when this runs as root: the bot writes nowhere else.
sudo install -d -o "$APP_USER" -g "$APP_USER" -m 0755 "$APP_DIR/.cache"

# yt-dlp is the one dependency that must NOT be pinned: YouTube changes its
# player constantly and a stale build stops resolving videos within weeks.
#
# The updater runs as root, so root runs a copy root owns. Run from the
# checkout, it was a file $APP_USER can write, and so one step from anything
# that ever runs as $APP_USER.
sudo install -o root -g root -m 0755 "$APP_DIR/deploy/update-ytdlp.sh" "$UPDATER_BIN"

sudo tee "/etc/systemd/system/${UPDATER_NAME}.service" >/dev/null <<UNIT
[Unit]
Description=Refresh yt-dlp for LoopifyBot
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
# Runs as root to restart the service; drops to $APP_USER for the pip install.
ExecStart=$UPDATER_BIN $APP_DIR
# pip runs as $APP_USER, whose home is read-only here; without this it warns
# on every run that its cache is unusable.
Environment=PIP_NO_CACHE_DIR=1
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$VENV_DIR
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictNamespaces=true
LockPersonality=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
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
