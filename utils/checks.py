from typing import Callable, TypeVar

from discord.ext import commands

from utils.context import GuildContext
from utils.embeds import error_embed

T = TypeVar("T")


def user_in_voice() -> Callable[[T], T]:
    """Check: user must be in a voice channel."""
    async def predicate(ctx: GuildContext) -> bool:
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send(embed=error_embed(
                "You must be in a voice channel to use this command."), ephemeral=True)
            return False
        return True
    return commands.check(predicate)


async def in_same_voice_channel(ctx: GuildContext) -> bool:
    """The same-channel rule, callable on its own by a command that needs it
    for only some of its arguments."""
    if not ctx.author.voice:
        await ctx.send(embed=error_embed("You must be in a voice channel."), ephemeral=True)
        return False
    if ctx.voice_client and ctx.author.voice.channel != ctx.voice_client.channel:
        await ctx.send(embed=error_embed(
            "You must be in the same voice channel as me."), ephemeral=True)
        return False
    return True


def same_voice_channel() -> Callable[[T], T]:
    """Check: user must be in the same voice channel as the bot."""
    return commands.check(in_same_voice_channel)
