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


@lru_cache(maxsize=1)
def _client() -> lyricsgenius.Genius:
    """One client for the process — rebuilding it per query buys nothing."""
    genius = lyricsgenius.Genius(GENIUS_TOKEN, quiet=True, skip_non_songs=True)
    genius.remove_section_headers = False
    return genius


async def fetch(title: str, artist: str = "", *, loop=None) -> Optional[dict]:
    """
    Search Genius for lyrics.

    Returns a dict with ``title``, ``artist``, ``lyrics`` and ``url``, or None
    when there is no match or Genius is unreachable — a missing lyric is never
    a reason to take the bot down.
    """
    loop = loop or asyncio.get_event_loop()

    def _search():
        return _client().search_song(title, artist) if artist else _client().search_song(title)

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
