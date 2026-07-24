import asyncio
import logging

import discord
from discord.ext import commands

from services import media
from utils.player import players, MusicPlayer, MAX_QUEUE
from utils.embeds import queue_embed, now_playing_embed, error_embed, success_embed
from utils.checks import user_in_voice, same_voice_channel

log = logging.getLogger("loopify.music")

# Query length cap — guards against absurd input before it reaches yt-dlp.
MAX_QUERY_LEN = 500


def _is_playlist_url(query: str) -> bool:
    """Detect playlist/set/album URLs across supported sites."""
    q = query.lower()
    if not q.startswith("http"):
        return False
    if "list=" in q and "watch?v=" not in q:
        return True                        # YouTube playlist
    return "/sets/" in q or "/album/" in q  # SoundCloud set / Bandcamp album


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Helpers ───────────────────────────────────────────────────────

    async def _ensure_voice(self, ctx) -> bool:
        """Connect (or move) the bot to the author's voice channel."""
        dest = ctx.author.voice.channel
        perms = dest.permissions_for(ctx.me)
        if not perms.connect or not perms.speak:
            await ctx.send(embed=error_embed(
                "I need permission to **connect** and **speak** in that voice channel."
            ))
            return False
        vc = ctx.voice_client
        try:
            if vc is None:
                await dest.connect(timeout=20, reconnect=True)
            elif vc.channel != dest:
                await vc.move_to(dest)
            return True
        except discord.ClientException as e:
            await ctx.send(embed=error_embed(f"Couldn't join voice: {e}"))
            return False
        except asyncio.TimeoutError:
            await ctx.send(embed=error_embed("Timed out connecting to voice."))
            return False

    def _player(self, ctx) -> MusicPlayer:
        return players.get_or_create(self.bot, ctx)

    # ── Playback commands ─────────────────────────────────────────────

    @commands.command(aliases=["p"])
    @commands.cooldown(rate=3, per=5.0, type=commands.BucketType.user)
    @commands.max_concurrency(1, per=commands.BucketType.user, wait=False)
    @user_in_voice()
    async def play(self, ctx, *, query: str):
        """Play from YouTube, SoundCloud or a direct link. Accepts URLs or search terms.

        Tip: prefix a search with `sc:` to search SoundCloud, e.g. `!play sc: lofi`.
        """
        query = query.strip()
        if len(query) > MAX_QUERY_LEN:
            return await ctx.send(embed=error_embed("That query is too long."))
        if not await self._ensure_voice(ctx):
            return
        player = self._player(ctx)

        async with ctx.typing():
            if _is_playlist_url(query):
                tracks = await media.get_playlist(query, loop=self.bot.loop)
                if not tracks:
                    return await ctx.send(embed=error_embed("Couldn't load that playlist."))
                return await self._enqueue(ctx, player, tracks, "playlist")

            track = await media.search(query, loop=self.bot.loop)
            if not track:
                return await ctx.send(embed=error_embed(f"No results found for `{query}`."))
            await self._enqueue(ctx, player, [track], None)

    async def _enqueue(self, ctx, player: MusicPlayer, tracks: list[dict], batch_label):
        """Add one or many tracks and report to the channel."""
        for t in tracks:
            t["requester"] = ctx.author        # who queued it (for Now Playing)
        was_idle = player.current is None and player.is_empty
        if len(tracks) == 1:
            if not player.add(tracks[0]):
                return await ctx.send(embed=error_embed(
                    f"Queue is full (max {MAX_QUEUE} tracks)."
                ))
            if not was_idle:
                await ctx.send(embed=self._added_embed(tracks[0]))
        else:
            added = player.add_many(tracks)
            if added == 0:
                return await ctx.send(embed=error_embed(
                    f"Queue is full (max {MAX_QUEUE} tracks)."
                ))
            skipped = f" ({len(tracks) - added} skipped — queue full)" if added < len(tracks) else ""
            await ctx.send(embed=success_embed(
                f"Added **{added} tracks** from {batch_label} to the queue.{skipped}"
            ))
        # When idle, the player loop picks up the newly-added track automatically.

    @staticmethod
    def _added_embed(track: dict) -> discord.Embed:
        url = track.get("url") or track.get("spotify_url")
        title = f"[{track['title']}]({url})" if url else track["title"]
        embed = discord.Embed(description=f"➕ Added to queue: **{title}**", color=0x5865F2)
        if track.get("thumbnail"):
            embed.set_thumbnail(url=track["thumbnail"])
        return embed

    @commands.command()
    @same_voice_channel()
    async def pause(self, ctx):
        """Pause the current track."""
        vc = ctx.voice_client
        if vc and vc.is_playing():
            vc.pause()
            await ctx.send(embed=success_embed("Paused ⏸"))
        else:
            await ctx.send(embed=error_embed("Nothing is playing right now."))

    @commands.command()
    @same_voice_channel()
    async def resume(self, ctx):
        """Resume a paused track."""
        vc = ctx.voice_client
        if vc and vc.is_paused():
            vc.resume()
            await ctx.send(embed=success_embed("Resumed ▶️"))
        else:
            await ctx.send(embed=error_embed("Nothing is paused."))

    @commands.command()
    @same_voice_channel()
    async def skip(self, ctx):
        """Skip the current track."""
        player = players.get(ctx.guild.id)
        if player and player.skip():
            await ctx.send(embed=success_embed("Skipped ⏭"))
        else:
            await ctx.send(embed=error_embed("Nothing is playing."))

    @commands.command(aliases=["prev"])
    @same_voice_channel()
    async def previous(self, ctx):
        """Go back to the previous track."""
        player = players.get(ctx.guild.id)
        if player and player.go_previous():
            await ctx.send(embed=success_embed("Playing previous track ⏮"))
        else:
            await ctx.send(embed=error_embed("No previous track in history."))

    @commands.command(aliases=["dc", "leave"])
    @same_voice_channel()
    async def stop(self, ctx):
        """Stop music and disconnect the bot."""
        player = players.get(ctx.guild.id)
        if player:
            player.destroy()
        elif ctx.voice_client:
            await ctx.voice_client.disconnect(force=True)
        await ctx.send(embed=success_embed("Disconnected and cleared the queue."))

    # ── Queue commands ────────────────────────────────────────────────

    @commands.command(aliases=["q"])
    async def queue(self, ctx, page: int = 1):
        """Show the current queue."""
        player = players.get(ctx.guild.id)
        if not player:
            return await ctx.send(embed=error_embed("Nothing is playing."))
        await ctx.send(embed=queue_embed(player.to_list(), player.current, page=page))

    @commands.command(aliases=["np", "current"])
    async def nowplaying(self, ctx):
        """Show the currently playing track."""
        player = players.get(ctx.guild.id)
        if not player or not player.current:
            return await ctx.send(embed=error_embed("Nothing is playing right now."))
        await ctx.send(embed=now_playing_embed(player.current, ctx.author, loop_mode=player.loop_mode))

    @commands.command()
    @same_voice_channel()
    async def volume(self, ctx, vol: int):
        """Set volume (0–100)."""
        if not 0 <= vol <= 100:
            return await ctx.send(embed=error_embed("Volume must be between 0 and 100."))
        player = players.get(ctx.guild.id)
        if not player:
            return await ctx.send(embed=error_embed("Nothing is playing."))
        player.set_volume(vol / 100)
        await ctx.send(embed=success_embed(f"Volume set to **{vol}%** 🔊"))

    @commands.command()
    @same_voice_channel()
    async def loop(self, ctx, mode: str = "track"):
        """Set loop mode: track | queue | off"""
        mode = mode.lower()
        if mode not in ("track", "queue", "off"):
            return await ctx.send(embed=error_embed("Loop mode must be `track`, `queue`, or `off`."))
        player = players.get(ctx.guild.id)
        if not player:
            return await ctx.send(embed=error_embed("Nothing is playing."))
        player.loop_mode = mode
        icons = {"track": "🔂", "queue": "🔁", "off": "➡️"}
        await ctx.send(embed=success_embed(f"Loop mode set to **{mode}** {icons[mode]}"))

    @commands.command()
    @same_voice_channel()
    async def shuffle(self, ctx):
        """Shuffle the queue."""
        player = players.get(ctx.guild.id)
        if not player or player.is_empty:
            return await ctx.send(embed=error_embed("Queue is empty."))
        player.shuffle()
        await ctx.send(embed=success_embed("Queue shuffled 🔀"))

    @commands.command()
    @same_voice_channel()
    async def remove(self, ctx, index: int):
        """Remove a track from the queue by its position."""
        player = players.get(ctx.guild.id)
        track = player.remove(index) if player else None
        if not track:
            return await ctx.send(embed=error_embed(f"No track at position {index}."))
        await ctx.send(embed=success_embed(f"Removed **{track['title']}** from the queue."))

    @commands.command()
    @same_voice_channel()
    async def move(self, ctx, from_pos: int, to_pos: int):
        """Move a track in the queue: !move <from> <to>"""
        player = players.get(ctx.guild.id)
        if player and player.move(from_pos, to_pos):
            await ctx.send(embed=success_embed(f"Moved track **{from_pos}** → **{to_pos}**."))
        else:
            await ctx.send(embed=error_embed("Invalid positions."))

    @commands.command()
    @same_voice_channel()
    async def clear(self, ctx):
        """Clear the queue (keeps the current track playing)."""
        player = players.get(ctx.guild.id)
        if player:
            player.clear()
        await ctx.send(embed=success_embed("Queue cleared 🗑️"))

    @commands.command()
    @same_voice_channel()
    async def autoplay(self, ctx):
        """Toggle autoplay (auto-queue related tracks when the queue ends)."""
        player = players.get(ctx.guild.id)
        if not player:
            return await ctx.send(embed=error_embed("Nothing is playing."))
        player.autoplay = not player.autoplay
        state = "enabled 🟢" if player.autoplay else "disabled 🔴"
        await ctx.send(embed=success_embed(f"Autoplay {state}"))

    # ── Errors & lifecycle ────────────────────────────────────────────

    @play.error
    async def play_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=error_embed("Please provide a song name or URL.\nUsage: `!play <song>`"))
        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.send(embed=error_embed(
                f"Slow down — try again in {error.retry_after:.1f}s."
            ))
        elif isinstance(error, commands.MaxConcurrencyReached):
            await ctx.send(embed=error_embed("You already have a `!play` in progress."))

    @volume.error
    async def volume_error(self, ctx, error):
        if isinstance(error, commands.BadArgument):
            await ctx.send(embed=error_embed("Please provide a number between 0 and 100."))

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Disconnect shortly after the bot is left alone in a channel."""
        if member.bot:
            return
        vc = member.guild.voice_client
        if not vc:
            return
        # Only react to people leaving the bot's own channel.
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


async def setup(bot):
    await bot.add_cog(Music(bot))
