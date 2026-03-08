import asyncio
import discord
from discord.ext import commands
from config import DISCORD_TOKEN, COMMAND_PREFIX, COGS


intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(
    command_prefix=COMMAND_PREFIX,
    intents=intents,
    help_command=None   # We'll add a custom one below
)


# ── Events ────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name=f"{COMMAND_PREFIX}play"
        )
    )


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return  # Silently ignore unknown commands
    if isinstance(error, commands.CheckFailure):
        return  # Checks already send their own error embeds
    print(f"[Error] {ctx.command}: {error}")


# ── Custom help command ───────────────────────────────────────────────

@bot.command(name="help")
async def help_command(ctx):
    p = COMMAND_PREFIX
    embed = discord.Embed(title="🎵 Music Bot — Commands", color=0x1DB954)

    embed.add_field(name="▶️ Playback", value=(
        f"`{p}play <song/url>` — Play from YouTube or Spotify\n"
        f"`{p}pause` — Pause\n"
        f"`{p}resume` — Resume\n"
        f"`{p}skip` — Skip current track\n"
        f"`{p}previous` — Go back one track\n"
        f"`{p}stop` — Stop & disconnect\n"
        f"`{p}nowplaying` — Show current track"
    ), inline=False)

    embed.add_field(name="📋 Queue", value=(
        f"`{p}queue [page]` — View queue\n"
        f"`{p}shuffle` — Shuffle queue\n"
        f"`{p}remove <#>` — Remove a track\n"
        f"`{p}move <from> <to>` — Move a track\n"
        f"`{p}clear` — Clear queue\n"
        f"`{p}loop <track|queue|off>` — Loop mode\n"
        f"`{p}autoplay` — Toggle autoplay"
    ), inline=False)

    embed.add_field(name="🎛️ Effects", value=(
        f"`{p}bass` `{p}bassboost` `{p}nightcore`\n"
        f"`{p}vaporwave` `{p}treble` `{p}echo`\n"
        f"`{p}8d` `{p}karaoke` `{p}reset`\n"
        f"`{p}effects` — List all effects"
    ), inline=False)

    embed.add_field(name="🎤 Extras", value=(
        f"`{p}lyrics [song]` — Get song lyrics\n"
        f"`{p}volume <0-100>` — Set volume"
    ), inline=False)

    embed.set_footer(text="Tip: !play works with YouTube URLs, Spotify tracks, albums & playlists!")
    await ctx.send(embed=embed)


# ── Load cogs & run ───────────────────────────────────────────────────

async def main():
    async with bot:
        for cog in COGS:
            try:
                await bot.load_extension(cog)
                print(f"✅ Loaded cog: {cog}")
            except Exception as e:
                print(f"❌ Failed to load {cog}: {e}")
        await bot.start(DISCORD_TOKEN)


asyncio.run(main())
