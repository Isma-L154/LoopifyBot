"""
The ``!help`` embed, generated from the commands actually registered.

Built from the live command registry rather than a hand-written list. The
previous hardcoded version had already drifted — `!effect` existed and was
undocumented — and a list of commands maintained beside the commands themselves
will always drift again.
"""

import discord
from discord.ext import commands

from utils.embeds import GREEN

FIELD_LIMIT = 1024      # Discord's hard cap on an embed field's value


def describe(command: commands.Command, prefix: str) -> str:
    """One help line: every name the command answers to, its args, its summary."""
    line = f"`{prefix}{command.name}"
    line += f" {command.signature}`" if command.signature else "`"
    line += "".join(f" `{prefix}{alias}`" for alias in command.aliases)
    return f"{line} — {command.short_doc}" if command.short_doc else line


def pack(title: str, lines: list[str]) -> list[tuple[str, str]]:
    """
    Group help lines into embed fields, splitting when one would exceed the cap.

    Discord rejects the whole message if any field runs over, so a cog that
    grows past the limit must split rather than silently fail to render.
    """
    fields: list[tuple[str, str]] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            fields.append((title if not fields else f"{title} (cont.)",
                           "\n".join(current)))

    for line in lines:
        if current and len("\n".join(current + [line])) > FIELD_LIMIT:
            flush()
            current = [line]
        else:
            current.append(line)
    flush()
    return fields


def build(bot: commands.Bot, prefix: str) -> discord.Embed:
    """The help embed for every visible command the bot has loaded."""
    embed = discord.Embed(title="🎵 Music Bot — Commands", color=GREEN)
    for cog in bot.cogs.values():
        visible = sorted((c for c in cog.get_commands() if not c.hidden),
                         key=lambda c: c.name)
        for name, value in pack(cog.qualified_name, [describe(c, prefix) for c in visible]):
            embed.add_field(name=name, value=value, inline=False)
    embed.set_footer(
        text=f"Tip: {prefix}play takes YouTube/SoundCloud searches "
             f"(prefix with sc:) and most links yt-dlp supports. "
             f"Every command also works as /command."
    )
    return embed
