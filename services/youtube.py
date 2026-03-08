import asyncio
import yt_dlp
import os
from typing import Optional


# FFmpeg options — reconnect keeps stream alive if it drops
FFMPEG_OPTIONS = {
    "before_options": (
        "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    ),
    "options": "-vn",
}

# yt-dlp config — best audio, no playlist by default, quiet, search support, and some fixes for common issues
YTDL_OPTIONS = {
    "format": "bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "remote_components": ["ejs:npm"],
    "js_runtimes": {"bun": {}},
}

cookies_path = os.getenv("COOKIES_PATH")
if cookies_path and os.path.exists(cookies_path):
    YTDL_OPTIONS["cookiefile"] = cookies_path


def _build_track(info: dict, original_query: str = "") -> dict:
    """Convert yt-dlp info dict → our internal track dict."""
    return {
        "title":     info.get("title", "Unknown Title"),
        "url":       info.get("webpage_url") or info.get("url"),
        "stream":    info.get("url"),
        "duration":  info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "uploader":  info.get("uploader") or info.get("channel"),
        "source":    "youtube",
        "query":     original_query,
    }


async def search(query: str, *, loop=None) -> Optional[dict]:
    """
    Search YouTube for a single track.
    Accepts a URL or a plain-text search query.
    Returns a track dict or None on failure.
    """
    loop = loop or asyncio.get_event_loop()

    opts = dict(YTDL_OPTIONS)
    if not query.startswith("http"):
        query = f"ytsearch:{query}"
        opts["noplaylist"] = True

    def _extract():
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=False)
            # ytsearch returns a list; grab first result
            if "entries" in info:
                info = info["entries"][0]
            return info

    try:
        info = await loop.run_in_executor(None, _extract)
        return _build_track(info, original_query=query)
    except Exception as e:
        print(f"[YouTube] Search error: {e}")
        return None


async def search_many(query: str, limit: int = 5, *, loop=None) -> list[dict]:
    """Return up to `limit` search results for a query (no URL)."""
    loop = loop or asyncio.get_event_loop()
    opts = {**YTDL_OPTIONS, "noplaylist": True}

    def _extract():
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
            return info.get("entries", [])

    try:
        entries = await loop.run_in_executor(None, _extract)
        return [_build_track(e, original_query=query) for e in entries if e]
    except Exception as e:
        print(f"[YouTube] Multi-search error: {e}")
        return []


async def get_playlist(url: str, *, loop=None) -> list[dict]:
    """Extract all tracks from a YouTube playlist URL."""
    loop = loop or asyncio.get_event_loop()
    opts = {**YTDL_OPTIONS, "noplaylist": False, "extract_flat": True}

    def _extract():
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get("entries", [])

    try:
        entries = await loop.run_in_executor(None, _extract)
        return [_build_track(e) for e in entries if e]
    except Exception as e:
        print(f"[YouTube] Playlist error: {e}")
        return []


def make_source(stream_url: str, volume: float = 0.5, ffmpeg_filter: str = ""):
    """
    Build a discord.py PCMVolumeTransformer audio source.
    Optionally applies an FFmpeg audio filter string.
    """
    import discord

    options = dict(FFMPEG_OPTIONS)
    if ffmpeg_filter:
        options["options"] = f"-vn -af {ffmpeg_filter}"

    source = discord.FFmpegPCMAudio(stream_url, **options)
    return discord.PCMVolumeTransformer(source, volume=volume)
