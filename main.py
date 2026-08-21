import asyncio
import logging

import discord
from discord.ext import commands

import config
from config import DISCORD_TOKEN, COMMAND_PREFIX, COGS
from utils import errors

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
    help_command=None,        # custom help below
    case_insensitive=True,
)


# ── Events ────────────────────────────────────────────────────────────

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


# ── Custom help command ───────────────────────────────────────────────

@bot.command(name="help")
async def help_command(ctx):
    p = COMMAND_PREFIX
    embed = discord.Embed(title="🎵 Music Bot — Commands", color=0x1DB954)
    embed.add_field(name="▶️ Playback", value=(
        f"`{p}play <song/url>` — Play from YouTube, SoundCloud or a link\n"
        f"`{p}pause` · `{p}resume` · `{p}skip` · `{p}previous`\n"
        f"`{p}stop` — Stop & disconnect\n"
        f"`{p}nowplaying` — Show current track"
    ), inline=False)
    embed.add_field(name="📋 Queue", value=(
        f"`{p}queue [page]` · `{p}shuffle` · `{p}remove <#>`\n"
        f"`{p}move <from> <to>` · `{p}clear`\n"
        f"`{p}loop <track|queue|off>` · `{p}autoplay`"
    ), inline=False)
    embed.add_field(name="🎛️ Effects", value=(
        f"`{p}bass` `{p}bassboost` `{p}nightcore` `{p}vaporwave`\n"
        f"`{p}treble` `{p}echo` `{p}8d` `{p}karaoke` `{p}reset`\n"
        f"`{p}effects` — List all effects"
    ), inline=False)
    embed.add_field(name="🎤 Extras", value=(
        f"`{p}lyrics [song]` — Get song lyrics\n"
        f"`{p}volume <0-100>` — Set volume"
    ), inline=False)
    embed.set_footer(text=f"Tip: {p}play works with YouTube/SoundCloud searches (use sc:) and most links yt-dlp supports!")
    await ctx.send(embed=embed)


# ── Load cogs & run ───────────────────────────────────────────────────

async def main():
    async with bot:
        for cog in COGS:
            try:
                await bot.load_extension(cog)
                log.info("Loaded cog: %s", cog)
            except Exception:
                log.exception("Failed to load cog: %s", cog)
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutting down (KeyboardInterrupt).")
