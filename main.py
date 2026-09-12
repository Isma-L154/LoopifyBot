import asyncio
import logging

import discord
from discord.ext import commands

import config
from config import COGS, COMMAND_PREFIX, DISCORD_TOKEN
from utils import errors
from utils.help import build as build_help
from utils.startup import start as start_bot

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
)


@bot.event
async def on_ready():
    log.info("Logged in as %s (ID: %s) — serving %d guild(s)",
             bot.user, bot.user.id, len(bot.guilds))
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name=f"{COMMAND_PREFIX}play",
        )
    )


@bot.event
async def on_command_error(ctx, error):
    # All of it lives in utils.errors so it can be tested without a gateway.
    await errors.handle(ctx, error)


@bot.command(name="help")
async def help_command(ctx):
    """Show this message."""
    await ctx.send(embed=build_help(bot, COMMAND_PREFIX))


async def main():
    async with bot:
        for cog in COGS:
            try:
                await bot.load_extension(cog)
                log.info("Loaded cog: %s", cog)
            except Exception:
                log.exception("Failed to load cog: %s", cog)
        # Not bot.start(): the login half of it needs retrying.
        await start_bot(bot, DISCORD_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutting down (KeyboardInterrupt).")
