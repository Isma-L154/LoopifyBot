"""
Central command-error handling.

Every wrong invocation gets an answer. A command that fails silently is
indistinguishable from the bot being offline, so the only errors that stay
quiet here are the two where silence is correct: an unrecognised command (people
type the prefix by accident) and a failed check (checks explain themselves).

Generic failures that can happen to *any* command — missing arguments, bad
types, cooldowns — are handled here rather than per-command, so behaviour stays
consistent as commands are added.
"""

import logging

import discord
from discord.ext import commands

from utils.embeds import error_embed

log = logging.getLogger("loopify.errors")


def usage(ctx) -> str:
    """
    How the command should have been invoked, e.g. ``!move <from_pos> <to_pos>``.

    ``signature`` renders required parameters as ``<name>`` and optional ones as
    ``[name]``, so the hint stays correct as commands change.
    """
    command = ctx.command
    if command is None:
        return ""
    text = f"{ctx.clean_prefix}{command.qualified_name}"
    return f"{text} {command.signature}" if command.signature else text


def _input_detail(error: commands.UserInputError) -> str:
    """A one-line explanation of what was wrong with the arguments."""
    if isinstance(error, commands.MissingRequiredArgument):
        return f"Missing the `{error.param.name}` argument."
    if isinstance(error, commands.TooManyArguments):
        return "That command takes fewer arguments than you gave it."
    if isinstance(error, commands.BadArgument):
        return "One of those arguments isn't the right type."
    return "I couldn't make sense of that."


async def handle(ctx, error: Exception) -> None:
    """Reply to the user, or log, depending on what went wrong."""
    # discord.py wraps exceptions raised inside a command body.
    error = getattr(error, "original", error)

    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CheckFailure):
        return                      # the check already sent its own message
    if ctx.command is not None and ctx.command.has_error_handler():
        return                      # the command replied itself; don't repeat it

    if isinstance(error, commands.CommandOnCooldown):
        return await _reply(ctx, f"Slow down — try again in {error.retry_after:.1f}s.")

    if isinstance(error, commands.MaxConcurrencyReached):
        name = usage(ctx).split(" ", 1)[0] or "that command"
        return await _reply(ctx, f"You already have a `{name}` in progress.")

    if isinstance(error, commands.UserInputError):
        hint = f"\nUsage: `{usage(ctx)}`" if ctx.command is not None else ""
        return await _reply(ctx, f"{_input_detail(error)}{hint}")

    # Anything left is a bug or an outage — the operator's problem, not the
    # user's. Log it with a traceback and stay quiet in the channel.
    log.warning("Error in command %s: %s", ctx.command, error, exc_info=error)


async def _reply(ctx, message: str) -> None:
    try:
        await ctx.send(embed=error_embed(message))
    except (discord.HTTPException, discord.Forbidden) as e:
        log.debug("Could not report an error to the channel: %s", e)
