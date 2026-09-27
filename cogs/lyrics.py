import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional, Sequence

import aiohttp
import discord
from discord.ext import commands

from services import lyrics_api, synced_lyrics
from services.media import Track
# Deliberately not `from ... import Lyrics`: the cog class below is called
# Lyrics too, and importing the bare name let it shadow the dataclass. The
# Genius fallback then built a Cog instead of a result and raised TypeError,
# which the command reported as nothing at all.
from services.synced_lyrics import Line, index_at, search_terms
from utils import now_playing_view
from utils.context import GuildContext, hybrid_command
from utils.embeds import (error_embed, info_embed, lyrics_embed, lyrics_pages,
                          success_embed, synced_lyrics_embed)
from utils.player import MusicPlayer, players

LyricsResult = synced_lyrics.Lyrics

log = logging.getLogger("loopify.lyrics")

# Discord allows roughly five message edits per five seconds per channel, and a
# fast song changes lines more often than that. The window always renders the
# current line, so skipping intermediate ones loses nothing.
#
# It has to stay below MAX_SLEEP: a threshold above the longest wakeup gap would
# delay ordinary line changes by a wakeup, which on lyrics is plainly visible.
MIN_EDIT_INTERVAL = 1.5
# Longest the follower ever sleeps. It normally waits exactly until the next
# line; capping it is how a pause, a skip or a stop gets noticed promptly
# without polling in a tight loop.
MAX_SLEEP = 2.0
MIN_SLEEP = 0.25

Loader = Callable[[Track], Awaitable[Optional[LyricsResult]]]


class LyricsFollower:
    """
    Keeps one message in step with what a guild is playing.

    It owns no task of its own, and it reads the player rather than being
    pushed to — so a pause, a skip, an effect change or a new track all show up
    simply as a different position on the next wakeup.
    """

    def __init__(self, message: discord.Message, player: MusicPlayer,
                 load: Loader, *,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 now: Callable[[], float] = time.monotonic) -> None:
        self.message = message
        self.player = player
        self._load = load
        self._sleep = sleep
        self._now = now
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True

    async def run(self) -> None:
        """Follow the music until the song, the player or the message runs out."""
        track: Optional[Track] = None
        lyrics: Optional[LyricsResult] = None
        shown: Optional[int] = None
        last_edit = float("-inf")

        while not self._stopped:
            current = self.player.current
            if current is None or self.player.is_destroyed:
                return
            if current is not track:
                track, shown = current, None
                lyrics = await self._load(current)

            if lyrics is None or not lyrics.synced:
                await self._sleep(MAX_SLEEP)      # wait for a track we can follow
                continue

            position = self.player.position
            index = index_at(lyrics.lines, position)
            if index != shown and self._now() - last_edit >= MIN_EDIT_INTERVAL:
                if not await self._show(lyrics, index, position, track):
                    return
                shown, last_edit = index, self._now()

            await self._sleep(self._until_next_line(lyrics.lines, index, position))

    async def _show(self, lyrics: LyricsResult, index: int, position: float,
                    track: Optional[Track]) -> bool:
        """Redraw the message. False means it is gone and we should stop."""
        try:
            await self.message.edit(embed=synced_lyrics_embed(
                lyrics.title, lyrics.artist, lyrics.lines, index,
                position, track.get("duration") if track else None,
            ))
            return True
        except (discord.NotFound, discord.Forbidden) as e:
            log.debug("Lyrics message is no longer editable: %s", e)
            return False
        except discord.HTTPException as e:
            log.warning("Could not update lyrics: %s", e)
            return True          # a rate limit or a blip, not a reason to stop

    def _until_next_line(self, lines: Sequence[Line], index: int,
                         position: float) -> float:
        """
        How long until the next line, in real seconds.

        The gap is in song-seconds, and a speed effect makes those pass faster
        or slower than wall time: at 1.25x a line ten song-seconds away arrives
        in eight.
        """
        following = index + 1
        if following >= len(lines):
            return MAX_SLEEP
        rate = self.player.effect_rate or 1.0
        gap = (lines[following][0] - position) / rate
        return min(MAX_SLEEP, max(MIN_SLEEP, gap))


