"""
Voice-state guards in ``utils.checks``.

``commands.check`` exposes the wrapped predicate as ``.predicate``, so each
guard can be exercised directly. Every guard must both return the right verdict
*and* tell the user why it refused - a silent False is a bug.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from utils.checks import user_in_voice, bot_in_voice, same_voice_channel


@pytest.fixture
def ctx():
    c = MagicMock()
    c.send = AsyncMock()
    c.author.voice = None
    c.voice_client = None
    return c


def in_channel(channel):
    """A voice state for someone sitting in ``channel``."""
    state = MagicMock()
    state.channel = channel
    return state


def sent_text(ctx) -> str:
    embed = ctx.send.await_args.kwargs.get("embed") or ctx.send.await_args.args[0]
    return embed.description or ""


# -- user_in_voice -----------------------------------------------------

async def test_user_in_voice_passes_when_connected(ctx):
    ctx.author.voice = in_channel(MagicMock())
    assert await user_in_voice().predicate(ctx) is True
    ctx.send.assert_not_awaited()


async def test_user_in_voice_refuses_and_explains_when_not_connected(ctx):
    assert await user_in_voice().predicate(ctx) is False
    assert "must be in a voice channel" in sent_text(ctx)


async def test_user_in_voice_refuses_a_stale_voice_state(ctx):
    """A voice state with no channel means the user just left."""
    ctx.author.voice = in_channel(None)
    assert await user_in_voice().predicate(ctx) is False


# -- bot_in_voice ------------------------------------------------------

async def test_bot_in_voice_passes_when_the_bot_is_connected(ctx):
    ctx.voice_client = MagicMock()
    assert await bot_in_voice().predicate(ctx) is True


async def test_bot_in_voice_refuses_and_explains_when_the_bot_is_not(ctx):
    assert await bot_in_voice().predicate(ctx) is False
    assert "not connected" in sent_text(ctx)


# -- same_voice_channel ------------------------------------------------

async def test_same_channel_passes(ctx):
    channel = MagicMock()
    ctx.author.voice = in_channel(channel)
    ctx.voice_client = MagicMock(channel=channel)
    assert await same_voice_channel().predicate(ctx) is True
    ctx.send.assert_not_awaited()


async def test_different_channel_is_refused(ctx):
    ctx.author.voice = in_channel(MagicMock())
    ctx.voice_client = MagicMock(channel=MagicMock())
    assert await same_voice_channel().predicate(ctx) is False
    assert "same voice channel" in sent_text(ctx)


async def test_same_channel_requires_the_user_to_be_in_voice(ctx):
    ctx.voice_client = MagicMock(channel=MagicMock())
    assert await same_voice_channel().predicate(ctx) is False
    assert "must be in a voice channel" in sent_text(ctx)


async def test_same_channel_passes_when_the_bot_is_not_connected_yet(ctx):
    """
    With no voice client there is no channel to mismatch, so the guard defers to
    the command, which reports "nothing is playing" itself.
    """
    ctx.author.voice = in_channel(MagicMock())
    ctx.voice_client = None
    assert await same_voice_channel().predicate(ctx) is True
