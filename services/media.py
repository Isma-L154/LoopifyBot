"""
Media extraction service — a thin async wrapper around yt-dlp.

yt-dlp supports 1000+ sites, so this single module powers every source the bot
understands: YouTube, SoundCloud, Bandcamp, direct audio links, and more. There
is no per-provider API key.

Design goals:
- Fast enqueue: searches/playlists use flat extraction (metadata only).
- Reliable playback: the audio is streamed by yt-dlp itself and piped into
  FFmpeg (``spawn_stream`` + ``make_pipe_source``). yt-dlp owns cookies,
  signature solving and throttling, which is what makes YouTube work from
  datacenter IPs — see the note above those functions.
- Non-blocking: every yt-dlp metadata call runs in a thread executor.

Track dict shape::

    {title, url, stream, duration, thumbnail, uploader, source, query}
"""

import os
import sys
import subprocess
import asyncio
import logging
from typing import Optional

import yt_dlp

log = logging.getLogger("loopify.media")

# The player_client fallback chain, as a CLI value for the streaming subprocess.
_PLAYER_CLIENTS = "default,android_vr,tv_embedded"

# Base yt-dlp config shared by every call.
#
# player_client fallback chain, tried in order:
#   * "default" (web) gives clean audio-only formats and works best WITH cookies,
#     but needs a JS runtime (Deno/Node) to solve YouTube's signature challenge —
#     deploy installs Deno for exactly this (see deploy/setup.sh).
#   * "android_vr"/"tv_embedded" need no JS runtime and are the fallback for
#     environments without one (e.g. a dev box). yt-dlp automatically advances to
#     the next client if one yields no usable formats.
# From a datacenter IP, valid cookies (COOKIES_PATH) are what defeats YouTube's
# "confirm you're not a bot" check — see deploy/README.md.
YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",   # bind to IPv4; avoids some 403s
    "skip_download": True,
    "extractor_args": {
        "youtube": {"player_client": ["default", "android_vr", "tv_embedded"]},
    },
}

# Optional cookies file (helps with age/region-gated or bot-checked videos).
_cookies_path = os.getenv("COOKIES_PATH")
if _cookies_path and os.path.exists(_cookies_path):
    YTDL_OPTIONS["cookiefile"] = _cookies_path
    log.info("Using cookies from %s", _cookies_path)

# Search-prefix aliases users can type: "!play sc: lofi" → SoundCloud search.
_SEARCH_PREFIXES = {
    "sc:": "scsearch1:",
    "soundcloud:": "scsearch1:",
    "yt:": "ytsearch1:",
    "youtube:": "ytsearch1:",
}


def _build_track(info: dict, *, query: str = "") -> dict:
    """Convert a yt-dlp info dict into our internal track dict."""
    return {
        "title":     info.get("title") or "Unknown Title",
        "url":       info.get("webpage_url") or info.get("url"),
        "stream":    None,
        "duration":  info.get("duration"),
        "thumbnail": info.get("thumbnail") or _first_thumb(info),
        "uploader":  info.get("uploader") or info.get("channel"),
        "source":    info.get("extractor_key", "media").lower(),
        "query":     query,
    }


def _first_thumb(info: dict) -> Optional[str]:
    thumbs = info.get("thumbnails") or []
    return thumbs[-1]["url"] if thumbs else None


def _run(opts: dict, target: str, *, loop):
    """Run yt-dlp's blocking extract_info in a thread executor."""
    def _extract():
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(target, download=False)
    return loop.run_in_executor(None, _extract)


def _search_target(query: str) -> tuple[str, bool]:
    """
    Turn a user query into a yt-dlp target.

    Returns ``(target, is_flat_ok)``. URLs are extracted directly; bare terms
    become a search (YouTube by default, or SoundCloud via an ``sc:`` prefix).
    """
    lowered = query.lower()
    for prefix, engine in _SEARCH_PREFIXES.items():
        if lowered.startswith(prefix):
            return engine + query[len(prefix):].strip(), True
    if query.startswith("http"):
        return query, False          # a URL — extract it directly
    return f"ytsearch1:{query}", True


# ── Public API ────────────────────────────────────────────────────────

async def search(query: str, *, loop=None) -> Optional[dict]:
    """Resolve a single track from a search term or any supported URL."""
    loop = loop or asyncio.get_event_loop()
    target, flat_ok = _search_target(query)
    opts = {**YTDL_OPTIONS, "extract_flat": flat_ok}
    try:
        info = await _run(opts, target, loop=loop)
        info = _first_entry(info)
        return _build_track(info, query=query) if info else None
    except Exception as e:
        log.warning("Search failed for %r: %s", query, e)
        return None


