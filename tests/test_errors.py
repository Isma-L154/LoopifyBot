"""
Central command-error handling.

The rule under test: a command invoked wrongly must always say so. A silent
failure is indistinguishable from the bot being down.
"""

import inspect
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord.ext import commands

from utils import errors


@pytest.fixture
def ctx():
    c = MagicMock()
    c.send = AsyncMock()
    c.clean_prefix = "!"
    c.command = MagicMock()
    c.command.qualified_name = "volume"
    c.command.signature = "<vol>"
    c.command.has_error_handler.return_value = False
    return c


def sent_text(ctx) -> str:
    assert ctx.send.await_count == 1, f"expected exactly one reply, got {ctx.send.await_count}"
    embed = ctx.send.await_args.kwargs["embed"]
    return embed.description or ""


def missing_arg(name: str = "vol") -> commands.MissingRequiredArgument:
    # discord.py has its own Parameter type, distinct from inspect.Parameter.
    param = commands.Parameter(name=name, kind=inspect.Parameter.POSITIONAL_OR_KEYWORD)
    return commands.MissingRequiredArgument(param)


# -- usage strings -----------------------------------------------------

def test_usage_includes_prefix_name_and_signature(ctx):
    assert errors.usage(ctx) == "!volume <vol>"


def test_usage_omits_the_signature_when_a_command_takes_no_arguments(ctx):
    ctx.command.qualified_name = "skip"
    ctx.command.signature = ""
    assert errors.usage(ctx) == "!skip"


def test_usage_is_empty_for_an_unknown_command(ctx):
    ctx.command = None
    assert errors.usage(ctx) == ""


def test_usage_follows_a_custom_prefix(ctx):
    ctx.clean_prefix = "?"
    assert errors.usage(ctx) == "?volume <vol>"


# -- the bug: silent failures ------------------------------------------

async def test_a_missing_argument_is_explained(ctx):
    """`!volume` with no number used to produce total silence."""
    await errors.handle(ctx, missing_arg("vol"))
    text = sent_text(ctx)
    assert "vol" in text
    assert "!volume <vol>" in text


async def test_a_bad_argument_is_explained(ctx):
    """`!queue abc` used to be logged server-side and never answered."""
    ctx.command.qualified_name = "queue"
    ctx.command.signature = "[page]"
    await errors.handle(ctx, commands.BadArgument("not an int"))
    assert "!queue [page]" in sent_text(ctx)


async def test_too_many_arguments_is_explained(ctx):
    await errors.handle(ctx, commands.TooManyArguments())
    assert "!volume <vol>" in sent_text(ctx)


async def test_any_other_user_input_error_still_gets_a_reply(ctx):
    """UserInputError has many subclasses; none of them may go unanswered."""
    await errors.handle(ctx, commands.UserInputError("something odd"))
    assert "!volume <vol>" in sent_text(ctx)


@pytest.mark.parametrize("command_name,signature", [
    ("volume", "<vol>"),
    ("remove", "<index>"),
    ("move", "<from_pos> <to_pos>"),
    ("play", "<query>"),
])
async def test_every_argument_taking_command_reports_its_usage(ctx, command_name, signature):
    ctx.command.qualified_name = command_name
    ctx.command.signature = signature
    await errors.handle(ctx, missing_arg())
    assert f"!{command_name} {signature}" in sent_text(ctx)


# -- generic failures that apply to any command ------------------------

async def test_cooldown_reports_the_wait(ctx):
    error = commands.CommandOnCooldown(MagicMock(), retry_after=2.5, type=MagicMock())
    await errors.handle(ctx, error)
    assert "2.5" in sent_text(ctx)


async def test_max_concurrency_names_the_command(ctx):
    ctx.command.qualified_name = "play"
    error = commands.MaxConcurrencyReached(number=1, per=MagicMock())
    await errors.handle(ctx, error)
    assert "!play" in sent_text(ctx)


# -- things that must stay quiet ---------------------------------------

async def test_an_unknown_command_is_ignored(ctx):
    """People type a prefix by accident; the bot must not nag."""
    await errors.handle(ctx, commands.CommandNotFound())
    ctx.send.assert_not_awaited()


async def test_a_failed_check_is_ignored_because_checks_explain_themselves(ctx):
    await errors.handle(ctx, commands.CheckFailure())
    ctx.send.assert_not_awaited()


async def test_a_command_with_its_own_handler_is_left_alone(ctx):
    """Otherwise the user gets the same complaint twice."""
    ctx.command.has_error_handler.return_value = True
    await errors.handle(ctx, missing_arg())
    ctx.send.assert_not_awaited()


async def test_an_unexpected_error_is_logged_not_shown(ctx, caplog):
    """Internal failures are the operator's problem, not the user's."""
    await errors.handle(ctx, RuntimeError("something broke internally"))
    ctx.send.assert_not_awaited()
    assert "something broke internally" in caplog.text


async def test_the_original_exception_is_unwrapped(ctx):
    """discord.py wraps command exceptions in CommandInvokeError."""
    wrapped = commands.CommandInvokeError(missing_arg())
    await errors.handle(ctx, wrapped)
    assert "!volume <vol>" in sent_text(ctx)


# -- resilience --------------------------------------------------------

async def test_a_send_failure_does_not_escalate(ctx):
    """No permission to post is not a reason to blow up the error handler."""
    ctx.send.side_effect = discord.Forbidden(MagicMock(status=403), "no perms")
    await errors.handle(ctx, missing_arg())      # must not raise


async def test_handling_survives_a_command_with_no_context(ctx):
    ctx.command = None
    await errors.handle(ctx, missing_arg())      # must not raise


# -- against the real commands, not mocks ------------------------------

@pytest.mark.parametrize("name,expected", [
    ("play",   "!play <query>"),
    ("volume", "!volume <vol>"),
    ("remove", "!remove <index>"),
    ("move",   "!move <from_pos> <to_pos>"),
    ("queue",  "!queue [page=1]"),
    ("loop",   "!loop [mode=track]"),
    ("skip",   "!skip"),
])
async def test_usage_matches_the_real_command_definitions(ctx, name, expected):
    """
    Renaming a parameter must not silently produce a wrong usage hint, so this
    reads the signature off the actual Command objects.
    """
    from cogs.music import Music

    command = getattr(Music, name)
    ctx.command = MagicMock()
    ctx.command.qualified_name = command.qualified_name
    ctx.command.signature = command.signature
    ctx.command.has_error_handler.return_value = False

    await errors.handle(ctx, missing_arg())
    assert expected in sent_text(ctx)


def test_no_music_command_keeps_a_private_error_handler():
    """
    A leftover per-command handler would suppress the central one and bring the
    silent-failure bug back for that command.
    """
    from cogs.music import Music

    with_handlers = [
        c.qualified_name for c in Music.__cog_commands__ if c.has_error_handler()
    ]
    assert with_handlers == []
