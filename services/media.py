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
import time
import queue
import threading
import subprocess
import tempfile
import asyncio
import logging
from typing import Optional

import discord
import yt_dlp

log = logging.getLogger("loopify.media")

# YouTube player clients, tried in order. ONE definition — both the metadata
# options below and the streaming subprocess derive from this, because two
# separate lists silently drift and then playback and search disagree about
# which client to use.
#
# Ordered by measured behaviour, not by theory. Every client yt-dlp offers was
# tested against the same video from a residential IP:
#
#   web_embedded  works, ~3.2s    <- fastest that works
#   mweb          works, ~9.3s    <- reliable but slow, kept as a fallback
#   tv_embedded   bot-checked     <- kept last: needs no JS runtime, so it is
#                                    the only option on a host without Deno
#   default / web / android_vr / tv / ios / android_music  all bot-checked
#
# The previous chain was "default,android_vr,tv_embedded" — every entry of which
# is now bot-checked, so yt-dlp exhausted it and 5 of 6 tracks failed to load.
#
# A residential IP reduces YouTube's bot-checking but does NOT remove it. Valid
# cookies (COOKIES_PATH) still help and remain supported; getting the client
# chain right avoids needing them at all.
_PLAYER_CLIENTS = ("web_embedded", "mweb", "tv_embedded")

# Base yt-dlp config shared by every call.
YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",   # bind to IPv4; avoids some 403s
    "skip_download": True,
    "extractor_args": {
        "youtube": {"player_client": list(_PLAYER_CLIENTS)},
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


# How much of yt-dlp's stderr to inspect when a stream produced no audio.
# The real error is written last, and a throttled download can emit megabytes of
# progress noise ahead of it, so only the tail is worth reading.
_STDERR_TAIL_BYTES = 16 * 1024

# Phrases YouTube uses when it wants a signed-in session (i.e. fresh cookies).
_BOT_CHECK_MARKERS = ("not a bot", "sign in to confirm")

# Longest we wait for a SIGKILLed yt-dlp to actually disappear.
_REAP_TIMEOUT = 5.0


def _close_quietly(handle) -> None:
    """Close a pipe/file, ignoring anything that goes wrong during teardown."""
    if handle is None:
        return
    try:
        handle.close()
    except Exception:
        pass


class AudioStream:
    """
    A running ``yt-dlp`` process writing one track's audio to a pipe.

    ``stdout`` feeds :func:`make_pipe_source`. The caller owns the stream and
    MUST :meth:`close` it when playback ends, is skipped, or the player is
    destroyed — otherwise the child is never reaped.

    Two deliberate choices about the child's streams:

    * **stdout is a pipe.** That is what bounds memory on a small host: yt-dlp
      blocks as soon as FFmpeg stops reading, instead of buffering a whole
      track in RAM.
    * **stderr is a temporary file, never a pipe.** Nothing reads stderr while
      the track plays, so a pipe whose ~64 KB kernel buffer filled up would
      block yt-dlp mid-write, starve FFmpeg and stall playback silently.
      Writing to a file never blocks.

    :meth:`close` and :meth:`classify_error` may both block briefly (waiting on
    the child, reading the file), so call them from an executor, never straight
    from the event loop.
    """

    __slots__ = ("_proc", "_errfile", "_error", "_closed")

    def __init__(self, proc: subprocess.Popen, errfile) -> None:
        self._proc = proc
        self._errfile = errfile
        self._error: Optional[str] = None
        self._closed = False

    @classmethod
    def launch(cls, cmd: list[str]) -> "AudioStream":
        """Spawn ``cmd`` with the stdout/stderr wiring described above."""
        errfile = tempfile.TemporaryFile()
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=errfile,
                bufsize=64 * 1024,      # large buffer smooths delivery to FFmpeg
            )
        except Exception:
            _close_quietly(errfile)
            raise
        return cls(proc, errfile)

    @property
    def stdout(self):
        """The audio pipe, or ``None`` once the stream has been closed."""
        return None if self._closed else self._proc.stdout

    def close(self) -> None:
        """Kill the process, reap it and release both handles. Idempotent."""
        if self._closed:
            return
        self._closed = True
        proc = self._proc
        try:
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass                # already gone between poll and kill
            try:
                proc.wait(timeout=_REAP_TIMEOUT)
            except subprocess.TimeoutExpired:
                log.warning("yt-dlp (pid %s) survived SIGKILL; not reaped", proc.pid)
            # Read the error while the file is still open — the player asks for
            # the reason only after the stream has been torn down.
            self._error = self._read_error()
        finally:
            _close_quietly(proc.stdout)
            _close_quietly(self._errfile)

    def classify_error(self) -> str:
        """
        Why this stream produced no audio.

        ``"blocked"`` means YouTube demanded a signed-in session and the cookies
        need refreshing; ``"unavailable"`` covers everything else. Safe to call
        before or after :meth:`close`, and repeatable.
        """
        err = self._error if self._error is not None else self._read_error()
        return "blocked" if any(m in err for m in _BOT_CHECK_MARKERS) else "unavailable"

    def _read_error(self) -> str:
        """The tail of the child's stderr, lowercased. Never raises."""
        try:
            self._errfile.seek(0, os.SEEK_END)
            start = max(0, self._errfile.tell() - _STDERR_TAIL_BYTES)
            self._errfile.seek(start)
            return self._errfile.read().decode("utf-8", "ignore").lower()
        except Exception:
            return ""


