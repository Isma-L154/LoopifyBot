"""
Per-guild music player.

Each active guild gets one :class:`MusicPlayer` running a single background
task (``_player_loop``). That loop is the *only* place that ever calls
``voice_client.play`` — every command (skip, effect, previous, …) simply
mutates state and signals the loop. This single-owner design removes the race
conditions that plague the naive "chained ``after`` callback" approach, where
``stop()`` could fire a callback that advanced the queue at the same time a new
source was being played.

Tracks are :class:`services.media.Track`.
"""

import time
import random
import asyncio
import logging
from collections import deque
from typing import Callable, Optional, cast

import discord

from services import media
from services.media import Track
from utils.announcer import ChannelAnnouncer

log = logging.getLogger("loopify.player")

INACTIVITY_TIMEOUT = 300   # seconds with an empty queue before disconnecting
HISTORY_LIMIT = 50
MAX_QUEUE = 500            # hard cap to protect memory on small instances
SEEK_TAIL_MARGIN = 2.0     # never resume into the last seconds of a track
# How long before a track ends to start fetching the next one. A prefetched
# yt-dlp sits blocked on a full pipe until we consume it, and YouTube drops
# connections that idle too long — so this is a compromise between hiding the
# 3–8s startup and not holding a connection open for a whole track.
PREFETCH_LEAD_SECONDS = 30.0
LOAD_FAILURE_SECONDS = 2.0 # a track ending faster than this never really started


