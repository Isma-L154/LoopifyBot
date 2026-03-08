"""
Lyrics service — uses lyricsgenius (Genius API).

Required .env variable:
    GENIUS_TOKEN
"""

import os
import asyncio
import lyricsgenius
from typing import Optional


def _get_client() -> lyricsgenius.Genius:
    token = os.getenv("GENIUS_TOKEN")
    genius = lyricsgenius.Genius(token, quiet=True, skip_non_songs=True)
    genius.remove_section_headers = False
    return genius


async def fetch(title: str, artist: str = "", *, loop=None) -> Optional[dict]:
    """
    Search Genius for lyrics.
    Returns dict with keys: title, artist, lyrics, url — or None.
    """
    loop = loop or asyncio.get_event_loop()

    def _search():
        genius = _get_client()
        if artist:
            song = genius.search_song(title, artist)
        else:
            song = genius.search_song(title)
        return song

    try:
        song = await loop.run_in_executor(None, _search)
        if not song:
            return None
        return {
            "title":  song.title,
            "artist": song.artist,
            "lyrics": song.lyrics,
            "url":    song.url,
        }
    except Exception as e:
        print(f"[Lyrics] Error: {e}")
        return None
