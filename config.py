"""Central configuration, loaded from environment variables (.env)."""

import os
import sys
import logging
import platform
import subprocess
from typing import Optional

from dotenv import load_dotenv

# python-dotenv is the single owner of .env — the systemd unit deliberately does
# not set EnvironmentFile, because the two parsers disagree about quoting and
# inline comments, and a value they disagree on fails silently and looks like a
# bad credential.
#
# The path is anchored to the project root rather than left to search upward
# from the working directory, so the same file is picked up whether the bot is
# started by systemd, from a shell anywhere, or by the test runner.
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

# ── Bot ───────────────────────────────────────────────────────────────
DISCORD_TOKEN  = os.getenv("DISCORD_TOKEN")
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!")

# ── Genius (Lyrics) ───────────────────────────────────────────────────
GENIUS_TOKEN = os.getenv("GENIUS_TOKEN")

# ── Misc ──────────────────────────────────────────────────────────────
COOKIES_PATH = os.getenv("COOKIES_PATH")
LOG_LEVEL    = os.getenv("LOG_LEVEL", "INFO").upper()

# ── Cogs to load ──────────────────────────────────────────────────────
COGS = [
    "cogs.music",
    "cogs.effects",
    "cogs.lyrics",
]


def configure_logging() -> None:
    """Set up structured logging. Never logs tokens or secrets."""
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # discord.py is chatty at INFO; keep it at WARNING unless debugging.
    logging.getLogger("discord").setLevel(logging.WARNING)


def _first_line(cmd: list[str], cwd: Optional[str] = None) -> Optional[str]:
    """First line of a command's output, or None if it can't be run."""
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    lines = result.stdout.strip().splitlines()
    return lines[0].strip() if lines else None


def _ffmpeg_version() -> str:
    # "ffmpeg version 6.1.1-3ubuntu5 Copyright (c) 2000-2023 ..."
    line = _first_line(["ffmpeg", "-version"])
    parts = line.split() if line else []
    return parts[2] if len(parts) > 2 and parts[1] == "version" else "unknown"


def runtime_versions() -> dict:
    """
    What this instance is actually running.

    Every lookup degrades to "unknown" rather than failing the boot — a host
    deployed without git, or without FFmpeg on PATH, should still start and say
    so plainly.
    """
    return {
        "commit": _first_line(
            ["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT) or "unknown",
        "yt_dlp": _first_line([sys.executable, "-m", "yt_dlp", "--version"]) or "unknown",
        "ffmpeg": _ffmpeg_version(),
        "python": platform.python_version(),
    }


def log_runtime() -> None:
    """
    Record the running versions at startup.

    Without this there is no way to tell from `journalctl` which commit or which
    yt-dlp was live when something broke — and a stale yt-dlp is the single most
    common cause of YouTube failing.
    """
    v = runtime_versions()
    logging.getLogger("loopify").info(
        "Running commit %s — yt-dlp %s, FFmpeg %s, Python %s",
        v["commit"], v["yt_dlp"], v["ffmpeg"], v["python"],
    )


def validate() -> None:
    """Fail fast with a clear message if required config is missing."""
    missing = [name for name, val in {
        "DISCORD_TOKEN": DISCORD_TOKEN,
    }.items() if not val]
    if missing:
        print(
            f"❌ Missing required environment variable(s): {', '.join(missing)}\n"
            f"   Set them in a .env file (see .env.example).",
            file=sys.stderr,
        )
        sys.exit(1)

    if not GENIUS_TOKEN:
        logging.getLogger("loopify").warning(
            "GENIUS_TOKEN missing — the !lyrics command will not work."
        )
