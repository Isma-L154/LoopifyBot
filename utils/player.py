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
        self._proc = None                # active yt-dlp streaming subprocess

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

    def apply_effect(self, name: Optional[str], filter_str: str) -> bool:
        """Restart the current track with a new FFmpeg filter (from the start)."""
        vc = self.voice
        if not (vc and self.current and (vc.is_playing() or vc.is_paused())):
            return False
        self.effect_name = name
        self.effect_filter = filter_str
        self._replay = True
        vc.stop()
        return True

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
                proc = await self.bot.loop.run_in_executor(None, media.spawn_stream, track)
                self._proc = proc
                was_replay = self._replay
                self._replay = False
                source = media.make_pipe_source(
                    proc.stdout, volume=self.volume, ffmpeg_filter=self.effect_filter,
                )
                vc.play(source, after=self._after_play)
                self._start_ts = time.monotonic()

                if not silent:
                    await self._safe_send(now_playing_embed(
                        track, track.get("requester") or self.guild.me,
                        loop_mode=self.loop_mode,
                    ))

                await self._next.wait()
                played = time.monotonic() - self._start_ts
                source.cleanup()               # stop FFmpeg
                media.kill_stream(proc)         # stop yt-dlp
                self._proc = None

                # A near-instant end that wasn't a user action means the source
                # failed to load (bot-check, unavailable). Tell the user.
                if (not self._destroyed and not self._skip and not was_replay
                        and played < 2.0):
                    track["error"] = await self.bot.loop.run_in_executor(
                        None, media.classify_stream_error, proc)
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
        media.kill_stream(self._proc)
        self._proc = None
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
