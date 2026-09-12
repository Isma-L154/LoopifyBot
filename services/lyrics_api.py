"""
Lyrics service — Genius, via lyricsgenius.

Required .env variable: ``GENIUS_TOKEN`` (see :mod:`config`).
"""

import asyncio
import logging
from functools import lru_cache
from typing import Optional

import lyricsgenius

from config import GENIUS_TOKEN

log = logging.getLogger("loopify.lyrics")

# Genius is reached from a thread, so a lookup that never returns pins that
# thread — and a pinned executor thread delays the event loop's shutdown. The
# library's own default is 5s; this trades a little patience for the occasional
# slow response while staying well inside the shutdown budget (see #35).
GENIUS_TIMEOUT = 10.0


@lru_cache(maxsize=1)
def _client() -> lyricsgenius.Genius:
    """
    One client for the process — rebuilding it per query buys nothing.

    There is deliberately no verbosity argument. ``quiet`` belonged to
    lyricsgenius 2.x and raises ``TypeError`` on 3.x, which is what broke
    ``!lyrics`` entirely; 3.x prints nothing of its own, so there is nothing left
    to silence. The other two are passed because their behaviour is relied on,
    not because the defaults differ.
    """
    return lyricsgenius.Genius(
        GENIUS_TOKEN,
        skip_non_songs=True,           # tracklists and credits pages aren't lyrics
        remove_section_headers=False,  # keep [Chorus] and friends in the embed
        timeout=GENIUS_TIMEOUT,
    )


async def fetch(title: str, artist: str = "") -> Optional[dict]:
    """
    Search Genius for lyrics.

    Returns a dict with ``title``, ``artist``, ``lyrics`` and ``url``, or None
    when there is no match, no token, or Genius is unreachable — a missing lyric
    is never a reason to take the bot down.
    """
    if not GENIUS_TOKEN:
        # Constructing a client without one makes lyricsgenius fall back to
        # $GENIUS_ACCESS_TOKEN and raise KeyError. config.validate() already
        # warned about this at startup.
        log.debug("No GENIUS_TOKEN configured; skipping lookup for %r", title)
        return None

    # The running loop, not a passed-in one: reaching for `bot.loop` before
    # the gateway is up raises, and this never needs a different loop anyway.
    loop = asyncio.get_running_loop()

    def _search():
        client = _client()
        # An empty artist matches worse than no artist at all.
        return client.search_song(title, artist) if artist else client.search_song(title)

    try:
        song = await loop.run_in_executor(None, _search)
    except Exception as e:
        log.warning("Genius lookup failed for %r: %s", title, e)
        return None
    if not song:
        return None
    return {
        "title":  song.title,
        "artist": song.artist,
        "lyrics": song.lyrics,
        "url":    song.url,
    }
