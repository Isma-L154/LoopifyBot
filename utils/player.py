"""
Per-guild music player.

Each active guild gets one :class:`MusicPlayer` running a single background
task (``_player_loop``). That loop is the *only* place that ever calls
``voice_client.play`` — every command (skip, effect, previous, …) simply
mutates state and signals the loop. This single-owner design removes the race
conditions that plague the naive "chained ``after`` callback" approach, where
``stop()`` could fire a callback that advanced the queue at the same time a new
source was being played.

Track dict shape (see ``services.media._build_track``)::

    {title, url, stream, duration, thumbnail, uploader, source, query}
"""

import time
import random
import asyncio
import logging
from collections import deque
from typing import Optional

import discord

from services import media
from utils.embeds import now_playing_embed, error_embed, info_embed

log = logging.getLogger("loopify.player")

INACTIVITY_TIMEOUT = 300   # seconds with an empty queue before disconnecting
HISTORY_LIMIT = 50
MAX_QUEUE = 500            # hard cap to protect memory on small instances
SEEK_TAIL_MARGIN = 2.0     # never resume into the last seconds of a track
LOAD_FAILURE_SECONDS = 2.0 # a track ending faster than this never really started


class MusicPlayer:
    """Owns the queue, playback loop and voice state for a single guild."""

    def __init__(self, bot: discord.Client, guild: discord.Guild,
                 text_channel: discord.abc.Messageable):
        self.bot = bot
        self.guild = guild
        self.text_channel = text_channel

        self.queue: deque[dict] = deque()
        self.history: list[dict] = []
        self.current: Optional[dict] = None

        self.loop_mode: str = "off"      # off | track | queue
        self.autoplay: bool = False
        self.volume: float = 0.5
        self.effect_name: Optional[str] = None
        self.effect_filter: str = ""

        self._start_ts: float = 0.0      # monotonic clock when current started
        self._paused_at: Optional[float] = None   # when the current pause began
        self._paused_total: float = 0.0           # paused seconds, this track
        self._resume_at: float = 0.0              # seek offset for the next spawn
        self._stream: Optional[media.AudioStream] = None   # active yt-dlp stream

        # Signalling between commands and the playback loop.
        self._next = asyncio.Event()     # set when the current source finishes
        self._added = asyncio.Event()    # set when a track is enqueued
        self._skip = False               # bypass loop mode for one advance
        self._replay = False             # replay current track (effect change)
        self._destroyed = False

        self._task = bot.loop.create_task(self._player_loop())

    # ── Queue mutation (called by commands) ───────────────────────────

    def add(self, track: dict) -> bool:
        """Append a track. Returns False if the queue is at its hard cap."""
        if len(self.queue) >= MAX_QUEUE:
            return False
        self.queue.append(track)
        self._added.set()
        return True

    def add_many(self, tracks: list[dict]) -> int:
        """Append up to the queue cap. Returns how many were actually added."""
        room = MAX_QUEUE - len(self.queue)
        accepted = tracks[:max(0, room)]
        self.queue.extend(accepted)
        if accepted:
            self._added.set()
        return len(accepted)

    def remove(self, index: int) -> Optional[dict]:
        """Remove a 1-based queue position. Returns the removed track or None."""
        if not (1 <= index <= len(self.queue)):
            return None
        lst = list(self.queue)
        removed = lst.pop(index - 1)
        self.queue = deque(lst)
        return removed

    def move(self, frm: int, to: int) -> bool:
        lst = list(self.queue)
        if not (1 <= frm <= len(lst)) or not (1 <= to <= len(lst)):
            return False
        lst.insert(to - 1, lst.pop(frm - 1))
        self.queue = deque(lst)
        return True

    def shuffle(self) -> None:
        lst = list(self.queue)
        random.shuffle(lst)
        self.queue = deque(lst)

    def clear(self) -> None:
        self.queue.clear()

    @property
    def is_empty(self) -> bool:
        return len(self.queue) == 0

    def to_list(self) -> list[dict]:
        return list(self.queue)

    @property
    def voice(self) -> Optional[discord.VoiceClient]:
        return self.guild.voice_client

    # ── Command-facing controls ───────────────────────────────────────

    def skip(self) -> bool:
        vc = self.voice
        if vc and (vc.is_playing() or vc.is_paused()):
            self._skip = True
            vc.stop()          # fires the source's `after` → wakes the loop
            return True
        return False

    @property
    def elapsed(self) -> float:
        """Seconds of the current track actually heard, excluding paused time."""
        if not self._start_ts:
            return 0.0
        paused = self._paused_total
        if self._paused_at is not None:
            paused += time.monotonic() - self._paused_at
        return max(0.0, time.monotonic() - self._start_ts - paused)

    def pause(self) -> bool:
        """Pause playback and stop the clock, so ``elapsed`` stays honest."""
        vc = self.voice
        if not (vc and vc.is_playing()):
            return False
        vc.pause()
        if self._paused_at is None:
            self._paused_at = time.monotonic()
        return True

    def resume(self) -> bool:
        vc = self.voice
        if not (vc and vc.is_paused()):
            return False
        vc.resume()
        if self._paused_at is not None:
            self._paused_total += time.monotonic() - self._paused_at
            self._paused_at = None
        return True

    def apply_effect(self, name: Optional[str], filter_str: str) -> bool:
        """
        Switch the current track to a new FFmpeg filter, resuming in place.

        A filter chain is fixed for the life of an FFmpeg process, so changing
        one means respawning the stream. ``_resume_at`` carries the current
        position across that respawn — without it, asking for a bass boost four
        minutes into a song threw the listener back to 0:00.
        """
        vc = self.voice
        if not (vc and self.current and (vc.is_playing() or vc.is_paused())):
            return False
        self.effect_name = name
        self.effect_filter = filter_str
        self._resume_at = self._seek_target()
        self._replay = True
        vc.stop()
        return True

    def _seek_target(self) -> float:
        """
        Where a respawn should pick up, or 0 when seeking would be wrong.

        Live streams report no duration and cannot be seeked, and a position in
        the last couple of seconds would resume into silence or past the end.
        """
        duration = (self.current or {}).get("duration")
        if not duration:
            return 0.0
        position = self.elapsed
        return position if 0 < position < duration - SEEK_TAIL_MARGIN else 0.0

    def go_previous(self) -> bool:
        """Queue the previous track to play next, keeping the current one after it."""
        if not self.history:
            return False
        prev = self.history.pop()
        if self.current:
            self.queue.appendleft(self.current)
        self.queue.appendleft(prev)
        self.current = None      # loop won't re-archive it into history
        self._skip = True
        vc = self.voice
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
        else:
            self._added.set()
        return True

    def set_volume(self, vol: float) -> None:
        self.volume = vol
        vc = self.voice
        if vc and vc.source and isinstance(vc.source, discord.PCMVolumeTransformer):
            vc.source.volume = vol

    # ── The single playback loop ──────────────────────────────────────

    async def _player_loop(self) -> None:
        await self.bot.wait_until_ready()
        try:
            while not self._destroyed:
                self._next.clear()

                track, silent = await self._advance()
                if track is None:
                    return await self._idle_disconnect()

                vc = self.voice
                if not vc or not vc.is_connected():
                    return self.destroy()

                # Stream the audio through yt-dlp → FFmpeg (see services.media).
                stream = await self.bot.loop.run_in_executor(
                    None, media.spawn_stream, track)
                self._stream = stream
                was_replay = self._replay
                self._replay = False
                seek_to = self._resume_at
                self._resume_at = 0.0
                source = media.make_pipe_source(
                    stream.stdout, volume=self.volume,
                    ffmpeg_filter=self.effect_filter, seek_seconds=seek_to,
                )
                vc.play(source, after=self._after_play)
                # Backdate the clock by the seek so a *second* effect change
                # resumes from the real position, not from the respawn point.
                self._start_ts = time.monotonic() - seek_to
                self._paused_total = 0.0
                self._paused_at = None
                spawned_at = time.monotonic()

                if not silent:
                    await self._safe_send(now_playing_embed(
                        track, track.get("requester") or self.guild.me,
                        loop_mode=self.loop_mode,
                    ))

                await self._next.wait()
                # Measured from the spawn, not from _start_ts, which is
                # backdated when resuming partway into a track.
                played = time.monotonic() - spawned_at
                source.cleanup()               # stop FFmpeg
                # close() waits on the child and reads its stderr, so keep it
                # off the event loop.
                await self.bot.loop.run_in_executor(None, stream.close)
                self._stream = None

                # A near-instant end that wasn't a user action means the source
                # failed to load (bot-check, unavailable). Tell the user.
                if (not self._destroyed and not self._skip and not was_replay
                        and played < LOAD_FAILURE_SECONDS):
                    track["error"] = stream.classify_error()   # cached by close()
                    await self._safe_send(self._load_error_embed(track))
                    self.current = None
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Player loop crashed for guild %s", self.guild.id)
            self.destroy()

    def _after_play(self, error: Optional[Exception]) -> None:
        """Runs in the voice thread — hand control back to the loop safely."""
        if error:
            log.warning("Playback error in guild %s: %s", self.guild.id, error)
        self.bot.loop.call_soon_threadsafe(self._next.set)

    async def _advance(self) -> tuple[Optional[dict], bool]:
        """
        Decide the next track to play.

        Returns ``(track, silent)`` where ``silent`` suppresses the
        "Now Playing" message (used for effect replays and track-loop repeats).
        Returns ``(None, _)`` to signal the loop should disconnect.
        """
        # Effect/volume replay: same track, same position, no announcement.
        if self._replay:
            return self.current, True

        prev = self.current
        if prev and not self._skip:
            self.history.append(prev)
            if len(self.history) > HISTORY_LIMIT:
                self.history.pop(0)
            if self.loop_mode == "track":
                return prev, True                      # silent repeat
            if self.loop_mode == "queue":
                self.queue.append(prev)
        self._skip = False

        if self.queue:
            self.current = self.queue.popleft()
            return self.current, False

        # Queue empty → try autoplay.
        if self.autoplay and prev:
            nxt = await media.related(prev, loop=self.bot.loop)
            if nxt:
                self.current = nxt
                return self.current, False

        # Nothing to play: wait for a track, or time out and disconnect.
        self.current = None
        self._added.clear()
        try:
            await asyncio.wait_for(self._added.wait(), timeout=INACTIVITY_TIMEOUT)
        except asyncio.TimeoutError:
            return None, False
        return await self._advance()

    @staticmethod
    def _load_error_embed(track: dict) -> discord.Embed:
        title = track.get("title", "track")
        if track.get("error") == "blocked":
            return error_embed(
                f"YouTube is rate-limiting this server, so **{title}** can't be "
                f"loaded right now. Try SoundCloud instead — e.g. `!play sc: {title}`."
            )
        return error_embed(f"Couldn't load **{title}** — skipping.")

    async def _idle_disconnect(self) -> None:
        await self._safe_send(info_embed(
            "👋 Left the channel",
            "Disconnected after 5 minutes of inactivity.",
        ))
        self.destroy()

    # ── Teardown ──────────────────────────────────────────────────────

    def destroy(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        self.queue.clear()
        self.current = None
        if self._stream is not None:
            # Reaping blocks briefly; hand it to a thread so teardown from a
            # command never stalls the event loop.
            self.bot.loop.run_in_executor(None, self._stream.close)
            self._stream = None
        vc = self.voice
        if vc and vc.is_connected():
            asyncio.ensure_future(vc.disconnect(force=True))
        if self._task and not self._task.done():
            self._task.cancel()
        players.discard(self.guild.id)

    async def _safe_send(self, embed: discord.Embed) -> None:
        try:
            await self.text_channel.send(embed=embed)
        except (discord.HTTPException, discord.Forbidden) as e:
            log.debug("Could not send message to guild %s: %s", self.guild.id, e)


class PlayerManager:
    """Holds one :class:`MusicPlayer` per active guild."""

    def __init__(self) -> None:
        self._players: dict[int, MusicPlayer] = {}

    def get(self, guild_id: int) -> Optional[MusicPlayer]:
        return self._players.get(guild_id)

    def get_or_create(self, bot: discord.Client, ctx) -> MusicPlayer:
        player = self._players.get(ctx.guild.id)
        if player is None or player._destroyed:
            player = MusicPlayer(bot, ctx.guild, ctx.channel)
            self._players[ctx.guild.id] = player
        else:
            player.text_channel = ctx.channel   # follow the latest command channel
        return player

    def discard(self, guild_id: int) -> None:
        self._players.pop(guild_id, None)


# Singleton used across the whole bot.
players = PlayerManager()
