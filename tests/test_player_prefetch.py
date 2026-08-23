"""
Fetching the next track while the current one plays.

The value is obvious - a queued track starts instantly instead of waiting 3-8s
on yt-dlp. The risk is not: a prefetched stream is a live yt-dlp process, and
one that is fetched and then never claimed is exactly the leak that produced a
19-day zombie before. Most of these tests are about the discard paths.
"""

import asyncio
import time

import pytest

from utils import player as player_module
from utils.player import PREFETCH_LEAD_SECONDS
from tests.conftest import make_track


class FakeStream:
    """Stands in for media.AudioStream, recording whether it was closed."""

    def __init__(self, label: str = "stream"):
        self.label = label
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def spawned(monkeypatch):
    """Replaces media.spawn_stream, recording which tracks were fetched."""
    calls = []

    def _spawn(track):
        calls.append(track)
        return FakeStream(track["title"])

    monkeypatch.setattr(player_module.media, "spawn_stream", _spawn)
    return calls


@pytest.fixture
async def real_loop(player):
    """
    Give the player a bot whose loop is the one actually running the test.

    The default fixture closes coroutines instead of scheduling them, which is
    right for _advance but useless here: prefetching *is* a scheduled task. The
    fixture must be async so `get_running_loop` sees the test's loop rather
    than whatever `get_event_loop` would invent.
    """
    player.bot.loop = asyncio.get_running_loop()
    yield player
    # Never let a leaked task bleed into the next test.
    if player._prefetch_task is not None and not player._prefetch_task.done():
        player._prefetch_task.cancel()
        try:
            await player._prefetch_task
        except (asyncio.CancelledError, Exception):
            pass


async def settle():
    """Let scheduled prefetch tasks run."""
    for _ in range(6):
        await asyncio.sleep(0)


