import discord
from discord.ext import commands
from services import youtube
from utils.queue_manager import queue_manager
from utils.embeds import success_embed, error_embed
from utils.checks import same_voice_channel


# FFmpeg audio filter presets
EFFECTS = {
    "bass":       "equalizer=f=54:width_type=o:width=2:g=5",
    "bassboost":  "equalizer=f=54:width_type=o:width=2:g=10",
    "nightcore":  "asetrate=48000*1.25,aresample=48000",
    "vaporwave":  "asetrate=48000*0.8,aresample=48000",
    "treble":     "equalizer=f=8000:width_type=o:width=2:g=5",
    "echo":       "aecho=0.8:0.88:60:0.4",
    "karaoke":    "pan=stereo|c0=c0-c1|c1=c1-c0",
    "8d":         "apulsator=hz=0.08",
}


class Effects(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._current_effect: dict[int, str] = {}   # guild_id → effect name

    def _restart_with_filter(self, ctx, filter_str: str = ""):
        """Stop current audio and restart it with a new FFmpeg filter."""
        gq = queue_manager.get(ctx.guild.id)
        if not gq.current or not gq.current.get("stream"):
            return False

        vc = ctx.voice_client
        if not vc:
            return False

        vc.stop()
        source = youtube.make_source(
            gq.current["stream"],
            volume=gq.volume,
            ffmpeg_filter=filter_str
        )

        def after(error):
            import asyncio
            if error:
                print(f"[Effects] Error: {error}")
            # Re-use the music cog's _play_next
            music_cog = ctx.bot.get_cog("Music")
            if music_cog:
                asyncio.run_coroutine_threadsafe(
                    music_cog._play_next(ctx), ctx.bot.loop
                )

        vc.play(source, after=after)
        return True

    # ── Individual effect commands ─────────────────────────────────

    @commands.command()
    @same_voice_channel()
    async def bass(self, ctx):
        """Add a light bass boost."""
        if self._restart_with_filter(ctx, EFFECTS["bass"]):
            self._current_effect[ctx.guild.id] = "bass"
            await ctx.send(embed=success_embed("Bass boost applied 🔊"))
        else:
            await ctx.send(embed=error_embed("Nothing is playing."))

    @commands.command()
    @same_voice_channel()
    async def bassboost(self, ctx):
        """Add a heavy bass boost."""
        if self._restart_with_filter(ctx, EFFECTS["bassboost"]):
            self._current_effect[ctx.guild.id] = "bassboost"
            await ctx.send(embed=success_embed("Heavy bass boost applied 💥"))
        else:
            await ctx.send(embed=error_embed("Nothing is playing."))

    @commands.command()
    @same_voice_channel()
    async def nightcore(self, ctx):
        """Apply nightcore effect (faster + higher pitch)."""
        if self._restart_with_filter(ctx, EFFECTS["nightcore"]):
            self._current_effect[ctx.guild.id] = "nightcore"
            await ctx.send(embed=success_embed("Nightcore effect applied 🌙✨"))
        else:
            await ctx.send(embed=error_embed("Nothing is playing."))

    @commands.command()
    @same_voice_channel()
    async def vaporwave(self, ctx):
        """Apply vaporwave effect (slower + lower pitch)."""
        if self._restart_with_filter(ctx, EFFECTS["vaporwave"]):
            self._current_effect[ctx.guild.id] = "vaporwave"
            await ctx.send(embed=success_embed("Vaporwave effect applied 🌊🎶"))
        else:
            await ctx.send(embed=error_embed("Nothing is playing."))

    @commands.command()
    @same_voice_channel()
    async def treble(self, ctx):
        """Boost treble frequencies."""
        if self._restart_with_filter(ctx, EFFECTS["treble"]):
            self._current_effect[ctx.guild.id] = "treble"
            await ctx.send(embed=success_embed("Treble boost applied 🎵"))
        else:
            await ctx.send(embed=error_embed("Nothing is playing."))

    @commands.command()
    @same_voice_channel()
    async def echo(self, ctx):
        """Add an echo effect."""
        if self._restart_with_filter(ctx, EFFECTS["echo"]):
            self._current_effect[ctx.guild.id] = "echo"
            await ctx.send(embed=success_embed("Echo effect applied 🔔"))
        else:
            await ctx.send(embed=error_embed("Nothing is playing."))

    @commands.command(name="8d")
    @same_voice_channel()
    async def eight_d(self, ctx):
        """Apply 8D audio effect (rotating stereo)."""
        if self._restart_with_filter(ctx, EFFECTS["8d"]):
            self._current_effect[ctx.guild.id] = "8d"
            await ctx.send(embed=success_embed("8D audio applied 🎧 *Use headphones!*"))
        else:
            await ctx.send(embed=error_embed("Nothing is playing."))

    @commands.command()
    @same_voice_channel()
    async def karaoke(self, ctx):
        """Apply karaoke filter (removes center vocals)."""
        if self._restart_with_filter(ctx, EFFECTS["karaoke"]):
            self._current_effect[ctx.guild.id] = "karaoke"
            await ctx.send(embed=success_embed("Karaoke mode on 🎤"))
        else:
            await ctx.send(embed=error_embed("Nothing is playing."))

    @commands.command(name="reset", aliases=["fxreset", "noeffect"])
    @same_voice_channel()
    async def reset_effect(self, ctx):
        """Remove all audio effects."""
        if self._restart_with_filter(ctx, ""):
            self._current_effect.pop(ctx.guild.id, None)
            await ctx.send(embed=success_embed("Audio effects removed ✅"))
        else:
            await ctx.send(embed=error_embed("Nothing is playing."))

    @commands.command(name="effect")
    async def current_effect(self, ctx):
        """Show the current active audio effect."""
        effect = self._current_effect.get(ctx.guild.id, "none")
        await ctx.send(embed=success_embed(f"Current effect: **{effect}**"))

    @commands.command(name="effects")
    async def list_effects(self, ctx):
        """List all available audio effects."""
        names = ", ".join(f"`!{k}`" for k in EFFECTS)
        embed = discord.Embed(
            title="🎛️ Available Effects",
            description=names + "\n\nUse `!reset` to remove all effects.",
            color=0x5865F2
        )
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Effects(bot))
