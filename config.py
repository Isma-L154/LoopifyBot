import os
from dotenv import load_dotenv

load_dotenv()

# ── Bot ───────────────────────────────────────────────────────────────
DISCORD_TOKEN    = os.getenv("DISCORD_TOKEN")
COMMAND_PREFIX   = os.getenv("COMMAND_PREFIX", "!")

# ── Spotify ───────────────────────────────────────────────────────────
SPOTIFY_CLIENT_ID     = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

# ── Genius (Lyrics) ───────────────────────────────────────────────────
GENIUS_TOKEN = os.getenv("GENIUS_TOKEN")

# ── Cogs to load ──────────────────────────────────────────────────────
COGS = [
    "cogs.music",
    "cogs.effects",
    "cogs.lyrics",
]
