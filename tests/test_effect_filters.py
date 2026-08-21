"""
The FFmpeg filter presets, measured against real FFmpeg.

The pitch/speed effects are the ones worth measuring: ``asetrate``
*reinterprets* a stream's declared sample rate rather than scaling it, so a
hardcoded constant silently produces a different speed for every source rate.
Rendering a tone and measuring the output is the only way to catch that - the
filter string looks perfectly reasonable either way.

Skipped when FFmpeg is not installed.
"""

import shutil
import subprocess

import pytest

from cogs.effects import EFFECTS
from services import media

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="FFmpeg is not installed"
)

OUTPUT_RATE = 48000          # what Discord consumes
SOURCE_SECONDS = 2.0
# Rates real sources actually use: 48 kHz (YouTube Opus), 44.1 kHz (CD-derived
# uploads, much of SoundCloud), and lower ones on older or spoken-word uploads.
SOURCE_RATES = [48000, 44100, 32000, 22050]


def rendered_seconds(audio_filter: str, source_rate: int) -> float:
    """
    Push a sine through ``audio_filter`` and return how long the result plays.

    Output is raw 16-bit mono at 48 kHz, so duration is just a byte count.
    """
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi",
            "-i", f"sine=frequency=440:sample_rate={source_rate}:duration={SOURCE_SECONDS}",
            "-af", audio_filter,
            "-f", "s16le", "-ac", "1", "-ar", str(OUTPUT_RATE), "-",
        ],
        capture_output=True, check=True,
    )
    return len(result.stdout) / (2 * OUTPUT_RATE)


def speed_factor(audio_filter: str, source_rate: int) -> float:
    """How much faster the filtered audio plays than the source."""
    return SOURCE_SECONDS / rendered_seconds(audio_filter, source_rate)


# -- the bug ------------------------------------------------------------

@pytest.mark.parametrize("source_rate", SOURCE_RATES)
def test_nightcore_speed_does_not_depend_on_the_source_rate(source_rate):
    assert speed_factor(EFFECTS["nightcore"], source_rate) == pytest.approx(1.25, rel=0.02)


@pytest.mark.parametrize("source_rate", SOURCE_RATES)
def test_vaporwave_speed_does_not_depend_on_the_source_rate(source_rate):
    assert speed_factor(EFFECTS["vaporwave"], source_rate) == pytest.approx(0.8, rel=0.02)


def test_the_hardcoded_form_really_was_rate_dependent():
    """
    Documents why the fix exists. The previous filter reinterpreted every source
    as 48 kHz, so a 22 kHz upload played at 2.7x instead of 1.25x.
    """
    previous = "asetrate=48000*1.25,aresample=48000"
    assert speed_factor(previous, 48000) == pytest.approx(1.25, rel=0.02)
    assert speed_factor(previous, 44100) == pytest.approx(1.36, rel=0.02)
    assert speed_factor(previous, 22050) == pytest.approx(2.72, rel=0.02)


# -- every preset must be valid FFmpeg ----------------------------------

@pytest.mark.parametrize("name", sorted(EFFECTS))
def test_every_preset_is_accepted_by_ffmpeg(name):
    """A typo in a filter string would only surface as silence at playback."""
    rendered_seconds(EFFECTS[name], 48000)      # check=True raises on rejection


@pytest.mark.parametrize("name", sorted(set(EFFECTS) - {"nightcore", "vaporwave"}))
def test_non_speed_effects_leave_the_duration_alone(name):
    """Only the two pitch effects are meant to change how long a track runs."""
    assert speed_factor(EFFECTS[name], 44100) == pytest.approx(1.0, rel=0.05)


@pytest.mark.parametrize("name", sorted(EFFECTS))
def test_every_preset_produces_audio(name):
    assert rendered_seconds(EFFECTS[name], 44100) > 0


# -- resuming in place, through the real audio path ---------------------

def played_seconds(source) -> float:
    """Drain a discord AudioSource and return how much audio it yielded."""
    frames = 0
    while source.read():
        frames += 1
    source.cleanup()
    return frames * 0.02        # discord consumes 20 ms frames


@pytest.fixture
def tone_file(tmp_path):
    """A 30-second MP3 on disk, to feed the pipe source from a real file."""
    path = tmp_path / "tone.mp3"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi",
            "-i", "sine=frequency=440:sample_rate=44100:duration=30",
            str(path),
        ],
        check=True,
    )
    return path


@pytest.mark.parametrize("seek,expected_remaining", [
    (0, 30.0),
    (10, 20.0),
    (25, 5.0),
])
def test_seeking_starts_playback_partway_in(tone_file, seek, expected_remaining):
    """
    The whole point of #9: an effect change respawns FFmpeg, and the new
    process has to pick up where the listener was rather than at 0:00.
    """
    with open(tone_file, "rb") as handle:
        source = media.make_pipe_source(handle, seek_seconds=seek)
        assert played_seconds(source) == pytest.approx(expected_remaining, abs=0.3)


def test_seeking_and_an_effect_apply_together(tone_file):
    """A resumed track keeps the effect that triggered the respawn."""
    with open(tone_file, "rb") as handle:
        source = media.make_pipe_source(
            handle, ffmpeg_filter=EFFECTS["bassboost"], seek_seconds=20,
        )
        assert played_seconds(source) == pytest.approx(10.0, abs=0.3)
