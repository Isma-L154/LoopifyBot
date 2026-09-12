from discord.ext import commands

from utils.embeds import error_embed


def user_in_voice():
    """Check: user must be in a voice channel."""
    async def predicate(ctx):
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send(embed=error_embed(
                "You must be in a voice channel to use this command."))
            return False
        return True
    return commands.check(predicate)


def same_voice_channel():
    """Check: user must be in the same voice channel as the bot."""
    async def predicate(ctx):
        if not ctx.author.voice:
            await ctx.send(embed=error_embed("You must be in a voice channel."))
            return False
        if ctx.voice_client and ctx.author.voice.channel != ctx.voice_client.channel:
            await ctx.send(embed=error_embed(
                "You must be in the same voice channel as me."))
            return False
        return True
    return commands.check(predicate)