class LyricsPages(discord.ui.View):
    """Page buttons for lyrics that cannot be followed."""

    def __init__(self, title: str, artist: str, text: str, note: str) -> None:
        super().__init__(timeout=600)
        self.song_title = title
        self.artist = artist
        self.text = text
        self.note = note
        self.page = 0
        self.total = len(lyrics_pages(text))

    def embed(self) -> discord.Embed:
        return lyrics_embed(self.song_title, self.artist, self.text,
                            self.page, self.note)

    async def _turn(self, interaction: discord.Interaction, by: int) -> None:
        self.page = (self.page + by) % self.total
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(emoji="◀", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction,
                       _button: "discord.ui.Button[LyricsPages]") -> None:
        await self._turn(interaction, -1)

    @discord.ui.button(emoji="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction,
                   _button: "discord.ui.Button[LyricsPages]") -> None:
        await self._turn(interaction, 1)


class FollowControls(discord.ui.View):
    """A stop button on the live message, so no command has to be typed."""

    def __init__(self, on_stop: Callable[[], None]) -> None:
        super().__init__(timeout=None)
        self._on_stop = on_stop

    @discord.ui.button(label="Stop", emoji="⏹",
                       style=discord.ButtonStyle.secondary)
    async def stop_following(self, interaction: discord.Interaction,
                             _button: "discord.ui.Button[FollowControls]") -> None:
        self._on_stop()
        await interaction.response.edit_message(view=None)


