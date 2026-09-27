"""
The generated help must describe every command the bot actually has.

This is the regression guard for the drift the hardcoded help had accumulated:
`!effect` existed and was missing from the list, and nothing failed. Building
the embed from the live registry makes that impossible, and these tests fail if
anyone reintroduces a hand-maintained list.
"""

import discord
import pytest
from discord.ext import commands

from config import COGS
from utils.help import FIELD_LIMIT, build, describe, pack

PREFIX = "!"


@pytest.fixture
async def bot():
    """A bot with the real cogs loaded, but no gateway connection."""
    b = commands.Bot(command_prefix=PREFIX, intents=discord.Intents.none(),
                     help_command=None)
    for cog in COGS:
        await b.load_extension(cog)
    return b


def help_text(embed: discord.Embed) -> str:
    return "\n".join(f"{field.name}\n{field.value}" for field in embed.fields)


async def test_every_registered_command_is_documented(bot):
    text = help_text(build(bot, PREFIX))
    undocumented = [c.name for c in bot.commands
                    if not c.hidden and f"{PREFIX}{c.name}" not in text]
    assert undocumented == [], f"commands missing from !help: {undocumented}"


async def test_effect_is_documented(bot):
    """The exact command the hardcoded help had silently dropped."""
    assert f"{PREFIX}effect [name]`" in help_text(build(bot, PREFIX))


async def test_every_alias_is_documented(bot):
    """Aliases are how every effect except the first is reachable."""
    text = help_text(build(bot, PREFIX))
    missing = [a for c in bot.commands for a in c.aliases if f"{PREFIX}{a}`" not in text]
    assert missing == [], f"aliases missing from !help: {missing}"


async def test_no_field_exceeds_discord_limit(bot):
    """Discord rejects the whole message if one field runs over."""
    for field in build(bot, PREFIX).fields:
        assert len(field.value) <= FIELD_LIMIT
        assert len(field.name) <= 256


async def test_commands_carry_a_summary(bot):
    """A command with no docstring renders as a bare name and helps nobody."""
    text = help_text(build(bot, PREFIX))
    assert "—" in text


def test_pack_splits_instead_of_overflowing():
    lines = [f"`!command{i}` — a reasonably wordy description of what it does"
             for i in range(40)]
    fields = pack("Cog", lines)
    assert len(fields) > 1, "40 lines must not fit in one field"
    assert all(len(value) <= FIELD_LIMIT for _, value in fields)
    assert sum(value.count("\n") + 1 for _, value in fields) == len(lines)


def test_pack_keeps_a_single_field_when_it_fits():
    assert len(pack("Cog", ["`!one` — does one thing"])) == 1


def test_describe_renders_arguments_and_aliases():
    @commands.command(name="move", aliases=["mv"])
    async def move(ctx, from_pos: int, to_pos: int):
        """Move a track."""

    line = describe(move, PREFIX)
    assert "`!move <from_pos> <to_pos>`" in line
    assert "`!mv`" in line
    assert "Move a track." in line


async def test_the_owner_sync_command_stays_out_of_help(bot):
    assert bot.get_command("sync").hidden
    assert f"{PREFIX}sync" not in help_text(build(bot, PREFIX))
