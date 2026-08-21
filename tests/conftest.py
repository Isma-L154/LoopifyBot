"""
Shared fixtures.

The suite never opens a Discord gateway connection and never touches the
network. Everything here builds just enough of a fake bot/guild for the real
:class:`~utils.player.MusicPlayer` logic to run in-process.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Make the project importable without installing it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.player import MusicPlayer, players  # noqa: E402


def make_track(title: str = "Track", **overrides) -> dict:
    """A track dict shaped like the one ``services.media._build_track`` returns."""
    track = {
        "title": title,
        "url": f"https://example.invalid/{title.replace(' ', '_')}",
        "stream": None,
        "duration": 180,
        "thumbnail": None,
        "uploader": "Uploader",
        "source": "test",
        "query": "",
    }
    track.update(overrides)
    return track


@pytest.fixture
def track_factory():
    return make_track


@pytest.fixture
def fake_bot():
    """
    A stand-in for the bot.

    ``create_task`` deliberately closes the coroutine instead of scheduling it:
    unit tests drive ``_advance`` and the queue directly, and letting the real
    ``_player_loop`` run would try to touch voice state.
    """
    bot = MagicMock()

    def _create_task(coro):
        coro.close()
        task = MagicMock()
        task.done.return_value = True
        return task

    bot.loop.create_task.side_effect = _create_task
    return bot


@pytest.fixture
def fake_guild():
    guild = MagicMock()
    guild.id = 1234567890
    guild.voice_client = None      # not connected unless a test says otherwise
    return guild


@pytest.fixture
def player(fake_bot, fake_guild):
    """A MusicPlayer whose background loop is never started."""
    p = MusicPlayer(fake_bot, fake_guild, MagicMock())
    yield p
    # Keep the module-level singleton clean between tests.
    players.discard(fake_guild.id)
