"""
Pure helpers in ``services.media``.

Nothing here touches the network or spawns a real yt-dlp: these cover the
parsing and process-handling code that decides what gets searched and how a
failure is reported back to the user.
"""

import io
import subprocess
from unittest.mock import MagicMock

import pytest

from services import media


# ── query → yt-dlp target ─────────────────────────────────────────────

@pytest.mark.parametrize("query,expected", [
    ("sc: lofi",            "scsearch1:lofi"),
    ("SC: Lofi",            "scsearch1:Lofi"),
    ("soundcloud: lofi",    "scsearch1:lofi"),
    ("yt: never gonna",     "ytsearch1:never gonna"),
    ("youtube: never",      "ytsearch1:never"),
])
def test_search_prefixes_pick_the_right_engine(query, expected):
    target, flat_ok = media._search_target(query)
    assert target == expected
    assert flat_ok is True


def test_bare_terms_default_to_a_youtube_search():
    target, flat_ok = media._search_target("bohemian rhapsody")
    assert target == "ytsearch1:bohemian rhapsody"
    assert flat_ok is True


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=abc123",
    "https://soundcloud.com/artist/track",
    "http://example.invalid/audio.mp3",
])
def test_urls_are_passed_through_untouched(url):
    target, flat_ok = media._search_target(url)
    assert target == url
    assert flat_ok is False, "a URL must be extracted directly, not flat-listed"


def test_a_prefix_wins_over_url_detection():
    """`sc: https://...` should search SoundCloud, not extract the URL."""
    target, _ = media._search_target("sc: https://example.invalid/x")
    assert target == "scsearch1:https://example.invalid/x"


# ── info dict → track dict ────────────────────────────────────────────

def test_build_track_maps_the_common_fields():
    track = media._build_track({
        "title": "Song",
        "webpage_url": "https://example.invalid/s",
        "duration": 210,
        "thumbnail": "https://example.invalid/t.jpg",
        "uploader": "Artist",
        "extractor_key": "Youtube",
    }, query="song")
    assert track["title"] == "Song"
    assert track["url"] == "https://example.invalid/s"
    assert track["duration"] == 210
    assert track["source"] == "youtube"
    assert track["query"] == "song"


def test_build_track_survives_an_almost_empty_info_dict():
    """Flat playlist entries are sparse — this must never raise."""
    track = media._build_track({})
    assert track["title"] == "Unknown Title"
    assert track["url"] is None
    assert track["duration"] is None
    assert track["thumbnail"] is None
    assert track["uploader"] is None


def test_build_track_falls_back_to_url_and_channel():
    track = media._build_track({"url": "https://example.invalid/x", "channel": "Chan"})
    assert track["url"] == "https://example.invalid/x"
    assert track["uploader"] == "Chan"


def test_build_track_picks_the_largest_thumbnail_when_none_is_flagged():
    track = media._build_track({"thumbnails": [
        {"url": "small.jpg"}, {"url": "large.jpg"},
    ]})
    assert track["thumbnail"] == "large.jpg"


# ── entry unwrapping ──────────────────────────────────────────────────

def test_first_entry_unwraps_a_search_result():
    assert media._first_entry({"entries": [{"title": "A"}, {"title": "B"}]})["title"] == "A"


def test_first_entry_skips_leading_nulls():
    """yt-dlp puts None in `entries` for unavailable videos."""
    assert media._first_entry({"entries": [None, None, {"title": "C"}]})["title"] == "C"


@pytest.mark.parametrize("info", [None, {"entries": []}, {"entries": [None]}])
def test_first_entry_returns_none_when_there_is_nothing_playable(info):
    assert media._first_entry(info) is None


def test_first_entry_passes_through_a_single_result():
    assert media._first_entry({"title": "Solo"})["title"] == "Solo"


# ── failure classification ────────────────────────────────────────────

def _finished_proc(stderr: bytes) -> MagicMock:
    proc = MagicMock(spec=subprocess.Popen)
    proc.stderr = io.BytesIO(stderr)
    return proc


@pytest.mark.parametrize("stderr", [
    b"ERROR: Sign in to confirm you're not a bot",
    b"ERROR: SIGN IN TO CONFIRM you are not a bot",
    b"please confirm you're not a bot to continue",
])
def test_bot_checks_are_classified_as_blocked(stderr):
    assert media.classify_stream_error(_finished_proc(stderr)) == "blocked"


@pytest.mark.parametrize("stderr", [
    b"ERROR: Video unavailable",
    b"ERROR: Private video",
    b"",
])
def test_other_failures_are_classified_as_unavailable(stderr):
    assert media.classify_stream_error(_finished_proc(stderr)) == "unavailable"


def test_classify_handles_a_missing_process():
    assert media.classify_stream_error(None) == "unavailable"


def test_classify_survives_undecodable_stderr():
    """yt-dlp can emit non-UTF-8 bytes; classification must not raise."""
    assert media.classify_stream_error(_finished_proc(b"\xff\xfe invalid")) == "unavailable"


# ── process teardown ──────────────────────────────────────────────────

def test_kill_stream_accepts_none():
    media.kill_stream(None)        # must not raise


def test_kill_stream_leaves_an_already_finished_process_alone():
    proc = MagicMock(spec=subprocess.Popen)
    proc.poll.return_value = 0
    media.kill_stream(proc)
    proc.kill.assert_not_called()


def test_kill_stream_kills_a_running_process():
    proc = MagicMock(spec=subprocess.Popen)
    proc.poll.return_value = None
    media.kill_stream(proc)
    proc.kill.assert_called_once()


def test_kill_stream_is_idempotent():
    proc = MagicMock(spec=subprocess.Popen)
    proc.poll.side_effect = [None, 0]     # running, then dead
    media.kill_stream(proc)
    media.kill_stream(proc)
    assert proc.kill.call_count == 1