def spawn_stream(track: dict) -> AudioStream:
    """Start streaming a track's best audio through yt-dlp."""
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "-f", "bestaudio/best",
        "-o", "-",                       # write audio to stdout
        "-q", "--no-warnings", "--no-playlist",
        "--extractor-args", f"youtube:player_client={','.join(_PLAYER_CLIENTS)}",
        "--source-address", "0.0.0.0",
    ]
    cookies = YTDL_OPTIONS.get("cookiefile")
    if cookies:
        cmd += ["--cookies", cookies]
    cmd.append(_stream_target(track))
    return AudioStream.launch(cmd)


# Discord consumes 20 ms of 48 kHz stereo 16-bit PCM per frame.
FRAME_SIZE = 3840
FRAME_SECONDS = 0.02
# How much audio to keep ready. 5 s is ~960 KB — nothing against 15 GB of RAM,
# and long enough to cover the gaps yt-dlp leaves when YouTube throttles a
# download after its initial burst.
READ_AHEAD_SECONDS = 5.0
# Longest to wait for the first audio before starting playback. yt-dlp needs
# seconds to resolve and connect; beyond this something is genuinely wrong.
PRIME_TIMEOUT_SECONDS = 30.0


class BufferedAudioSource(discord.AudioSource):
    """
    Keeps a few seconds of audio ready so a stalled source never delays a frame.

    ``discord.py``'s player paces itself against a wall clock::

        next_time = self._start + DELAY * self.loops
        delay = max(0, DELAY + (next_time - time.perf_counter()))
        time.sleep(delay)

    If ``source.read()`` blocks — FFmpeg waiting on bytes from yt-dlp — the
    player falls behind that schedule. ``delay`` then clamps to zero and it
    sends frames as fast as it can until it catches up, which is audible as the
    track briefly speeding up before settling. Reading ahead in a background
    thread means a stall shorter than the buffer never reaches the player.

    The queue is **bounded**: an unbounded one in front of a fast source would
    pull a whole track into RAM and undo the backpressure that the yt-dlp pipe
    exists to provide.
    """

    def __init__(self, source, *, seconds: float = READ_AHEAD_SECONDS) -> None:
        self._source = source
        self.capacity_frames = max(1, int(seconds / FRAME_SECONDS))
        self._queue: "queue.Queue" = queue.Queue(maxsize=self.capacity_frames)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._fill, name="loopify-readahead", daemon=True,
        )
        self._thread.start()

    @property
    def buffered_frames(self) -> int:
        return self._queue.qsize()

    def is_opus(self) -> bool:
        return self._source.is_opus()

    def _fill(self) -> None:
        """Pull from the wrapped source until it ends, fails, or we stop."""
        try:
            while not self._stop.is_set():
                data = self._source.read()
                if not data:
                    break
                # Block when full — that is the backpressure. Time out so the
                # thread still notices _stop while the consumer is gone.
                while not self._stop.is_set():
                    try:
                        self._queue.put(data, timeout=0.1)
                        break
                    except queue.Full:
                        continue
        except Exception as e:
            log.warning("Read-ahead stopped: %s", e)
        finally:
            # A sentinel unblocks a consumer waiting on an exhausted source.
            try:
                self._queue.put_nowait(b"")
            except queue.Full:
                pass

    def read(self) -> bytes:
        """
        The next frame, or ``b""`` when the source is finished.

        An empty buffer is NOT the end. yt-dlp needs seconds to produce its
        first byte, so returning ``b""`` while the producer is still working
        would tell discord.py the track had ended and stop it immediately. Only
        the sentinel the producer puts in on exit, or a producer that has died,
        means finished.
        """
        while not self._stop.is_set():
            try:
                return self._queue.get(timeout=0.5)
            except queue.Empty:
                if not self._thread.is_alive():
                    return b""      # producer gone without leaving a sentinel
                continue
        return b""

    def prime(self, *, timeout: float = PRIME_TIMEOUT_SECONDS) -> bool:
        """
        Block until there is audio ready, or the source ends. Never on the loop.

        discord.py's player starts its clock *before* its first read, so
        beginning playback against an empty buffer leaves it seconds behind
        schedule — and it catches up by bursting frames, which is the exact
        artefact this class exists to prevent. Filling first means the clock
        starts when audio is genuinely ready.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._stop.is_set() or self.buffered_frames or not self._thread.is_alive():
                return self.buffered_frames > 0
            time.sleep(0.05)
        log.warning("Timed out waiting %.0fs for audio to buffer", timeout)
        return False

    def cleanup(self) -> None:
        """Stop the reader thread and tear down the wrapped source."""
        if self._stop.is_set():
            return
        self._stop.set()
        # Drain so a producer blocked on a full queue can see _stop and exit.
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self._source.cleanup()
        except Exception:
            pass


def make_pipe_source(stdin, *, volume: float = 0.5, ffmpeg_filter: str = "",
                     seek_seconds: float = 0.0):
    """
    Build a ``discord.PCMVolumeTransformer`` that reads audio from a pipe.

    ``seek_seconds`` starts playback partway in, which is what lets an effect
    change resume where the listener was. It is passed as an *input* option so
    FFmpeg discards packets without decoding them; on a pipe that is a
    read-and-discard rather than a real seek, but it costs almost nothing
    because yt-dlp delivers at network speed rather than in realtime.
    """
    options = f"-vn -af {ffmpeg_filter}" if ffmpeg_filter else "-vn"
    before = f"-ss {seek_seconds:.3f}" if seek_seconds > 0 else None
    source = discord.FFmpegPCMAudio(
        stdin, pipe=True, before_options=before, options=options,
    )
    # Order matters. The read-ahead goes around FFmpeg, which is what stalls,
    # and the volume transformer stays outermost so `MusicPlayer.set_volume`
    # still finds a PCMVolumeTransformer on `voice_client.source`. Volume is a
    # cheap multiply, so applying it on the consumer side costs nothing.
    return discord.PCMVolumeTransformer(
        BufferedAudioSource(source), volume=volume,
    )


def prime_source(source) -> bool:
    """
    Wait for a source from :func:`make_pipe_source` to have audio ready.

    Blocks, so call it from an executor. Returns whether anything buffered —
    ``False`` means the track produced no audio and the caller should treat it
    as a failed load rather than start playing silence.
    """
    inner = getattr(source, "original", None)
    return inner.prime() if isinstance(inner, BufferedAudioSource) else True
