import time

import discord
from discord.ext import commands

from utils.player import players
from utils.embeds import success_embed, error_embed
from utils.checks import same_voice_channel

# Minimum seconds between effect changes per guild — each one restarts the
# FFmpeg process, so rapid toggling is throttled to protect CPU/memory.
_EFFECT_COOLDOWN = 3.0


# FFmpeg audio-filter presets. Each restarts the current track through
# ``-af <filter>`` while resuming near the current playback position.
EFFECTS = {
    "bass":       "equalizer=f=54:width_type=o:width=2:g=5",     # gentle low-end lift
    "bassboost":  "equalizer=f=54:width_type=o:width=2:g=10",    # heavy low-end lift
    "nightcore":  "asetrate=48000*1.25,aresample=48000",         # +pitch, +speed
    "vaporwave":  "asetrate=48000*0.8,aresample=48000",          # -pitch, -speed
    "treble":     "equalizer=f=8000:width_type=o:width=2:g=5",   # high-end lift
    "echo":       "aecho=0.8:0.88:60:0.4",                       # short echo
    "karaoke":    "pan=stereo|c0=c0-c1|c1=c1-c0",                # cancel centre vocals
    "8d":         "apulsator=hz=0.08",                           # rotating stereo
}

_LABELS = {
    "bass": "Bass boost applied 🔊",
    "bassboost": "Heavy bass boost applied 💥",
    "nightcore": "Nightcore effect applied 🌙✨",
    "vaporwave": "Vaporwave effect applied 🌊🎶",
    "treble": "Treble boost applied 🎵",
    "echo": "Echo effect applied 🔔",
    "8d": "8D audio applied 🎧 *Use headphones!*",
    "karaoke": "Karaoke mode on 🎤",
}


class Effects(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_change: dict[int, float] = {}   # guild_id → monotonic time

    def _throttled(self, ctx) -> bool:
        now = time.monotonic()
        last = self._last_change.get(ctx.guild.id, 0.0)
        if now - last < _EFFECT_COOLDOWN:
            return True
        self._last_change[ctx.guild.id] = now
        return False

    def _apply(self, ctx, name: str, filter_str: str) -> bool:
        player = players.get(ctx.guild.id)
        if not player:
            return False
        return player.apply_effect(name, filter_str)

    async def _run(self, ctx, name: str):
        if self._throttled(ctx):
            return await ctx.send(embed=error_embed(
                f"Easy — wait {_EFFECT_COOLDOWN:.0f}s between effect changes."
            ))
        if self._apply(ctx, name, EFFECTS[name]):
            await ctx.send(embed=success_embed(_LABELS[name]))
        else:
            await ctx.send(embed=error_embed("Nothing is playing."))

    # ── Effect commands ───────────────────────────────────────────────

    @commands.command()
    @same_voice_channel()
    async def bass(self, ctx):
        """Add a light bass boost."""
        await self._run(ctx, "bass")

    @commands.command()
    @same_voice_channel()
    async def bassboost(self, ctx):
        """Add a heavy bass boost."""
        await self._run(ctx, "bassboost")

    @commands.command()
    @same_voice_channel()
    async def nightcore(self, ctx):
        """Apply nightcore (faster + higher pitch)."""
        await self._run(ctx, "nightcore")

    @commands.command()
    @same_voice_channel()
    async def vaporwave(self, ctx):
        """Apply vaporwave (slower + lower pitch)."""
        await self._run(ctx, "vaporwave")

    @commands.command()
    @same_voice_channel()
    async def treble(self, ctx):
        """Boost treble frequencies."""
        await self._run(ctx, "treble")

    @commands.command()
    @same_voice_channel()
    async def echo(self, ctx):
        """Add an echo effect."""
        await self._run(ctx, "echo")

    @commands.command(name="8d")
    @same_voice_channel()
    async def eight_d(self, ctx):
        """Apply 8D audio (rotating stereo)."""
        await self._run(ctx, "8d")

    @commands.command()
    @same_voice_channel()
    async def karaoke(self, ctx):
        """Remove centre vocals."""
        await self._run(ctx, "karaoke")

    @commands.command(name="reset", aliases=["fxreset", "noeffect"])
    @same_voice_channel()
    async def reset_effect(self, ctx):
        """Remove all audio effects."""
        if self._throttled(ctx):
            return await ctx.send(embed=error_embed(
                f"Easy — wait {_EFFECT_COOLDOWN:.0f}s between effect changes."
            ))
        if self._apply(ctx, None, ""):
            await ctx.send(embed=success_embed("Audio effects removed ✅"))
        else:
            await ctx.send(embed=error_embed("Nothing is playing."))

    @commands.command(name="effect")
    async def current_effect(self, ctx):
        """Show the active audio effect."""
        player = players.get(ctx.guild.id)
        name = (player.effect_name if player else None) or "none"
        await ctx.send(embed=success_embed(f"Current effect: **{name}**"))

    @commands.command(name="effects")
    async def list_effects(self, ctx):
        """List all available audio effects."""
        names = ", ".join(f"`!{k}`" for k in EFFECTS)
        embed = discord.Embed(
            title="🎛️ Available Effects",
            description=names + "\n\nUse `!reset` to remove all effects.",
            color=0x5865F2,
        )
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Effects(bot))
