"""Central configuration, loaded from environment variables (.env)."""

import os
import sys
import logging

from dotenv import load_dotenv

load_dotenv()

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
