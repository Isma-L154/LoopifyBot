import asyncio
import logging
from typing import Optional, cast

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from services import media, suggestions
from services.media import Track
from utils import controls
from utils.player import players, MusicPlayer, MAX_QUEUE
from utils.context import GuildContext, hybrid_command
from utils.embeds import (added_embed, error_embed, now_playing_embed,
                          queue_embed, success_embed)
from utils.checks import user_in_voice, same_voice_channel

log = logging.getLogger("loopify.music")

# Query length cap — guards against absurd input before it reaches yt-dlp.
MAX_QUERY_LEN = 500

# How long to wait for a voice connection. discord.py reuses this same value as
# the deadline for Discord to confirm a *departure* while closing, so it is also
# the worst case a shutdown can take — it must stay under the unit's
# TimeoutStopSec or systemd kills the process mid-teardown. See issue #35.
VOICE_CONNECT_TIMEOUT = 20.0


def _is_playlist_url(query: str) -> bool:
    """Detect playlist/set/album URLs across supported sites."""
    q = query.lower()
    if not q.startswith("http"):
        return False
    if "list=" in q and "watch?v=" not in q:
        return True                        # YouTube playlist
    return "/sets/" in q or "/album/" in q  # SoundCloud set / Bandcamp album


async def _reply(ctx: GuildContext, outcome: controls.Outcome) -> None:
    """Report an action's outcome; under `/`, a failure only to whoever asked."""
    embed = success_embed(outcome.message) if outcome.ok else error_embed(outcome.message)
    await ctx.send(embed=embed, ephemeral=not outcome.ok)


