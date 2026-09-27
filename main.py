import asyncio
import logging
from typing import cast

import discord
from discord import app_commands
from discord.ext import commands

import config
from config import COGS, COMMAND_PREFIX, DISCORD_TOKEN
from utils import errors
from utils.context import guild_only
from utils.help import build as build_help
from utils.startup import serve

config.configure_logging()
config.log_runtime()
config.validate()

log = logging.getLogger("loopify")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(
    command_prefix=COMMAND_PREFIX,
    intents=intents,
    help_command=None,        # replaced by the generated one below
    case_insensitive=True,
    # Every command acts on a server's voice and queue, so `/` commands are not
    # offered in DMs at all. guild_only below still covers the `!` ones.
    allowed_contexts=app_commands.AppCommandContext(guild=True),
)
bot.add_check(guild_only)


@bot.event
async def on_ready() -> None:
    log.info("Logged in as %s (ID: %s) — serving %d guild(s)",
             bot.user, bot.user.id if bot.user else "?", len(bot.guilds))
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name="/play",
        )
    )


@bot.event
async def on_command_error(ctx: commands.Context[commands.Bot],
                           error: commands.CommandError) -> None:
    # All of it lives in utils.errors so it can be tested without a gateway.
    await errors.handle(ctx, error)


@bot.hybrid_command(name="help")
async def help_command(ctx: commands.Context[commands.Bot]) -> None:
    """Show this message."""
    await ctx.send(embed=build_help(bot, COMMAND_PREFIX))


async def main() -> None:
    async with bot:
        for cog in COGS:
            try:
                await bot.load_extension(cog)
                log.info("Loaded cog: %s", cog)
            except Exception:
                log.exception("Failed to load cog: %s", cog)
        # Not bot.start(): the login needs retrying, and voice has to be
        # released within a bound before close() waits it out. See utils.startup.
        # config.validate() has already exited if the token is missing.
        await serve(bot, cast(str, DISCORD_TOKEN))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutting down (KeyboardInterrupt).")
