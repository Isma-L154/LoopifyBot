"""
Spotify service — uses spotipy to fetch track/album/playlist metadata,
then resolves each track to a YouTube stream via the YouTube service.

Required .env variables:
    SPOTIFY_CLIENT_ID
    SPOTIFY_CLIENT_SECRET
"""

import os
import re
import asyncio
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from typing import Optional
from services import youtube


def _get_client() -> spotipy.Spotify:
    auth = SpotifyClientCredentials(
        client_id=os.getenv("SPOTIFY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
    )
    return spotipy.Spotify(auth_manager=auth)


def _parse_url(url: str) -> tuple[str, str]:
    """Return (type, id) from a Spotify URL. type is track|album|playlist."""
    match = re.search(r"spotify\.com/(track|album|playlist)/([A-Za-z0-9]+)", url)
    if match:
        return match.group(1), match.group(2)
    raise ValueError(f"Invalid Spotify URL: {url}")


def _track_query(track_obj: dict) -> str:
    """Build a YouTube search query from a Spotify track object."""
    name = track_obj["name"]
    artists = ", ".join(a["name"] for a in track_obj["artists"])
    return f"{name} {artists}"


# ── Public API ────────────────────────────────────────────────────────

async def resolve(url: str, *, loop=None) -> list[dict]:
    """
    Given a Spotify URL, return a list of track dicts ready for the queue.
    Each dict contains title, url (spotify), and a 'query' field for YouTube.
    For single tracks: resolves immediately to a YouTube stream.
    For albums/playlists: returns lightweight dicts (resolved lazily on play).
    """
    loop = loop or asyncio.get_event_loop()

    def _fetch():
        sp = _get_client()
        kind, sid = _parse_url(url)

        if kind == "track":
            track = sp.track(sid)
            return [_make_spotify_track(track)]

        elif kind == "album":
            album = sp.album(sid)
            return [_make_spotify_track(t) for t in album["tracks"]["items"]]

        elif kind == "playlist":
            results = sp.playlist_items(sid)
            tracks = []
            while results:
                for item in results["items"]:
                    t = item.get("track")
                    if t:
                        tracks.append(_make_spotify_track(t))
                results = sp.next(results) if results["next"] else None
            return tracks

    try:
        tracks = await loop.run_in_executor(None, _fetch)
    except Exception as e:
        print(f"[Spotify] Resolve error: {e}")
        return []

    # For a single track, resolve to YT immediately so it plays right away
    if len(tracks) == 1:
        yt_track = await youtube.search(tracks[0]["query"], loop=loop)
        if yt_track:
            yt_track["spotify_url"] = tracks[0]["spotify_url"]
            return [yt_track]
        return []

    return tracks


async def resolve_track(spotify_track: dict, *, loop=None) -> Optional[dict]:
    """
    Lazily resolve a single Spotify stub dict to a full YouTube track dict.
    Called by the player just before it needs to play the track.
    """
    yt = await youtube.search(spotify_track["query"], loop=loop)
    if yt:
        yt["spotify_url"] = spotify_track.get("spotify_url")
    return yt


def is_spotify_url(text: str) -> bool:
    return "spotify.com" in text or text.startswith("spotify:")


# ── Helpers ───────────────────────────────────────────────────────────

def _make_spotify_track(track_obj: dict) -> dict:
    query = _track_query(track_obj)
    return {
        "title":       query,
        "url":         track_obj.get("external_urls", {}).get("spotify", ""),
        "spotify_url": track_obj.get("external_urls", {}).get("spotify", ""),
        "stream":      None,          # resolved lazily
        "duration":    track_obj.get("duration_ms", 0) // 1000,
        "thumbnail":   _get_album_art(track_obj),
        "uploader":    ", ".join(a["name"] for a in track_obj.get("artists", [])),
        "source":      "spotify",
        "query":       query,
    }


def _get_album_art(track_obj: dict) -> Optional[str]:
    try:
        images = track_obj["album"]["images"]
        return images[0]["url"] if images else None
    except (KeyError, IndexError):
        return None