class Lyrics(commands.Cog, name="\U0001f3a4 Lyrics"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None
        self._following: dict[int, tuple[LyricsFollower, asyncio.Task]] = {}

    async def cog_load(self) -> None:
        # One session for the cog. Building one per request is what leaked in #34.
        self._session = aiohttp.ClientSession()
        now_playing_view.set_lyrics_provider(self.show_current)

    async def cog_unload(self) -> None:
        now_playing_view.set_lyrics_provider(None)
        for guild_id in list(self._following):
            self.stop_following(guild_id)
        if self._session is not None:
            await self._session.close()

    # -- Looking lyrics up ---------------------------------------------

    @staticmethod
    def _split_query(query: str) -> tuple[str, str]:
        """Split a ``title - artist`` query; one without the separator is all title."""
        if " - " in query:
            title, artist = query.split(" - ", 1)
            return title.strip(), artist.strip()
        return query.strip(), ""

    async def _find(self, title: str, artist: str,
                    duration: Optional[float]) -> Optional[LyricsResult]:
        """LRCLIB first, since it is the only source with timings, then Genius."""
        if self._session is None:
            raise RuntimeError("the Lyrics cog was used before cog_load")
        found = await synced_lyrics.fetch(self._session, title, artist, duration)
        if found is not None:
            return found
        fallback = await lyrics_api.fetch(title, artist)
        if fallback is None:
            return None
        return LyricsResult(title=fallback["title"], artist=fallback["artist"],
                            plain=fallback["lyrics"])

    async def _search(self, query: str) -> Optional[LyricsResult]:
        """
        Look up a typed query, in either order.

        The help documents ``<title> - <artist>``, but ``The Police - Every
        Breath You Take`` is how people actually type it. The documented order
        is tried first, and the other only if it found nothing.
        """
        title, artist = self._split_query(query)
        found = await self._find(title, artist, None)
        if found is None and artist:
            found = await self._find(artist, title, None)
        return found

    async def _for_track(self, track: Track) -> Optional[LyricsResult]:
        """Look a playing track up, with its title tidied into search terms."""
        title, artist = search_terms(track.get("title") or "",
                                     track.get("uploader") or "")
        return await self._find(title, artist, track.get("duration"))

    # -- Following -----------------------------------------------------

    def stop_following(self, guild_id: int) -> None:
        """End a guild's follower, if it has one. Safe to call twice."""
        entry = self._following.pop(guild_id, None)
        if entry is None:
            return
        follower, task = entry
        follower.stop()
        if not task.done():
            task.cancel()

    async def _follow(self, dest: discord.abc.Messageable, player: MusicPlayer,
                      lyrics: LyricsResult) -> None:
        """Post the live message and start keeping it up to date."""
        guild_id = player.guild.id
        self.stop_following(guild_id)          # one per guild; the newest wins
        index = index_at(lyrics.lines, player.position)
        message = await dest.send(
            embed=synced_lyrics_embed(
                lyrics.title, lyrics.artist, lyrics.lines, index,
                player.position,
                player.current.get("duration") if player.current else None),
            view=FollowControls(lambda: self.stop_following(guild_id)),
        )
        follower = LyricsFollower(message, player, self._for_track)
        task = self.bot.loop.create_task(self._run_follower(guild_id, follower))
        self._following[guild_id] = (follower, task)

    async def _run_follower(self, guild_id: int, follower: LyricsFollower) -> None:
        try:
            await follower.run()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Lyrics follower crashed for guild %s", guild_id)
        finally:
            self._following.pop(guild_id, None)

    # -- Showing what was found ---------------------------------------

    async def _present(self, dest: discord.abc.Messageable, found: LyricsResult,
                       player: Optional[MusicPlayer]) -> None:
        """
        Post lyrics that were found. ``player`` is given when they are the
        playing track's, and only then can they be followed: a lyric needs a
        clock, and a search result has none.
        """
        if found.instrumental:
            await dest.send(embed=info_embed(
                "\U0001f3b5 Instrumental",
                f"**{found.title}** has no lyrics to show."
            ))
            return
        if found.synced and player and player.current:
            return await self._follow(dest, player, found)

        note = "not synced - showing the full lyrics" if player else ""
        view = LyricsPages(found.title, found.artist, found.plain, note)
        if view.total > 1:
            await dest.send(embed=view.embed(), view=view)
        else:
            await dest.send(embed=view.embed())

    async def show_current(self, dest: discord.abc.Messageable,
                           player: MusicPlayer) -> bool:
        """Post the playing track's lyrics. False when there are none to post."""
        if not player.current:
            return False
        found = await self._for_track(player.current)
        if found is None:
            return False
        await self._present(dest, found, player)
        return True

    # -- The command ---------------------------------------------------

    @hybrid_command(aliases=["ly"])
    async def lyrics(self, ctx: GuildContext, *,
                     query: Optional[str] = None) -> None:
        """Follow the lyrics of the current song, or look a song up."""
        # Ephemeral under `/`: the lyrics themselves go to the channel (below),
        # so all this interaction ever shows is an error or an acknowledgement.
        async with ctx.typing(ephemeral=True):
            player = players.get(ctx.guild.id)
            if query:
                title = self._split_query(query)[0]
                found = await self._search(query)
            elif player and player.current:
                title = player.current.get("title", "")
                found = await self._for_track(player.current)
            else:
                await ctx.send(embed=error_embed(
                    f"Nothing is playing. Provide a song name: "
                    f"`{ctx.clean_prefix}lyrics <title>`"
                ), ephemeral=True)
                return

            if found is None:
                await ctx.send(embed=error_embed(
                    f"Couldn't find lyrics for **{title}**."
                ), ephemeral=True)
                return
            if ctx.interaction is None:
                await self._present(ctx, found, None if query else player)
                return
            # A reply to an interaction can only be edited with its token, which
            # expires after 15 minutes; the live message is edited for as long
            # as the music plays. Posted to the channel, it is the bot's own.
            await self._present(ctx.channel, found, None if query else player)
            await ctx.send(embed=success_embed("🎤 Lyrics posted."),
                           ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Lyrics(bot))
