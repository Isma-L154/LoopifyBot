"""
Time-synced lyrics, from LRCLIB.

Genius has no timestamps, so it cannot say *when* a line is sung. LRCLIB does,
needs no API key, and returns the LRC format: one line per lyric, each prefixed
with the time it starts.

    [00:07.13] Caught in a landslide, no escape from reality
"""

import logging
import re
from bisect import bisect_right
from dataclasses import dataclass
from typing import Any, Optional

import aiohttp

log = logging.getLogger("loopify.synced")

API_URL = "https://lrclib.net/api/get"
# LRCLIB asks clients to identify themselves.
USER_AGENT = "LoopifyBot (https://github.com/Isma-L154/LoopifyBot)"
# A lyric is a nicety; it must never hold up the music for long.
REQUEST_TIMEOUT = 6.0   # seconds; passed as an aiohttp.ClientTimeout below

# [mm:ss], [mm:ss.xx] or [mm:ss.xxx]. Minutes are not capped at 60 — a long
# track just keeps counting. Metadata tags like [ar: Queen] never match, which
# is how they get ignored.
_STAMP = re.compile(r"\[(\d+):(\d{2})(?:[.:](\d{1,3}))?\]")

Line = tuple[float, str]


def parse_lrc(body: str) -> tuple[Line, ...]:
    """
    Read an LRC body into ``(seconds, text)`` pairs, in time order.

    A line may carry several timestamps — that is how a repeated chorus is
    written — and each becomes its own entry. A timestamp with no words is kept
    rather than dropped: LRCLIB marks instrumental gaps that way, and losing
    them would leave the previous line on screen through the whole break.
    """
    lines: list[Line] = []
    for raw in body.splitlines():
        stamps = list(_STAMP.finditer(raw))
        if not stamps:
            continue
        text = raw[stamps[-1].end():].strip()
        for stamp in stamps:
            minutes, seconds, fraction = stamp.groups()
            at = int(minutes) * 60 + int(seconds)
            if fraction:
                at += int(fraction) / 10 ** len(fraction)
            lines.append((at, text))
    return tuple(sorted(lines, key=lambda line: line[0]))


# Words that mark a bracketed chunk as video furniture rather than part of the
# song's name. "(Acoustic Version)" is the song; "(Official Video)" is not.
_FURNITURE = re.compile(
    r"[(\[][^)\]]*\b(?:official|video|audio|lyrics?|visuali[sz]er"
    r"|remaster(?:ed)?|hd|hq|4k|8k|mv|explicit|clean|live|oficial|letra"
    r"|legendado|sub(?:titulado)?|full album|color coded)\b[^)\]]*[)\]]",
    re.IGNORECASE)
# "Song ft. Someone" — databases file a track under its lead artist alone.
_CREDITS = re.compile(r"\s*\b(?:ft|feat)\.?\s.*$", re.IGNORECASE)
# A trailing "| Album Name" or "| Official Video".
_TRAILING_PIPE = re.compile(r"\s*\|.*$")
# What separates an artist from a title. A run of two or more spaces counts:
# plenty of uploads write "The Police   Every Breath You Take" with no dash.
_SEPARATOR = re.compile(r"\s[-–—]\s|\s{2,}")
# Suffixes YouTube itself adds to a channel name.
_CHANNEL_NOISE = re.compile(r"\s*-\s*Topic$|\s*VEVO$|\s*Official$", re.IGNORECASE)


def _tidy(text: str) -> str:
    return " ".join(text.split())


def search_terms(title: str, uploader: str = "") -> tuple[str, str]:
    """
    Turn a video title into the ``(track, artist)`` a lyrics database expects.

    Raw YouTube titles are not song names: they carry video furniture, featured
    credits and album tags, and the channel is often an aggregator rather than
    the artist. Searching with them as-is is why some songs came back empty
    while others worked — measured against LRCLIB, the raw form 404s where the
    tidied one returns synced lyrics.

    The title is trusted over the channel when it names both, because a channel
    called "Music n Lyrics" is not who recorded the song.
    """
    # Whitespace is collapsed only at the end: a run of two or more spaces is
    # itself a separator, and tidying first would erase it.
    cleaned = _CREDITS.sub("", _TRAILING_PIPE.sub("", _FURNITURE.sub("", title))).strip()
    if not cleaned:
        cleaned = title.strip()     # all furniture: better to search it than nothing

    artist = _CHANNEL_NOISE.sub("", uploader)
    halves = _SEPARATOR.split(cleaned, maxsplit=1)
    if len(halves) == 2 and halves[0].strip() and halves[1].strip():
        artist, cleaned = halves

    return _tidy(cleaned).strip(" -–—"), _tidy(artist.split(",")[0])


def index_at(lines: tuple[Line, ...], position: float) -> int:
    """
    Index of the line playing at ``position``, or -1 before the first one.

    Exactly on a timestamp counts as that line having started.
    """
    return bisect_right(lines, position, key=lambda line: line[0]) - 1


@dataclass(frozen=True)
class Lyrics:
    """What LRCLIB knows about one track."""

    title: str
    artist: str
    lines: tuple[Line, ...] = ()   # empty unless the body carried timestamps
    plain: str = ""
    instrumental: bool = False

    @property
    def synced(self) -> bool:
        return bool(self.lines)


async def fetch(session: aiohttp.ClientSession, title: str, artist: str,
                duration: Optional[float]) -> Optional[Lyrics]:
    """
    Look a track up on LRCLIB. Returns None when there is nothing to show.

    ``session`` is an ``aiohttp.ClientSession`` owned by the caller. Nothing
    here raises: LRCLIB being unreachable, slow or wrong is not a reason to
    interrupt playback, so every failure becomes None and a log line.
    """
    params: dict[str, str | int] = {"track_name": title, "artist_name": artist}
    if duration:
        params["duration"] = int(duration)

    try:
        async with session.get(
            API_URL, params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
        ) as response:
            if response.status != 200:
                log.debug("LRCLIB returned %s for %r", response.status, title)
                return None
            payload = await response.json(content_type=None)
    except Exception as e:
        log.warning("LRCLIB lookup failed for %r: %s", title, e)
        return None

    return _build(payload, title, artist)


def _build(payload: dict[str, Any], title: str, artist: str) -> Optional[Lyrics]:
    """Turn a payload into Lyrics, or None when it holds nothing worth showing."""
    lines = parse_lrc(payload.get("syncedLyrics") or "")
    plain = (payload.get("plainLyrics") or "").strip()
    if not plain and lines:
        # A payload can carry timings and no plain copy. `!lyrics <search>` has
        # no clock to follow, so it needs the words on their own.
        plain = "\n".join(text for _, text in lines)
    found = Lyrics(
        title=payload.get("trackName") or title,
        artist=payload.get("artistName") or artist,
        lines=lines,
        plain=plain,
        instrumental=bool(payload.get("instrumental")),
    )
    if found.synced or found.plain or found.instrumental:
        return found
    return None
