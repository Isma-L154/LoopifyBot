import asyncio
import discord
from discord.ext import commands

from services import youtube, spotify
from services.spotify import is_spotify_url
from utils.queue_manager import queue_manager
from utils.embeds import (
    now_playing_embed, queue_embed,
    error_embed, success_embed, info_embed
)
from utils.checks import user_in_voice, same_voice_channel


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Internal player ───────────────────────────────────────────────

    async def _play_next(self, ctx):
        """Called after a track ends. Plays the next one or stops."""
        gq = queue_manager.get(ctx.guild.id)
        track = gq.next_track()

        if not track:
            await ctx.send(embed=info_embed("Queue Ended", "No more tracks in queue. Use `!play` to add more!"))
            return

        # Lazy resolve for Spotify stubs
        if track.get("source") == "spotify" and not track.get("stream"):
            track = await spotify.resolve_track(track, loop=self.bot.loop)
            if not track:
                await ctx.send(embed=error_embed("Couldn't resolve Spotify track. Skipping..."))
                return await self._play_next(ctx)
            gq.current = track

        source = youtube.make_source(track["stream"], volume=gq.volume)

        def after(error):
            if error:
                print(f"[Player] Error: {error}")
            asyncio.run_coroutine_threadsafe(self._play_next(ctx), self.bot.loop)

        ctx.voice_client.play(source, after=after)
        await ctx.send(embed=now_playing_embed(track, ctx.author, loop_mode=gq.loop_mode))

    async def _ensure_voice(self, ctx):
        """Connect bot to voice if not already connected."""
        if not ctx.voice_client:
            await ctx.author.voice.channel.connect()
        elif ctx.voice_client.channel != ctx.author.voice.channel:
            await ctx.voice_client.move_to(ctx.author.voice.channel)

    # ── Commands ──────────────────────────────────────────────────────

    @commands.command(aliases=["p"])
    @user_in_voice()
    async def play(self, ctx, *, query: str):
        """Play a song from YouTube or Spotify. Accepts URLs or search terms."""
        await self._ensure_voice(ctx)
        gq = queue_manager.get(ctx.guild.id)

        async with ctx.typing():
            # ── Spotify ──
            if is_spotify_url(query):
                tracks = await spotify.resolve(query, loop=self.bot.loop)
                if not tracks:
                    return await ctx.send(embed=error_embed("Couldn't resolve Spotify link."))

                if len(tracks) == 1:
                    gq.add(tracks[0])
                    if not ctx.voice_client.is_playing():
                        await self._play_next(ctx)
                    else:
                        await ctx.send(embed=success_embed(f"Added to queue: **{tracks[0]['title']}**"))
                else:
                    for t in tracks:
                        gq.add(t)
                    await ctx.send(embed=success_embed(f"Added **{len(tracks)} tracks** from Spotify to the queue."))
                    if not ctx.voice_client.is_playing():
                        await self._play_next(ctx)
                return

            # ── YouTube playlist ──
            if "youtube.com/playlist" in query or "list=" in query:
                tracks = await youtube.get_playlist(query, loop=self.bot.loop)
                if not tracks:
                    return await ctx.send(embed=error_embed("Couldn't load playlist."))
                for t in tracks:
                    gq.add(t)
                await ctx.send(embed=success_embed(f"Added **{len(tracks)} tracks** from playlist to queue."))
                if not ctx.voice_client.is_playing():
                    await self._play_next(ctx)
                return

            # ── Single YouTube track / search ──
            track = await youtube.search(query, loop=self.bot.loop)
            if not track:
                return await ctx.send(embed=error_embed(f"No results found for `{query}`."))

            gq.add(track)
            if not ctx.voice_client.is_playing():
                await self._play_next(ctx)
            else:
                embed = discord.Embed(
                    description=f"➕ Added to queue: **[{track['title']}]({track['url']})**",
                    color=0x5865F2
                )
                if track.get("thumbnail"):
                    embed.set_thumbnail(url=track["thumbnail"])
                await ctx.send(embed=embed)

    @commands.command()
    @same_voice_channel()
    async def pause(self, ctx):
        """Pause the current track."""
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.pause()
            await ctx.send(embed=success_embed("Paused ⏸"))
        else:
            await ctx.send(embed=error_embed("Nothing is playing right now."))

    @commands.command()
    @same_voice_channel()
    async def resume(self, ctx):
        """Resume a paused track."""
        if ctx.voice_client and ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            await ctx.send(embed=success_embed("Resumed ▶️"))
        else:
            await ctx.send(embed=error_embed("Nothing is paused."))

    @commands.command()
    @same_voice_channel()
    async def skip(self, ctx):
        """Skip the current track."""
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()
            await ctx.send(embed=success_embed("Skipped ⏭"))
        else:
            await ctx.send(embed=error_embed("Nothing is playing."))

    @commands.command(aliases=["prev"])
    @same_voice_channel()
    async def previous(self, ctx):
        """Go back to the previous track."""
        gq = queue_manager.get(ctx.guild.id)
        track = gq.previous_track()
        if not track:
            return await ctx.send(embed=error_embed("No previous track in history."))
        if ctx.voice_client.is_playing():
            ctx.voice_client.stop()
        # Add it back to front so _play_next picks it up
        gq.queue.appendleft(track)
        gq.current = None
        await self._play_next(ctx)

    @commands.command(aliases=["dc", "leave"])
    @same_voice_channel()
    async def stop(self, ctx):
        """Stop music and disconnect the bot."""
        gq = queue_manager.get(ctx.guild.id)
        gq.clear()
        gq.current = None
        if ctx.voice_client:
            await ctx.voice_client.disconnect()
        queue_manager.delete(ctx.guild.id)
        await ctx.send(embed=success_embed("Disconnected and cleared queue."))

    @commands.command(aliases=["q"])
    async def queue(self, ctx, page: int = 1):
        """Show the current queue."""
        gq = queue_manager.get(ctx.guild.id)
        embed = queue_embed(gq.to_list(), gq.current, page=page)
        await ctx.send(embed=embed)

    @commands.command(aliases=["np", "current"])
    async def nowplaying(self, ctx):
        """Show the currently playing track."""
        gq = queue_manager.get(ctx.guild.id)
        if not gq.current:
            return await ctx.send(embed=error_embed("Nothing is playing right now."))
        await ctx.send(embed=now_playing_embed(gq.current, ctx.author, loop_mode=gq.loop_mode))

    @commands.command()
    @same_voice_channel()
    async def volume(self, ctx, vol: int):
        """Set volume (0–100)."""
        if not 0 <= vol <= 100:
            return await ctx.send(embed=error_embed("Volume must be between 0 and 100."))
        gq = queue_manager.get(ctx.guild.id)
        gq.volume = vol / 100
        if ctx.voice_client and ctx.voice_client.source:
            ctx.voice_client.source.volume = gq.volume
        await ctx.send(embed=success_embed(f"Volume set to **{vol}%** 🔊"))

    @commands.command()
    @same_voice_channel()
    async def loop(self, ctx, mode: str = "track"):
        """Set loop mode: track | queue | off"""
        mode = mode.lower()
        if mode not in ("track", "queue", "off"):
            return await ctx.send(embed=error_embed("Loop mode must be `track`, `queue`, or `off`."))
        gq = queue_manager.get(ctx.guild.id)
        gq.loop_mode = mode
        icons = {"track": "🔂", "queue": "🔁", "off": "➡️"}
        await ctx.send(embed=success_embed(f"Loop mode set to **{mode}** {icons[mode]}"))

    @commands.command()
    @same_voice_channel()
    async def shuffle(self, ctx):
        """Shuffle the queue."""
        gq = queue_manager.get(ctx.guild.id)
        if gq.is_empty:
            return await ctx.send(embed=error_embed("Queue is empty."))
        gq.shuffle()
        await ctx.send(embed=success_embed("Queue shuffled 🔀"))

    @commands.command()
    @same_voice_channel()
    async def remove(self, ctx, index: int):
        """Remove a track from the queue by its position."""
        gq = queue_manager.get(ctx.guild.id)
        track = gq.remove(index)
        if not track:
            return await ctx.send(embed=error_embed(f"No track at position {index}."))
        await ctx.send(embed=success_embed(f"Removed **{track['title']}** from queue."))

    @commands.command()
    @same_voice_channel()
    async def move(self, ctx, from_pos: int, to_pos: int):
        """Move a track in the queue: !move <from> <to>"""
        gq = queue_manager.get(ctx.guild.id)
        if gq.move(from_pos, to_pos):
            await ctx.send(embed=success_embed(f"Moved track from position **{from_pos}** to **{to_pos}**."))
        else:
            await ctx.send(embed=error_embed("Invalid positions."))

    @commands.command()
    @same_voice_channel()
    async def clear(self, ctx):
        """Clear the entire queue (keeps current track playing)."""
        gq = queue_manager.get(ctx.guild.id)
        gq.clear()
        await ctx.send(embed=success_embed("Queue cleared 🗑️"))

    @commands.command()
    @same_voice_channel()
    async def autoplay(self, ctx):
        """Toggle autoplay (auto-queue related tracks when queue ends)."""
        gq = queue_manager.get(ctx.guild.id)
        gq.autoplay = not gq.autoplay
        state = "enabled 🟢" if gq.autoplay else "disabled 🔴"
        await ctx.send(embed=success_embed(f"Autoplay {state}"))

    # ── Error handling ────────────────────────────────────────────────

    @play.error
    async def play_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=error_embed("Please provide a song name or URL.\nUsage: `!play <song>`"))
        else:
            await ctx.send(embed=error_embed(str(error)))

    @volume.error
    async def volume_error(self, ctx, error):
        if isinstance(error, commands.BadArgument):
            await ctx.send(embed=error_embed("Please provide a number between 0 and 100."))


async def setup(bot):
    await bot.add_cog(Music(bot))