async def search_many(query: str, limit: int = 5, *, loop=None) -> list[dict]:
    """Return up to ``limit`` YouTube search results (metadata only)."""
    loop = loop or asyncio.get_event_loop()
    opts = {**YTDL_OPTIONS, "extract_flat": True}
    try:
        info = await _run(opts, f"ytsearch{limit}:{query}", loop=loop)
        entries = (info or {}).get("entries", [])
        return [_build_track(e, query=query) for e in entries if e]
    except Exception as e:
        log.warning("Multi-search failed for %r: %s", query, e)
        return []


async def get_playlist(url: str, *, loop=None) -> list[dict]:
    """Extract every track from a playlist/set/album URL (metadata only)."""
    loop = loop or asyncio.get_event_loop()
    opts = {**YTDL_OPTIONS, "noplaylist": False, "extract_flat": True}
    try:
        info = await _run(opts, url, loop=loop)
        entries = (info or {}).get("entries", [])
        return [_build_track(e) for e in entries if e]
    except Exception as e:
        log.warning("Playlist load failed for %r: %s", url, e)
        return []


async def related(track: dict, *, loop=None) -> Optional[dict]:
    """Approximate a 'related' track for autoplay via a themed search."""
    seed = track.get("uploader") or track.get("title") or ""
    if not seed:
        return None
    for cand in await search_many(f"{seed} mix", limit=5, loop=loop):
        if cand.get("url") and cand["url"] != track.get("url"):
            return cand
    return None


def _first_entry(info):
    """Unwrap the first playable entry from a search/playlist result."""
    if info and "entries" in info:
        entries = [e for e in info["entries"] if e]
        return entries[0] if entries else None
    return info


# ── Playback: pipe yt-dlp → FFmpeg ────────────────────────────────────
#
# Rather than hand a googlevideo URL to FFmpeg (which YouTube 403s when the URL
# is bound to an authenticated/cookie session), we let yt-dlp fetch the audio
# and stream it to stdout, then pipe those bytes into FFmpeg. yt-dlp owns all
# the hard parts — cookies, signature (nsig) solving via Deno, throttling — and
# FFmpeg just decodes a byte stream. This is the reliable path on cloud IPs.
#
# The pipe applies natural backpressure, so memory stays bounded on small hosts.

def _stream_target(track: dict) -> str:
    """The yt-dlp target for streaming a track: its URL, or a search query."""
    if track.get("url"):
        return track["url"]
    query = track.get("query") or track.get("title", "")
    target, _ = _search_target(query)
    return target


def spawn_stream(track: dict) -> subprocess.Popen:
    """
    Start a yt-dlp subprocess that writes the track's best audio to stdout.

    The returned process' ``stdout`` is meant to feed ``make_pipe_source``.
    Caller owns the process and MUST ``kill_stream`` it when playback ends.
    """
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "-f", "bestaudio/best",
        "-o", "-",                       # write audio to stdout
        "-q", "--no-warnings", "--no-playlist",
        "--extractor-args", f"youtube:player_client={_PLAYER_CLIENTS}",
        "--source-address", "0.0.0.0",
    ]
    cookies = YTDL_OPTIONS.get("cookiefile")
    if cookies:
        cmd += ["--cookies", cookies]
    cmd.append(_stream_target(track))
    # Large stdout buffer smooths delivery to FFmpeg; stderr captured for errors.
    return subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        bufsize=64 * 1024,
    )


def make_pipe_source(stdin, *, volume: float = 0.5, ffmpeg_filter: str = ""):
    """Build a ``discord.PCMVolumeTransformer`` that reads audio from a pipe."""
    import discord
    options = f"-vn -af {ffmpeg_filter}" if ffmpeg_filter else "-vn"
    source = discord.FFmpegPCMAudio(stdin, pipe=True, options=options)
    return discord.PCMVolumeTransformer(source, volume=volume)


def kill_stream(proc: Optional[subprocess.Popen]) -> None:
    """Terminate a yt-dlp streaming process and reap it (idempotent)."""
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.kill()
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        pass


def classify_stream_error(proc: Optional[subprocess.Popen]) -> str:
    """
    Read a finished yt-dlp process' stderr and classify why it produced no audio.

    Returns "blocked" (YouTube bot-check — needs fresh cookies), or "unavailable".
    """
    if proc is None:
        return "unavailable"
    try:
        err = (proc.stderr.read() or b"").decode("utf-8", "ignore").lower() if proc.stderr else ""
    except Exception:
        err = ""
    if "not a bot" in err or "sign in to confirm" in err:
        return "blocked"
    return "unavailable"
