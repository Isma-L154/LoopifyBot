"""
``MusicPlayer._advance`` — the state machine that decides what plays next.

Every combination of loop mode, skip and replay is covered here, because this
is the one place where a wrong branch produces the classic music-bot bugs:
double-skips, tracks repeating forever, or history silently losing entries.

``_advance`` returns ``(track, silent)``; ``silent`` suppresses the "Now
Playing" announcement, and ``(None, _)`` means "disconnect".
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import make_track


async def test_first_track_comes_off_the_queue_and_is_announced(player):
    player.add(make_track("A"))
    track, silent = await player._advance()
    assert track["title"] == "A"
    assert silent is False
    assert player.current["title"] == "A"


async def test_normal_advance_archives_the_previous_track(player):
    player.current = make_track("A")
    player.add(make_track("B"))
    track, silent = await player._advance()
    assert track["title"] == "B"
    assert silent is False
    assert [t["title"] for t in player.history] == ["A"]


# ── loop modes ────────────────────────────────────────────────────────

async def test_loop_track_repeats_silently(player):
    player.current = make_track("A")
    player.loop_mode = "track"
    player.add(make_track("B"))          # must NOT be consumed
    track, silent = await player._advance()
    assert track["title"] == "A"
    assert silent is True, "a loop repeat should not re-announce the track"
    assert [t["title"] for t in player.to_list()] == ["B"]


async def test_loop_queue_sends_the_finished_track_to_the_back(player):
    player.current = make_track("A")
    player.loop_mode = "queue"
    player.add(make_track("B"))
    track, _ = await player._advance()
    assert track["title"] == "B"
    assert [t["title"] for t in player.to_list()] == ["A"]


async def test_loop_off_drops_the_finished_track(player):
    player.current = make_track("A")
    player.loop_mode = "off"
    player.add(make_track("B"))
    track, _ = await player._advance()
    assert track["title"] == "B"
    assert player.is_empty


# ── skip beats loop mode ──────────────────────────────────────────────

async def test_skip_overrides_loop_track(player):
    """A user pressing skip must escape a single-track loop."""
    player.current = make_track("A")
    player.loop_mode = "track"
    player._skip = True
    player.add(make_track("B"))
    track, silent = await player._advance()
    assert track["title"] == "B"
    assert silent is False
    assert player._skip is False, "the skip flag must be consumed exactly once"


async def test_skip_overrides_loop_queue(player):
    player.current = make_track("A")
    player.loop_mode = "queue"
    player._skip = True
    player.add(make_track("B"))
    track, _ = await player._advance()
    assert track["title"] == "B"
    assert player.is_empty, "a skipped track must not be requeued"


async def test_skip_does_not_archive_the_skipped_track(player):
    player.current = make_track("A")
    player._skip = True
    player.add(make_track("B"))
    await player._advance()
    assert player.history == []


# ── replay (effect change) ────────────────────────────────────────────

async def test_replay_returns_the_same_track_silently(player):
    player.current = make_track("A")
    player._replay = True
    player.add(make_track("B"))
    track, silent = await player._advance()
    assert track["title"] == "A"
    assert silent is True
    assert [t["title"] for t in player.to_list()] == ["B"], "replay must not consume the queue"
    assert player.history == [], "replay must not archive anything"


# ── autoplay ──────────────────────────────────────────────────────────

async def test_autoplay_fills_an_empty_queue(player):
    player.current = make_track("A")
    player.autoplay = True
    suggestion = make_track("Related")
    with patch("services.media.related", new=AsyncMock(return_value=suggestion)):
        track, silent = await player._advance()
    assert track["title"] == "Related"
    assert silent is False


async def test_autoplay_that_finds_nothing_falls_through_to_waiting(player):
    player.current = make_track("A")
    player.autoplay = True
    with patch("services.media.related", new=AsyncMock(return_value=None)), \
         patch("utils.player.INACTIVITY_TIMEOUT", 0.01):
        track, _ = await player._advance()
    assert track is None, "no suggestion and no queue means disconnect"


async def test_autoplay_is_not_consulted_when_the_queue_has_tracks(player):
    player.current = make_track("A")
    player.autoplay = True
    player.add(make_track("B"))
    related = AsyncMock(return_value=make_track("Related"))
    with patch("services.media.related", new=related):
        track, _ = await player._advance()
    assert track["title"] == "B"
    related.assert_not_awaited()


# ── idle timeout ──────────────────────────────────────────────────────

async def test_empty_queue_times_out_and_signals_disconnect(player):
    with patch("utils.player.INACTIVITY_TIMEOUT", 0.01):
        track, _ = await player._advance()
    assert track is None
    assert player.current is None


async def test_a_track_added_while_idle_wakes_the_player(player):
    """The 5-minute idle wait must be interrupted the moment something is queued."""
    async def enqueue_shortly():
        await asyncio.sleep(0.01)
        player.add(make_track("Late"))

    with patch("utils.player.INACTIVITY_TIMEOUT", 5):
        advance = asyncio.create_task(player._advance())
        await enqueue_shortly()
        track, silent = await asyncio.wait_for(advance, timeout=2)

    assert track["title"] == "Late"
    assert silent is False
