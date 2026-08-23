"""
``services.media.BufferedAudioSource`` - read-ahead in front of FFmpeg.

discord.py's player paces itself against a wall clock and, if a read blocks,
catches up by sending frames with no delay - audible as the track briefly
speeding up. These tests drive sources that stall on purpose and assert the
buffer absorbs it.
"""

import threading
import time

import discord
import pytest

from services.media import BufferedAudioSource, FRAME_SIZE


class FakeSource(discord.AudioSource):
    """A source that yields ``frames`` frames, optionally stalling partway."""

    def __init__(self, frames: int, stall_at: int = -1, stall_for: float = 0.0):
        self.frames = frames
        self.stall_at = stall_at
        self.stall_for = stall_for
        self.served = 0
        self.cleaned = False

    def read(self) -> bytes:
        if self.served == self.stall_at:
            time.sleep(self.stall_for)
        if self.served >= self.frames:
            return b""
        self.served += 1
        return bytes([self.served % 256]) * FRAME_SIZE

    def cleanup(self) -> None:
        self.cleaned = True


def drain(source) -> list:
    frames = []
    while True:
        chunk = source.read()
        if not chunk:
            return frames
        frames.append(chunk)


# -- the bug this exists to prevent ------------------------------------

def test_a_stalling_source_still_delivers_frames_on_time():
    """
    The player must not have to wait on a stalled source. With enough
    read-ahead, a one-second gap is invisible to the caller.
    """
    inner = FakeSource(frames=200, stall_at=5, stall_for=1.0)
    source = BufferedAudioSource(inner, seconds=4.0)
    try:
        # Give the background thread a moment to fill past the stall point.
        deadline = time.monotonic() + 5
        while source.buffered_frames < 50 and time.monotonic() < deadline:
            time.sleep(0.01)

        started = time.monotonic()
        for _ in range(40):
            assert source.read(), "the buffer ran dry during a stall"
        elapsed = time.monotonic() - started

        assert elapsed < 0.2, f"reads blocked for {elapsed:.2f}s; buffer did not absorb the stall"
    finally:
        source.cleanup()


def test_every_frame_arrives_in_order_and_none_are_dropped():
    inner = FakeSource(frames=120)
    source = BufferedAudioSource(inner, seconds=1.0)
    try:
        frames = drain(source)
    finally:
        source.cleanup()

    assert len(frames) == 120, "frames were dropped"
    assert frames[0][0] == 1
    assert frames[-1][0] == 120 % 256
    assert all(len(f) == FRAME_SIZE for f in frames)


def test_the_end_of_the_source_ends_playback():
    inner = FakeSource(frames=3)
    source = BufferedAudioSource(inner, seconds=1.0)
    try:
        assert len(drain(source)) == 3
        assert source.read() == b"", "must keep reporting end, not block"
    finally:
        source.cleanup()


def test_an_empty_source_ends_immediately():
    source = BufferedAudioSource(FakeSource(frames=0), seconds=1.0)
    try:
        assert source.read() == b""
    finally:
        source.cleanup()


# -- bounded memory ----------------------------------------------------

def test_the_buffer_is_bounded():
    """
    An unbounded queue in front of a fast source would pull an entire track
    into RAM, undoing the backpressure the yt-dlp pipe exists to provide.
    """
    inner = FakeSource(frames=100_000)
    source = BufferedAudioSource(inner, seconds=2.0)
    try:
        time.sleep(0.5)                     # let it fill
        cap = source.capacity_frames
        assert cap == pytest.approx(100, abs=1), "2s at 20ms/frame is ~100 frames"
        assert source.buffered_frames <= cap
        assert inner.served <= cap + 2, "the producer ran past the buffer cap"
    finally:
        source.cleanup()


def test_seconds_translates_to_frames():
    source = BufferedAudioSource(FakeSource(frames=1), seconds=5.0)
    try:
        assert source.capacity_frames == 250      # 5s / 20ms
    finally:
        source.cleanup()


# -- teardown ----------------------------------------------------------

def test_cleanup_stops_the_thread_and_the_inner_source():
    inner = FakeSource(frames=100_000)
    source = BufferedAudioSource(inner, seconds=1.0)
    time.sleep(0.2)
    source.cleanup()

    deadline = time.monotonic() + 3
    while source._thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert not source._thread.is_alive(), "the reader thread outlived cleanup()"
    assert inner.cleaned, "the wrapped source was not cleaned up"


def test_cleanup_is_idempotent():
    source = BufferedAudioSource(FakeSource(frames=10), seconds=1.0)
    source.cleanup()
    source.cleanup()
    source.cleanup()


def test_reading_after_cleanup_reports_end_rather_than_blocking():
    source = BufferedAudioSource(FakeSource(frames=100_000), seconds=1.0)
    source.cleanup()
    started = time.monotonic()
    assert source.read() == b""
    assert time.monotonic() - started < 1.0, "read() blocked after cleanup"


def test_no_reader_threads_are_left_behind():
    before = threading.active_count()
    for _ in range(5):
        s = BufferedAudioSource(FakeSource(frames=1000), seconds=1.0)
        time.sleep(0.05)
        s.cleanup()

    deadline = time.monotonic() + 5
    while threading.active_count() > before and time.monotonic() < deadline:
        time.sleep(0.05)
    assert threading.active_count() <= before


# -- a source that raises ----------------------------------------------

def test_a_source_that_raises_ends_playback_instead_of_hanging():
    class Exploding(discord.AudioSource):
        def read(self):
            raise OSError("pipe died")

        def cleanup(self):
            pass

    source = BufferedAudioSource(Exploding(), seconds=1.0)
    try:
        started = time.monotonic()
        assert source.read() == b""
        assert time.monotonic() - started < 2.0
    finally:
        source.cleanup()


def test_it_reports_opus_the_same_as_the_wrapped_source():
    """discord.py asks this to decide whether to encode."""
    source = BufferedAudioSource(FakeSource(frames=1), seconds=1.0)
    try:
        assert source.is_opus() is False
    finally:
        source.cleanup()


# -- how make_pipe_source composes the chain ---------------------------

def test_the_volume_transformer_stays_outermost(tmp_path):
    """
    MusicPlayer.set_volume does `isinstance(vc.source, PCMVolumeTransformer)`.
    Wrapping the buffer around the outside would silently break volume control.
    """
    from services import media

    path = tmp_path / "silence.raw"
    path.write_bytes(b"\x00" * 4096)
    with open(path, "rb") as handle:
        source = media.make_pipe_source(handle, volume=0.7)
        try:
            assert isinstance(source, discord.PCMVolumeTransformer)
            assert source.volume == pytest.approx(0.7)
            assert isinstance(source.original, BufferedAudioSource)
        finally:
            source.cleanup()


def test_cleanup_propagates_through_the_whole_chain(tmp_path):
    from services import media

    path = tmp_path / "silence.raw"
    path.write_bytes(b"\x00" * 4096)
    with open(path, "rb") as handle:
        source = media.make_pipe_source(handle)
        buffered = source.original
        source.cleanup()

    deadline = time.monotonic() + 3
    while buffered._thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not buffered._thread.is_alive(), "cleanup did not reach the read-ahead thread"


def test_volume_can_still_be_changed_mid_playback(tmp_path):
    from services import media

    path = tmp_path / "silence.raw"
    path.write_bytes(b"\x00" * 4096)
    with open(path, "rb") as handle:
        source = media.make_pipe_source(handle, volume=0.5)
        try:
            source.volume = 0.9
            assert source.volume == pytest.approx(0.9)
        finally:
            source.cleanup()
