"""
Playback position tracking, and resuming in place across an effect change.

An FFmpeg filter chain is fixed for the life of the process, so changing an
effect means respawning the stream. These cover the clock that makes the
respawn land where the listener actually was, rather than back at 0:00.

Time is driven by a fake clock so the tests are exact rather than timing-
dependent.
"""

from unittest.mock import MagicMock

import pytest

import discord
from services import media
from utils import player as player_module
from utils.player import SEEK_TAIL_MARGIN
from tests.conftest import make_track


class FakeClock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(player_module.time, "monotonic", c)
    return c


@pytest.fixture
def playing(player, fake_guild, clock):
    """A player mid-track, three minutes into a five-minute song."""
    vc = MagicMock()
    vc.is_playing.return_value = True
    vc.is_paused.return_value = False
    fake_guild.voice_client = vc

    player.current = make_track("Song", duration=300)
    player._start_ts = clock.now
    clock.advance(180)
    return player


# -- elapsed -----------------------------------------------------------

def test_elapsed_is_zero_before_anything_plays(player, clock):
    assert player.elapsed == 0.0


def test_elapsed_tracks_wall_time(playing, clock):
    assert playing.elapsed == pytest.approx(180)
    clock.advance(45)
    assert playing.elapsed == pytest.approx(225)


def test_elapsed_excludes_time_spent_paused(playing, clock):
    """Otherwise a track paused for a coffee break resumes far too late."""
    playing.pause()
    clock.advance(600)                    # ten minutes paused
    playing.voice.is_playing.return_value = False
    playing.voice.is_paused.return_value = True
    playing.resume()

    assert playing.elapsed == pytest.approx(180), "paused time must not count"

    clock.advance(20)
    assert playing.elapsed == pytest.approx(200)


def test_elapsed_is_frozen_while_still_paused(playing, clock):
    playing.pause()
    clock.advance(60)
    assert playing.elapsed == pytest.approx(180)
    clock.advance(60)
    assert playing.elapsed == pytest.approx(180)


def test_repeated_pauses_accumulate(playing, clock):
    for _ in range(3):
        playing.voice.is_playing.return_value = True
        playing.voice.is_paused.return_value = False
        playing.pause()
        clock.advance(10)
        playing.voice.is_playing.return_value = False
        playing.voice.is_paused.return_value = True
        playing.resume()
        clock.advance(5)

    # 180 before, then 3 x 5s of actual playback; the 3 x 10s paused is excluded.
    assert playing.elapsed == pytest.approx(195)


# -- pause / resume guards ---------------------------------------------

def test_pause_refuses_when_nothing_is_playing(player, fake_guild):
    fake_guild.voice_client = None
    assert player.pause() is False


def test_resume_refuses_when_nothing_is_paused(playing):
    assert playing.resume() is False, "already playing"


def test_double_pause_does_not_lose_the_clock(playing, clock):
    playing.pause()
    clock.advance(30)
    playing.pause()                       # a second !pause while paused
    clock.advance(30)
    playing.voice.is_playing.return_value = False
    playing.voice.is_paused.return_value = True
    playing.resume()
    assert playing.elapsed == pytest.approx(180)


# -- where a respawn should pick up ------------------------------------

def test_seek_target_is_the_current_position(playing):
    assert playing._seek_target() == pytest.approx(180)


def test_live_streams_are_never_seeked(playing):
    """A live stream reports no duration and has no position to seek to."""
    playing.current["duration"] = None
    assert playing._seek_target() == 0.0


def test_a_track_with_unknown_duration_is_never_seeked(playing):
    del playing.current["duration"]
    assert playing._seek_target() == 0.0


def test_the_last_seconds_of_a_track_are_not_seeked_into(playing, clock):
    """Resuming past the end would produce silence, not audio."""
    clock.advance(300 - 180 - SEEK_TAIL_MARGIN)      # right at the margin
    assert playing._seek_target() == 0.0


def test_a_position_past_the_end_is_not_seeked_to(playing, clock):
    clock.advance(500)
    assert playing._seek_target() == 0.0


def test_the_very_start_is_not_seeked_to(player, fake_guild, clock):
    """Seeking to 0 is a no-op that only costs a slower start."""
    vc = MagicMock()
    vc.is_playing.return_value = True
    fake_guild.voice_client = vc
    player.current = make_track("Song", duration=300)
    player._start_ts = clock.now
    assert player._seek_target() == 0.0


# -- apply_effect ------------------------------------------------------

def test_applying_an_effect_captures_the_position(playing):
    assert playing.apply_effect("bassboost", "equalizer=f=54:g=10") is True
    assert playing._resume_at == pytest.approx(180)
    assert playing._replay is True
    assert playing.effect_name == "bassboost"
    playing.voice.stop.assert_called_once()


def test_applying_an_effect_to_a_live_stream_does_not_seek(playing):
    playing.current["duration"] = None
    assert playing.apply_effect("bass", "equalizer=f=54:g=5") is True
    assert playing._resume_at == 0.0, "a live stream must restart, not seek"


def test_clearing_an_effect_also_resumes_in_place(playing):
    assert playing.apply_effect(None, "") is True
    assert playing._resume_at == pytest.approx(180)
    assert playing.effect_filter == ""


def test_apply_effect_refuses_when_nothing_is_playing(player, fake_guild):
    fake_guild.voice_client = None
    assert player.apply_effect("bass", "x") is False


def test_apply_effect_works_while_paused(playing, clock):
    playing.pause()
    playing.voice.is_playing.return_value = False
    playing.voice.is_paused.return_value = True
    clock.advance(90)
    assert playing.apply_effect("echo", "aecho=0.8:0.88:60:0.4") is True
    assert playing._resume_at == pytest.approx(180), "paused time must not shift the resume point"


# -- the seek reaching FFmpeg ------------------------------------------

class FakeFFmpeg(discord.AudioSource):
    """Records how discord.FFmpegPCMAudio was configured."""
    last = None

    def __init__(self, source, **kwargs):
        FakeFFmpeg.last = kwargs

    def read(self):
        return b""


@pytest.fixture
def captured_ffmpeg(monkeypatch):
    FakeFFmpeg.last = None
    monkeypatch.setattr(discord, "FFmpegPCMAudio", FakeFFmpeg)
    return FakeFFmpeg


def test_a_seek_becomes_an_ffmpeg_input_option(captured_ffmpeg):
    """
    -ss must be an *input* option. As an output option FFmpeg decodes and
    discards every frame instead of skipping packets.
    """
    media.make_pipe_source(MagicMock(), seek_seconds=182.5)
    assert captured_ffmpeg.last["before_options"] == "-ss 182.500"


@pytest.mark.parametrize("seek", [0, 0.0, -1])
def test_no_seek_means_no_input_option(captured_ffmpeg, seek):
    media.make_pipe_source(MagicMock(), seek_seconds=seek)
    assert captured_ffmpeg.last["before_options"] is None


def test_the_filter_and_the_seek_coexist(captured_ffmpeg):
    media.make_pipe_source(
        MagicMock(), ffmpeg_filter="equalizer=f=54:g=10", seek_seconds=60,
    )
    assert captured_ffmpeg.last["before_options"] == "-ss 60.000"
    assert captured_ffmpeg.last["options"] == "-vn -af equalizer=f=54:g=10"


def test_no_filter_still_strips_video(captured_ffmpeg):
    media.make_pipe_source(MagicMock())
    assert captured_ffmpeg.last["options"] == "-vn"
