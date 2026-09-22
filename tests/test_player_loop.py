"""
The playback loop itself, run for real against a fake voice client.

The other player tests drive ``_advance`` and the controls directly. These run
``_player_loop`` end to end, with yt-dlp, FFmpeg and Discord replaced, to check
what happens *between* tracks — which is where a failure used to take the whole
player down with it.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from services import media
from tests.conftest import make_track


def _run_inline(_executor, fn, *args):
    """``run_in_executor`` without the thread: a future that is already done."""
    future = asyncio.get_running_loop().create_future()
    try:
        future.set_result(fn(*args))
    except Exception as e:
        future.set_exception(e)
    return future


@pytest.fixture
def looping(player, fake_bot, fake_guild, monkeypatch):
    """A player whose loop can run: voice connected, announcer and media faked."""
    fake_bot.wait_until_ready = AsyncMock()
    fake_bot.loop.run_in_executor.side_effect = _run_inline
    fake_bot.loop.call_soon_threadsafe.side_effect = lambda fn: fn()

    vc = MagicMock()
    vc.is_connected.return_value = True
    vc.disconnect = AsyncMock()
    played: list[dict] = []

    def play(_source, *, after):
        # One track is enough to prove the loop carried on: stop here.
        played.append(player.current)
        player.destroy()
        after(None)

    vc.play.side_effect = play
    fake_guild.voice_client = vc

    player.announcer = MagicMock(now_playing=AsyncMock(), load_failed=AsyncMock(),
                                 idle_disconnect=AsyncMock())
    monkeypatch.setattr(media, "make_pipe_source", lambda *a, **k: MagicMock())
    monkeypatch.setattr(media, "prime_source", lambda source: True)
    player.played = played
    return player


async def test_a_track_yt_dlp_cannot_start_is_announced_and_skipped(looping, monkeypatch):
    broken, fine = make_track("Broken"), make_track("Fine")

    def spawn(track):
        if track is broken:
            raise OSError("fork failed: resource temporarily unavailable")
        return MagicMock()

    monkeypatch.setattr(media, "spawn_stream", spawn)
    looping.add_many([broken, fine])

    await asyncio.wait_for(looping._player_loop(), timeout=5)

    looping.announcer.load_failed.assert_awaited_once_with(broken)
    assert broken["error"] == "unavailable"
    assert looping.played == [fine], "the queue must carry on after the failure"


async def test_a_failed_respawn_during_an_effect_change_does_not_end_the_session(
        looping, monkeypatch):
    """
    An effect change replays the current track. If that respawn cannot start,
    the replay flag must not survive: _advance would hand back a track that is
    gone, and the loop would read that as an empty queue and disconnect.
    """
    current, following = make_track("Current"), make_track("Following")

    def spawn(track):
        if track is current:
            raise OSError("fork failed")
        return MagicMock()

    monkeypatch.setattr(media, "spawn_stream", spawn)
    looping.current = current
    looping._replay = True
    looping._resume_at = 42.0
    looping.add(following)

    await asyncio.wait_for(looping._player_loop(), timeout=5)

    assert looping.played == [following]
    looping.announcer.idle_disconnect.assert_not_awaited()
