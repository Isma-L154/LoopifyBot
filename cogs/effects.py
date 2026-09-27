import time
from dataclasses import dataclass
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import COMMAND_PREFIX
from utils.checks import in_same_voice_channel, same_voice_channel
from utils.context import GuildContext, hybrid_command
from utils.embeds import BLURPLE, error_embed, success_embed
from utils.player import players

# Minimum seconds between effect changes per guild — each one restarts the
# FFmpeg process, so rapid toggling is throttled to protect CPU/memory.
_EFFECT_COOLDOWN = 3.0


@dataclass(frozen=True)
class Effect:
    """An FFmpeg filter chain, what the bot says, and how fast it plays."""
    filter: str
    label: str
    # How fast this filter consumes audio. Only the pitch effects move it, and
    # synced lyrics need it to know where the song really is — wall-clock time
    # is a quarter short at 1.25x. Measured against FFmpeg in the tests.
    rate: float = 1.0


# The single source of truth for effects: the filter, the reply, and — via the
# command aliases built from these keys below — the command name itself. Adding
# an effect is one entry here and nothing else.
#
# Applying one respawns the stream through ``-af <filter>``, resuming at the
# current playback position — see ``MusicPlayer.apply_effect``.
EFFECTS: dict[str, Effect] = {
    # gentle / heavy low-end lift
    "bass":      Effect("equalizer=f=54:width_type=o:width=2:g=5", "Bass boost applied 🔊"),
    "bassboost": Effect("equalizer=f=54:width_type=o:width=2:g=10", "Heavy bass boost applied 💥"),
    # The leading `aresample=48000` is load-bearing: `asetrate` *reinterprets* a
    # stream's declared rate instead of scaling it, so without normalising first
    # the speed factor becomes 48000*N/<source rate> and differs per track. A
    # 44.1 kHz upload ran at 1.36x and a 22 kHz one at 2.72x, not 1.25x.
    "nightcore": Effect("aresample=48000,asetrate=48000*1.25,aresample=48000",
                        "Nightcore effect applied 🌙✨", rate=1.25),
    "vaporwave": Effect("aresample=48000,asetrate=48000*0.8,aresample=48000",
                        "Vaporwave effect applied 🌊🎶", rate=0.8),
    "treble":    Effect("equalizer=f=8000:width_type=o:width=2:g=5", "Treble boost applied 🎵"),
    "echo":      Effect("aecho=0.8:0.88:60:0.4", "Echo effect applied 🔔"),
    "karaoke":   Effect("pan=stereo|c0=c0-c1|c1=c1-c0", "Karaoke mode on 🎤"),
    "8d":        Effect("apulsator=hz=0.08", "8D audio applied 🎧 *Use headphones!*"),
}

# discord.py's CogMeta collects commands when the class body is executed, so a
# command cannot be registered per effect after the fact. One command carrying
# every effect name as an alias gets the same result from a single definition:
# `ctx.invoked_with` says which name the user actually typed.
_EFFECT_NAMES = list(EFFECTS)


class Effects(commands.Cog, name="🎛️ Audio Effects"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last_change: dict[int, float] = {}   # guild_id → monotonic time

    def _throttled(self, ctx: GuildContext) -> bool:
        now = time.monotonic()
        if now - self._last_change.get(ctx.guild.id, 0.0) < _EFFECT_COOLDOWN:
            return True
        self._last_change[ctx.guild.id] = now
        return False

    async def _switch_to(self, ctx: GuildContext, name: str | None, filter_str: str, label: str,
                         rate: float = 1.0) -> None:
        """Throttle, apply, and report — the whole path every effect command takes."""
        if self._throttled(ctx):
            await ctx.send(embed=error_embed(
                f"Easy — wait {_EFFECT_COOLDOWN:.0f}s between effect changes."
            ), ephemeral=True)
            return
        player = players.get(ctx.guild.id)
        if player and player.apply_effect(name, filter_str, rate):
            await ctx.send(embed=success_embed(label))
        else:
            await ctx.send(embed=error_embed("Nothing is playing."), ephemeral=True)

    @commands.command(name=_EFFECT_NAMES[0], aliases=_EFFECT_NAMES[1:],
                      help=f"Apply an audio effect. Use {COMMAND_PREFIX}effects to see "
                           "them all.")
    @same_voice_channel()
    async def apply_effect(self, ctx: GuildContext) -> None:
        # Always set here: this callback only runs when invoked by one of its names.
        name = (ctx.invoked_with or _EFFECT_NAMES[0]).lower()
        effect = EFFECTS[name]
        await self._switch_to(ctx, name, effect.filter, effect.label, effect.rate)

    @hybrid_command(name="reset", aliases=["fxreset", "noeffect"])
    @same_voice_channel()
    async def reset_effect(self, ctx: GuildContext) -> None:
        """Remove all audio effects."""
        await self._switch_to(ctx, None, "", "Audio effects removed ✅")

    @hybrid_command(name="effect")
    @app_commands.describe(name="The effect to apply; leave it out to see the active one")
    @app_commands.choices(name=[app_commands.Choice(name=n, value=n) for n in EFFECTS])
    async def effect(self, ctx: GuildContext, name: Optional[str] = None) -> None:
        """Show the active audio effect, or apply one by name."""
        if name is None:
            player = players.get(ctx.guild.id)
            active = (player.effect_name if player else None) or "none"
            await ctx.send(embed=success_embed(f"Current effect: **{active}**"))
            return
        name = name.lower()
        chosen = EFFECTS.get(name)
        if chosen is None:
            await ctx.send(embed=error_embed(
                f"Unknown effect `{name}`. See `{ctx.clean_prefix}effects`."), ephemeral=True)
            return
        if await in_same_voice_channel(ctx):
            await self._switch_to(ctx, name, chosen.filter, chosen.label, chosen.rate)

    @hybrid_command(name="effects")
    async def list_effects(self, ctx: GuildContext) -> None:
        """List all available audio effects."""
        prefix = ctx.clean_prefix
        if ctx.interaction is None:
            names = ", ".join(f"`{prefix}{name}`" for name in EFFECTS)
        else:
            # The per-effect shortcuts are aliases, and aliases do not exist
            # under `/`; there every effect goes through /effect.
            names = ("Pick one with `/effect`: "
                     + ", ".join(f"`{name}`" for name in EFFECTS))
        await ctx.send(embed=discord.Embed(
            title="🎛️ Available Effects",
            description=f"{names}\n\nUse `{prefix}reset` to remove all effects.",
            color=BLURPLE,
        ))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Effects(bot))
