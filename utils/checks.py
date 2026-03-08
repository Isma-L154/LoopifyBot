import discord
from discord.ext import commands


def user_in_voice():
    """Check: user must be in a voice channel."""
    async def predicate(ctx):
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send(embed=_error("You must be in a voice channel to use this command."))
            return False
        return True
    return commands.check(predicate)


def bot_in_voice():
    """Check: bot must be connected to voice."""
    async def predicate(ctx):
        if not ctx.voice_client:
            await ctx.send(embed=_error("I'm not connected to any voice channel."))
            return False
        return True
    return commands.check(predicate)


def same_voice_channel():
    """Check: user must be in the same voice channel as the bot."""
    async def predicate(ctx):
        if not ctx.author.voice:
            await ctx.send(embed=_error("You must be in a voice channel."))
            return False
        if ctx.voice_client and ctx.author.voice.channel != ctx.voice_client.channel:
            await ctx.send(embed=_error("You must be in the same voice channel as me."))
            return False
        return True
    return commands.check(predicate)


def _error(msg: str) -> discord.Embed:
    return discord.Embed(description=f"❌ {msg}", color=0xFF4444)
