"""
`!play` is the expensive path — every call starts a yt-dlp process on a small
host — so its limits must hold, not just exist.

The command tests call ``.callback()``, which skips discord.py's limiter. These
run the two steps ``Command.prepare`` runs before every invocation: the
max-concurrency gate and the cooldown bucket.
"""

import datetime
from unittest.mock import MagicMock

import pytest
from discord.ext import commands

from cogs.music import Music

PLAY = Music.play
NOW = datetime.datetime(2026, 10, 10, 12, 0, 0)


def ctx_for(user_id: int, seconds: float = 0.0) -> MagicMock:
    """A context from ``user_id``, sent ``seconds`` after NOW."""
    ctx = MagicMock()
    ctx.author.id = user_id
    ctx.message.edited_at = None
    ctx.message.created_at = NOW + datetime.timedelta(seconds=seconds)
    return ctx


@pytest.fixture(autouse=True)
def fresh_limits():
    """The buckets live on the command object, shared by every test.

    CooldownMapping.copy() shares its cache with the original, so the cache is
    cleared instead. Both guards tolerate a missing decorator, so its absence
    fails the test itself rather than the setup.
    """
    PLAY._buckets._cache.clear()
    if PLAY._max_concurrency is not None:
        PLAY._max_concurrency = PLAY._max_concurrency.copy()


def test_a_fourth_play_within_five_seconds_is_refused():
    for second in (0, 1, 2):
        PLAY._prepare_cooldowns(ctx_for(1, second))
    with pytest.raises(commands.CommandOnCooldown):
        PLAY._prepare_cooldowns(ctx_for(1, 3))


def test_the_cooldown_is_per_user():
    for second in (0, 1, 2):
        PLAY._prepare_cooldowns(ctx_for(1, second))
    PLAY._prepare_cooldowns(ctx_for(2, 3))     # someone else is unaffected


def test_the_cooldown_expires():
    for second in (0, 1, 2):
        PLAY._prepare_cooldowns(ctx_for(1, second))
    PLAY._prepare_cooldowns(ctx_for(1, 6))


async def test_one_play_at_a_time_per_user():
    limit = PLAY._max_concurrency
    assert limit is not None, "play has no max_concurrency"
    first = ctx_for(1)
    await limit.acquire(first)
    with pytest.raises(commands.MaxConcurrencyReached):
        await limit.acquire(ctx_for(1, 1))
    await limit.acquire(ctx_for(2, 1))   # another user may play
    await limit.release(first)
    await limit.acquire(ctx_for(1, 2))   # free again once done