class MusicPlayer:
    """Owns the queue, playback loop and voice state for a single guild."""

    def __init__(self, bot: discord.Client, guild: discord.Guild,
                 text_channel: discord.abc.Messageable, *,
                 on_destroy: Optional[Callable[[int], None]] = None) -> None:
        self.bot = bot
        self.guild = guild
        self.announcer = ChannelAnnouncer(text_channel)
        # Who to tell when this player is finished. Injected rather than reached
        # for, so the player never has to know about the registry holding it —
        # and a test can build one without touching process-wide state.
        self._on_destroy = on_destroy

        self.queue: deque[Track] = deque()
        self.history: list[Track] = []
        self.current: Optional[Track] = None

        self.loop_mode: str = "off"      # off | track | queue
        self.autoplay: bool = False
        self.volume: float = 0.5
        self.effect_name: Optional[str] = None
        self.effect_filter: str = ""
        # How fast the active effect consumes audio: nightcore 1.25, vaporwave
        # 0.8, everything else 1.0. `position` needs it; `elapsed` does not.
        self.effect_rate: float = 1.0

        self._start_ts: float = 0.0      # monotonic clock when current started
        self._paused_at: Optional[float] = None   # when the current pause began
        self._paused_total: float = 0.0           # paused seconds, this track
        self._resume_at: float = 0.0              # seek offset for the next spawn
        self._seek_base: float = 0.0              # offset the live stream started at
        self._stream: Optional[media.AudioStream] = None   # active yt-dlp stream
        # Next track's stream, fetched while the current one plays.
        self._prefetch: Optional[tuple[Track, media.AudioStream]] = None
        self._prefetch_task: Optional[asyncio.Task[None]] = None

        # Signalling between commands and the playback loop.
        self._next = asyncio.Event()     # set when the current source finishes
        self._added = asyncio.Event()    # set when a track is enqueued
        self._skip = False               # bypass loop mode for one advance
        self._replay = False             # replay current track (effect change)
        self._destroyed = False

        self._task = bot.loop.create_task(self._player_loop())

    # ── Queue mutation (called by commands) ───────────────────────────

    def add(self, track: Track) -> bool:
        """Append a track. Returns False if the queue is at its hard cap."""
        if len(self.queue) >= MAX_QUEUE:
            return False
        self.queue.append(track)
        self._added.set()
        return True

    def add_many(self, tracks: list[Track]) -> int:
        """Append up to the queue cap. Returns how many were actually added."""
        room = MAX_QUEUE - len(self.queue)
        accepted = tracks[:max(0, room)]
        self.queue.extend(accepted)
        if accepted:
            self._added.set()
        return len(accepted)

    def remove(self, index: int) -> Optional[Track]:
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
        return not self.queue

    def to_list(self) -> list[Track]:
        return list(self.queue)

    @property
    def voice(self) -> Optional[discord.VoiceClient]:
        # Typed as the VoiceProtocol base; this bot only ever connects with
        # discord.py's own VoiceClient.
        return cast(Optional[discord.VoiceClient], self.guild.voice_client)

    @property
    def text_channel(self) -> discord.abc.Messageable:
        return self.announcer.channel

    @text_channel.setter
    def text_channel(self, channel: discord.abc.Messageable) -> None:
        self.announcer.channel = channel

    @property
    def is_destroyed(self) -> bool:
        return self._destroyed

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

    @property
    def position(self) -> float:
        """
        Where the audio actually is, which is not always where the clock is.

        The pitch effects change playback speed, so at 1.25x the song is a
        quarter further along than wall time. Only the stretch since the current
        stream was spawned is scaled — whatever came before it was heard at
        whatever speed was in force then, and `_seek_base` is where it resumed.
        """
        if self.effect_rate == 1.0:
            return self.elapsed
        return self._seek_base + (self.elapsed - self._seek_base) * self.effect_rate

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

    def apply_effect(self, name: Optional[str], filter_str: str,
                     rate: float = 1.0) -> bool:
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
        self.effect_rate = rate
        self._resume_at = self._seek_target()
        self._replay = True
        vc.stop()
        return True

    # ── Prefetch ──────────────────────────────────────────────────────

    def _prefetch_delay(self, track: Track) -> Optional[float]:
        """
        Seconds to wait before prefetching, or ``None`` to start immediately.

        Starting at the top of a long track would leave a yt-dlp process
        blocked on a full pipe for minutes, and YouTube drops connections that
        idle that long — the stream would then be dead by the time we wanted
        it. Live streams have no end to count back from, so they prefetch now.
        """
        duration = (track or {}).get("duration")
        if not duration:
            return None
        remaining = duration - self.elapsed - PREFETCH_LEAD_SECONDS
        return remaining if remaining > 0 else None

    def _start_prefetch(self) -> None:
        """Begin fetching whatever is at the front of the queue."""
        if self._destroyed or self._prefetch is not None:
            return
        if self._prefetch_task is not None and not self._prefetch_task.done():
            return
        if self.loop_mode == "track":
            return                  # the next track is the current one
        if not self.queue:
            return
        upcoming = self.queue[0]
        self._prefetch_task = self.bot.loop.create_task(self._prefetch_next(upcoming))

    async def _prefetch_next(self, upcoming: Track) -> None:
        try:
            stream = await self.bot.loop.run_in_executor(
                None, media.spawn_stream, upcoming)
        except Exception:
            log.exception("Prefetch failed for guild %s", self.guild.id)
            return
        # The queue can change while this runs. Hand the stream over only if it
        # is still wanted; otherwise close it rather than leaking the process.
        if self._destroyed or self._prefetch is not None:
            return self._discard(stream)
        self._prefetch = (upcoming, stream)

    def _take_prefetch(self, track: Track) -> Optional[media.AudioStream]:
        """
        The prefetched stream for ``track``, or ``None``.

        Matching is by identity, not URL: skip, remove, shuffle and previous can
        all change what plays next, and a stream fetched for a track that is no
        longer next must be closed, not reused and not leaked.
        """
        if self._prefetch_task is not None and not self._prefetch_task.done():
            self._prefetch_task.cancel()
        self._prefetch_task = None

        pending, self._prefetch = self._prefetch, None
        if pending is None:
            return None
        upcoming, stream = pending
        if upcoming is track:
            return stream
        self._discard(stream)
        return None

    def _discard(self, stream: media.AudioStream) -> None:
        """Close a stream we are not going to play, off the event loop."""
        self.bot.loop.run_in_executor(None, stream.close)

    async def _wait_for_end(self, track: Track) -> None:
        """Wait for the current track to finish, prefetching before it does."""
        delay = self._prefetch_delay(track)
        if delay is None:
            self._start_prefetch()
        else:
            try:
                await asyncio.wait_for(self._next.wait(), timeout=delay)
                return                      # ended early — skip, stop, effect
            except asyncio.TimeoutError:
                self._start_prefetch()
        await self._next.wait()

    def _seek_target(self) -> float:
        """
        Where a respawn should pick up, or 0 when seeking would be wrong.

        Live streams report no duration and cannot be seeked, and a position in
        the last couple of seconds would resume into silence or past the end.
        """
        duration = self.current.get("duration") if self.current else None
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

                stream = await self._open_stream(track)
                if stream is None:
                    # Move on rather than retry: a replay flag left set would
                    # make _advance hand back a track that is no longer there.
                    self._replay = False
                    self._resume_at = 0.0
                    self.current = None
                    continue
                self._stream = stream
                was_replay = self._replay
                self._replay = False
                seek_to = self._resume_at
                self._resume_at = 0.0
                self._seek_base = seek_to
                if stream.stdout is None:
                    raise RuntimeError("stream was closed before it could play")
                source = media.make_pipe_source(
                    stream.stdout, volume=self.volume,
                    ffmpeg_filter=self.effect_filter, seek_seconds=seek_to,
                )
                # discord.py starts its playback clock before its first read,
                # so it must not begin against an empty buffer — it would be
                # seconds behind immediately and burst frames to catch up.
                await self.bot.loop.run_in_executor(None, media.prime_source, source)
                vc.play(source, after=self._after_play)
                # Backdate the clock by the seek so a *second* effect change
                # resumes from the real position, not from the respawn point.
                self._start_ts = time.monotonic() - seek_to
                self._paused_total = 0.0
                self._paused_at = None
                spawned_at = time.monotonic()

                if not silent:
                    await self.announcer.now_playing(
                        self, track, track.get("requester") or self.guild.me)

                await self._wait_for_end(track)
                # Measured from the spawn, not from _start_ts, which is
                # backdated when resuming partway into a track.
                played = time.monotonic() - spawned_at
                source.cleanup()
                # close() waits on the child and reads its stderr, so keep it
                # off the event loop.
                await self.bot.loop.run_in_executor(None, stream.close)
                self._stream = None

                # A near-instant end that wasn't a user action means the source
                # failed to load (bot-check, unavailable). Tell the user.
                if (not self._destroyed and not self._skip and not was_replay
                        and played < LOAD_FAILURE_SECONDS):
                    track["error"] = stream.classify_error()   # cached by close()
                    await self.announcer.load_failed(track)
                    self.current = None
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Player loop crashed for guild %s", self.guild.id)
            self.destroy()

    async def _open_stream(self, track: Track) -> Optional[media.AudioStream]:
        """
        The track's audio: the prefetched stream if there is one, else a new one.

        A stream fetched while the previous track played starts instantly;
        otherwise this pays the 3–8s yt-dlp startup. ``None`` means yt-dlp could
        not even be started (no processes left, say). That is one track's
        failure — it is announced here so the loop can carry on with the queue
        instead of tearing the whole player down.
        """
        stream = self._take_prefetch(track)
        if stream is not None:
            return stream
        try:
            return await self.bot.loop.run_in_executor(
                None, media.spawn_stream, track)
        except Exception:
            log.exception("Could not start streaming in guild %s", self.guild.id)
            track["error"] = "unavailable"
            await self.announcer.load_failed(track)
            return None

    def _after_play(self, error: Optional[Exception]) -> None:
        """Runs in the voice thread — hand control back to the loop safely."""
        if error:
            log.warning("Playback error in guild %s: %s", self.guild.id, error)
        self.bot.loop.call_soon_threadsafe(self._next.set)

    async def _advance(self) -> tuple[Optional[Track], bool]:
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

    async def _idle_disconnect(self) -> None:
        await self.announcer.idle_disconnect(INACTIVITY_TIMEOUT)
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
        # A prefetched stream is a live yt-dlp process too — leaking it here
        # would put back exactly the zombies that #6 removed.
        if self._prefetch_task is not None and not self._prefetch_task.done():
            self._prefetch_task.cancel()
        self._prefetch_task = None
        if self._prefetch is not None:
            self._discard(self._prefetch[1])
            self._prefetch = None
        vc = self.voice
        if vc and vc.is_connected():
            asyncio.ensure_future(vc.disconnect(force=True))
        if self._task and not self._task.done():
            self._task.cancel()
        # destroy() is sync, so the Now Playing buttons are greyed out in a
        # task of their own; left live, they would act on a dead player.
        self.bot.loop.create_task(self.announcer.retire())
        if self._on_destroy is not None:
            self._on_destroy(self.guild.id)


class PlayerManager:
    """Holds one :class:`MusicPlayer` per active guild."""

    def __init__(self) -> None:
        self._players: dict[int, MusicPlayer] = {}

    def get(self, guild_id: int) -> Optional[MusicPlayer]:
        return self._players.get(guild_id)

    def get_or_create(self, bot: discord.Client, guild: discord.Guild,
                      channel: discord.abc.Messageable) -> MusicPlayer:
        """The guild's player, creating one if it has none or its last one died."""
        player = self._players.get(guild.id)
        if player is None or player.is_destroyed:
            player = MusicPlayer(bot, guild, channel, on_destroy=self.discard)
            self._players[guild.id] = player
        else:
            player.text_channel = channel   # follow the latest command channel
        return player

    def discard(self, guild_id: int) -> None:
        self._players.pop(guild_id, None)


# Singleton used across the whole bot.
players = PlayerManager()