async def wait_until(condition, timeout: float = 2.0) -> bool:
    """
    Wait for a condition that a worker thread will make true.

    Streams are closed through run_in_executor, so yielding to the event loop
    alone is not enough — the thread pool has to actually get there.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        await asyncio.sleep(0.01)
    return False


# -- when it fires -----------------------------------------------------

def test_a_long_track_waits_before_prefetching(player):
    """
    Starting at the top of a long track leaves yt-dlp blocked on a full pipe
    for minutes, and YouTube drops connections that idle that long.
    """
    track = make_track("Long", duration=300)
    player._start_ts = 0
    delay = player._prefetch_delay(track)
    assert delay == pytest.approx(300 - PREFETCH_LEAD_SECONDS, abs=1)


def test_a_track_already_near_its_end_prefetches_immediately(player, clock_at_180):
    track = make_track("Long", duration=200)
    assert player._prefetch_delay(track) is None


def test_a_short_track_prefetches_immediately(player):
    track = make_track("Short", duration=10)
    player._start_ts = 0
    assert player._prefetch_delay(track) is None


def test_a_live_stream_prefetches_immediately(player):
    """No duration means no end to count backwards from."""
    assert player._prefetch_delay(make_track("Live", duration=None)) is None


# -- what it fetches ---------------------------------------------------

async def test_it_fetches_the_front_of_the_queue(real_loop, spawned):
    nxt = make_track("Next")
    real_loop.add(nxt)
    real_loop.add(make_track("After"))

    real_loop._start_prefetch()
    await settle()

    assert spawned == [nxt], "prefetched the wrong track"
    assert real_loop._prefetch[0] is nxt


async def test_an_empty_queue_fetches_nothing(real_loop, spawned):
    real_loop._start_prefetch()
    await settle()
    assert spawned == []
    assert real_loop._prefetch is None


async def test_track_loop_fetches_nothing(real_loop, spawned):
    """The next track is the current one — there is nothing to fetch."""
    real_loop.loop_mode = "track"
    real_loop.add(make_track("Next"))

    real_loop._start_prefetch()
    await settle()

    assert spawned == []


async def test_it_does_not_fetch_twice(real_loop, spawned):
    real_loop.add(make_track("Next"))
    real_loop._start_prefetch()
    await settle()
    real_loop._start_prefetch()
    await settle()
    assert len(spawned) == 1


# -- claiming it -------------------------------------------------------

async def test_the_matching_track_gets_the_prefetched_stream(real_loop, spawned):
    nxt = make_track("Next")
    real_loop.add(nxt)
    real_loop._start_prefetch()
    await settle()

    stream = real_loop._take_prefetch(nxt)

    assert stream is not None
    assert stream.closed is False
    assert real_loop._prefetch is None, "the slot must be cleared after claiming"


async def test_claiming_when_nothing_was_prefetched(real_loop):
    assert real_loop._take_prefetch(make_track("Any")) is None


# -- the discard paths, where a leak would hide ------------------------

async def test_a_stream_for_a_different_track_is_closed_not_reused(real_loop, spawned):
    """
    Skip, remove, shuffle and previous all change what plays next. Handing over
    a stream for the wrong track would play the wrong audio; keeping it would
    leak a yt-dlp process.
    """
    prefetched = make_track("Was next")
    real_loop.add(prefetched)
    real_loop._start_prefetch()
    await settle()
    stream = real_loop._prefetch[1]

    claimed = real_loop._take_prefetch(make_track("Actually playing"))

    assert claimed is None, "must not reuse a stream fetched for another track"
    assert await wait_until(lambda: stream.closed), "the unused stream leaked"


async def test_matching_is_by_identity_not_by_url(real_loop, spawned):
    """
    Two queue entries for the same song are different tracks: one has already
    been requested, the other has not. Matching on URL would hand the same
    stream to both.
    """
    first = make_track("Same song")
    second = make_track("Same song")
    assert first["url"] == second["url"]

    real_loop.add(first)
    real_loop._start_prefetch()
    await settle()
    stream = real_loop._prefetch[1]

    assert real_loop._take_prefetch(second) is None
    assert await wait_until(lambda: stream.closed)


async def test_a_prefetch_arriving_after_destroy_is_closed(real_loop, spawned, monkeypatch):
    """The player can be destroyed while a fetch is still in flight."""
    created = []

    def _slow_spawn(track):
        stream = FakeStream(track["title"])
        created.append(stream)
        return stream

    monkeypatch.setattr(player_module.media, "spawn_stream", _slow_spawn)
    real_loop.add(make_track("Next"))
    real_loop._start_prefetch()

    real_loop._destroyed = True
    await settle()

    assert real_loop._prefetch is None
    assert created, "nothing was fetched"
    assert await wait_until(lambda: created[0].closed), "a stream fetched after destroy leaked"


async def test_destroy_closes_a_ready_prefetch(real_loop, spawned, fake_guild):
    real_loop.add(make_track("Next"))
    real_loop._start_prefetch()
    await settle()
    stream = real_loop._prefetch[1]

    real_loop.destroy()

    assert await wait_until(lambda: stream.closed), "destroy leaked the prefetched stream"
    assert real_loop._prefetch is None


async def test_destroy_cancels_an_in_flight_prefetch(real_loop, spawned):
    real_loop.add(make_track("Next"))
    real_loop._start_prefetch()
    task = real_loop._prefetch_task

    real_loop.destroy()
    await settle()

    assert task.cancelled() or task.done()
    assert real_loop._prefetch_task is None


# -- waiting ------------------------------------------------------------

async def test_a_track_ending_early_skips_the_prefetch_wait(real_loop, spawned):
    """
    Skip, stop and effect changes all end a track before its duration. The wait
    must return promptly rather than sitting on its timeout.
    """
    track = make_track("Long", duration=600)
    real_loop._start_ts = 0
    real_loop.add(make_track("Next"))

    real_loop._next.set()
    await asyncio.wait_for(real_loop._wait_for_end(track), timeout=1)


async def test_the_wait_prefetches_then_keeps_waiting(real_loop, spawned):
    """After the lead elapses it must fetch and then still wait for the end."""
    track = make_track("Short", duration=1)      # lead already passed
    real_loop._start_ts = 0
    nxt = make_track("Next")
    real_loop.add(nxt)

    waiting = asyncio.create_task(real_loop._wait_for_end(track))
    await settle()

    assert spawned == [nxt], "did not prefetch"
    assert not waiting.done(), "returned before the track ended"

    real_loop._next.set()
    await asyncio.wait_for(waiting, timeout=1)