class Music(commands.Cog, name="🎵 Music & Queue"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None

    async def cog_load(self) -> None:
        self._session = aiohttp.ClientSession()

    async def cog_unload(self) -> None:
        if self._session is not None:
            await self._session.close()

    # ── Helpers ───────────────────────────────────────────────────────

    async def _ensure_voice(self, ctx: GuildContext) -> bool:
        """Connect (or move) the bot to the author's voice channel."""
        # Checked again although @user_in_voice already did: play awaits a
        # DNS lookup in between, and the author can leave during it.
        dest = ctx.author.voice.channel if ctx.author.voice else None
        if dest is None:
            await ctx.send(embed=error_embed(
                "You must be in a voice channel to use this command."))
            return False
        perms = dest.permissions_for(ctx.me)
        if not perms.connect or not perms.speak:
            await ctx.send(embed=error_embed(
                "I need permission to **connect** and **speak** in that voice channel."
            ))
            return False
        vc = ctx.voice_client
        try:
            if vc is None:
                await dest.connect(timeout=VOICE_CONNECT_TIMEOUT, reconnect=True)
            elif vc.channel != dest:
                await vc.move_to(dest)
            return True
        except discord.ClientException as e:
            await ctx.send(embed=error_embed(f"Couldn't join voice: {e}"))
            return False
        except asyncio.TimeoutError:
            await ctx.send(embed=error_embed("Timed out connecting to voice."))
            return False

    def _player(self, ctx: GuildContext) -> MusicPlayer:
        return players.get_or_create(self.bot, ctx.guild, ctx.channel)

    # ── Playback commands ─────────────────────────────────────────────

    @hybrid_command(aliases=["p"])
    @app_commands.describe(query="A song to search for, or a link")
    @commands.cooldown(rate=3, per=5.0, type=commands.BucketType.user)
    @commands.max_concurrency(1, per=commands.BucketType.user, wait=False)
    @user_in_voice()
    async def play(self, ctx: GuildContext, *, query: str) -> None:
        """Play from YouTube, SoundCloud or a direct link. Accepts URLs or search terms.

        Tip: prefix a search with `sc:` to search SoundCloud, e.g. `!play sc: lofi`.
        """
        # Joining voice and asking yt-dlp can take far longer than the three
        # seconds Discord gives a slash command to answer. Once only: unlike
        # ctx.typing(), ctx.defer() fails on an interaction already answered.
        await ctx.defer()
        query = query.strip()
        if len(query) > MAX_QUERY_LEN:
            await ctx.send(embed=error_embed("That query is too long."))
            return
        if query.startswith("http") and not await media.is_public_url(query):
            await ctx.send(embed=error_embed(
                "I can only play links to public websites."))
            return
        if not await self._ensure_voice(ctx):
            return
        player = self._player(ctx)

        async with ctx.typing():
            if _is_playlist_url(query):
                tracks = await media.get_playlist(query, loop=self.bot.loop)
                if not tracks:
                    await ctx.send(embed=error_embed("Couldn't load that playlist."))
                    return
                return await self._enqueue(ctx, player, tracks, "playlist")

            track = await media.search(query, loop=self.bot.loop)
            if not track:
                await ctx.send(embed=error_embed(f"No results found for `{query}`."))
                return
            await self._enqueue(ctx, player, [track], None)

    @play.autocomplete("query")
    async def _suggest(self, _interaction: discord.Interaction,
                       current: str) -> list[app_commands.Choice[str]]:
        if self._session is None:
            return []
        terms = await suggestions.fetch(self._session, current)
        return [app_commands.Choice(name=t, value=t) for t in terms]

    async def _enqueue(self, ctx: GuildContext, player: MusicPlayer,
                       tracks: list[Track], batch_label: Optional[str]) -> None:
        """Add one or many tracks and report to the channel."""
        for t in tracks:
            t["requester"] = ctx.author        # who queued it (for Now Playing)
        was_idle = player.current is None and player.is_empty
        if len(tracks) == 1:
            if not player.add(tracks[0]):
                await ctx.send(embed=error_embed(
                    f"Queue is full (max {MAX_QUEUE} tracks)."
                ))
                return
            if not was_idle:
                await ctx.send(embed=added_embed(tracks[0]))
            elif ctx.interaction is not None:
                # Now Playing goes to the channel, not to this interaction, which
                # would otherwise be left "thinking..." forever.
                await ctx.send(embed=success_embed(
                    f"▶️ Starting **{tracks[0]['title']}**"))
        else:
            added = player.add_many(tracks)
            if added == 0:
                await ctx.send(embed=error_embed(
                    f"Queue is full (max {MAX_QUEUE} tracks)."
                ))
                return
            skipped = f" ({len(tracks) - added} skipped — queue full)" if added < len(tracks) else ""
            await ctx.send(embed=success_embed(
                f"Added **{added} tracks** from {batch_label} to the queue.{skipped}"
            ))

    @hybrid_command()
    @same_voice_channel()
    async def pause(self, ctx: GuildContext) -> None:
        """Pause the current track."""
        await _reply(ctx, controls.pause(players.get(ctx.guild.id)))

    @hybrid_command()
    @same_voice_channel()
    async def resume(self, ctx: GuildContext) -> None:
        """Resume a paused track."""
        await _reply(ctx, controls.resume(players.get(ctx.guild.id)))

    @hybrid_command()
    @same_voice_channel()
    async def skip(self, ctx: GuildContext) -> None:
        """Skip the current track."""
        await _reply(ctx, controls.skip(players.get(ctx.guild.id)))

    @hybrid_command(aliases=["prev"])
    @same_voice_channel()
    async def previous(self, ctx: GuildContext) -> None:
        """Go back to the previous track."""
        await _reply(ctx, controls.previous(players.get(ctx.guild.id)))

    @hybrid_command(aliases=["dc", "leave"])
    @same_voice_channel()
    async def stop(self, ctx: GuildContext) -> None:
        """Stop music and disconnect the bot."""
        player = players.get(ctx.guild.id)
        if player:
            controls.stop(player)
        elif ctx.voice_client:
            await ctx.voice_client.disconnect(force=True)
        await ctx.send(embed=success_embed(controls.STOPPED))

    # ── Queue commands ────────────────────────────────────────────────

    @hybrid_command(aliases=["q"])
    async def queue(self, ctx: GuildContext, page: int = 1) -> None:
        """Show the current queue."""
        player = players.get(ctx.guild.id)
        if not player:
            await ctx.send(embed=error_embed("Nothing is playing."))
            return
        await ctx.send(embed=queue_embed(player.to_list(), player.current, page=page))

    @hybrid_command(aliases=["np", "current"])
    async def nowplaying(self, ctx: GuildContext) -> None:
        """Show the currently playing track."""
        player = players.get(ctx.guild.id)
        if not player or not player.current:
            await ctx.send(embed=error_embed("Nothing is playing right now."))
            return
        await ctx.send(embed=now_playing_embed(player.current, ctx.author, loop_mode=player.loop_mode))

    @hybrid_command()
    @same_voice_channel()
    async def volume(self, ctx: GuildContext,
                     vol: commands.Range[int, 0, 100]) -> None:
        """Set volume (0–100)."""
        player = players.get(ctx.guild.id)
        if not player:
            await ctx.send(embed=error_embed("Nothing is playing."))
            return
        player.set_volume(vol / 100)
        await ctx.send(embed=success_embed(f"Volume set to **{vol}%** 🔊"))

    @hybrid_command()
    # Choices rather than a Literal annotation: the Literal converter is
    # case-sensitive, and `!loop Track` has always worked.
    @app_commands.choices(mode=[app_commands.Choice(name=m, value=m)
                                for m in ("track", "queue", "off")])
    @same_voice_channel()
    async def loop(self, ctx: GuildContext, mode: str = "track") -> None:
        """Set loop mode: track | queue | off"""
        mode = mode.lower()
        if mode not in controls.LOOP_ICONS:
            await ctx.send(embed=error_embed("Loop mode must be `track`, `queue`, or `off`."),
                           ephemeral=True)
            return
        await _reply(ctx, controls.set_loop(players.get(ctx.guild.id),
                                            cast(controls.LoopMode, mode)))

    @hybrid_command()
    @same_voice_channel()
    async def shuffle(self, ctx: GuildContext) -> None:
        """Shuffle the queue."""
        await _reply(ctx, controls.shuffle(players.get(ctx.guild.id)))

    @hybrid_command()
    @same_voice_channel()
    async def remove(self, ctx: GuildContext, index: int) -> None:
        """Remove a track from the queue by its position."""
        player = players.get(ctx.guild.id)
        track = player.remove(index) if player else None
        if not track:
            await ctx.send(embed=error_embed(f"No track at position {index}."))
            return
        await ctx.send(embed=success_embed(f"Removed **{track['title']}** from the queue."))

    @hybrid_command()
    @same_voice_channel()
    async def move(self, ctx: GuildContext, from_pos: int, to_pos: int) -> None:
        """Move a track to another position in the queue."""
        player = players.get(ctx.guild.id)
        if player and player.move(from_pos, to_pos):
            await ctx.send(embed=success_embed(f"Moved track **{from_pos}** → **{to_pos}**."))
        else:
            await ctx.send(embed=error_embed("Invalid positions."))

    @hybrid_command()
    @same_voice_channel()
    async def clear(self, ctx: GuildContext) -> None:
        """Clear the queue (keeps the current track playing)."""
        player = players.get(ctx.guild.id)
        if player:
            player.clear()
        await ctx.send(embed=success_embed("Queue cleared 🗑️"))

    @hybrid_command()
    @same_voice_channel()
    async def autoplay(self, ctx: GuildContext) -> None:
        """Toggle autoplay (auto-queue related tracks when the queue ends)."""
        player = players.get(ctx.guild.id)
        if not player:
            await ctx.send(embed=error_embed("Nothing is playing."))
            return
        player.autoplay = not player.autoplay
        state = "enabled 🟢" if player.autoplay else "disabled 🔴"
        await ctx.send(embed=success_embed(f"Autoplay {state}"))

    # ── Errors & lifecycle ────────────────────────────────────────────

    # Missing/bad arguments, cooldowns and concurrency limits are handled
    # centrally in utils.errors, so every command reports them the same way.

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member,
                                    before: discord.VoiceState,
                                    after: discord.VoiceState) -> None:
        """Disconnect shortly after the bot is left alone in a channel."""
        if member.bot:
            return
        # Typed as the VoiceProtocol base; this bot only connects with VoiceClient.
        vc = cast(Optional[discord.VoiceClient], member.guild.voice_client)
        if not vc:
            return
        if before.channel != vc.channel:
            return
        if len([m for m in vc.channel.members if not m.bot]) == 0:
            await asyncio.sleep(60)
            if vc.is_connected() and len([m for m in vc.channel.members if not m.bot]) == 0:
                player = players.get(member.guild.id)
                if player:
                    player.destroy()
                else:
                    await vc.disconnect(force=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))
