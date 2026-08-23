"""
Pure helpers in ``services.media``.

Nothing here touches the network or spawns a process: these cover the parsing
that decides what gets searched and how a yt-dlp result becomes a track.
Process handling lives in ``tests/test_audio_stream.py``.
"""

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


# Process teardown and failure classification moved to AudioStream when the
# stderr-deadlock fix landed; they are covered against real subprocesses in
# tests/test_audio_stream.py.


# -- YouTube player clients --------------------------------------------

def test_the_two_client_usages_cannot_drift():
    """
    The chain is used twice: as a list for metadata extraction and as a
    comma-joined string for the streaming subprocess. When those were separate
    literals they drifted, and search and playback disagreed about which client
    to use.
    """
    from_options = media.YTDL_OPTIONS["extractor_args"]["youtube"]["player_client"]
    assert list(media._PLAYER_CLIENTS) == list(from_options)


def test_spawn_stream_passes_the_same_chain(monkeypatch):
    captured = {}
    monkeypatch.setattr(media.AudioStream, "launch", classmethod(
        lambda cls, cmd: captured.setdefault("cmd", cmd)
    ))
    media.spawn_stream({"url": "https://example.invalid/x", "title": "T"})
    cmd = captured["cmd"]
    arg = cmd[cmd.index("--extractor-args") + 1]
    assert arg == f"youtube:player_client={','.join(media._PLAYER_CLIENTS)}"


def test_a_working_client_comes_first():
    """
    Order is latency: yt-dlp tries each client in turn, so a blocked client at
    the front costs a full round trip before anything can play. web_embedded
    measured 3.2s against mweb's 9.3s, both succeeding 6/6.
    """
    assert media._PLAYER_CLIENTS[0] == "web_embedded"


@pytest.mark.parametrize("blocked", [
    "default", "web", "android_vr", "tv", "ios", "android_music",
])
def test_clients_known_to_be_bot_checked_are_not_in_the_chain(blocked):
    """
    Every one of these was measured returning "sign in to confirm you're not a
    bot". The previous chain consisted entirely of such clients, which is why
    5 of 6 tracks failed to load.
    """
    assert blocked not in media._PLAYER_CLIENTS


def test_a_client_needing_no_js_runtime_remains_as_a_last_resort():
    """
    web_embedded and mweb need a JS runtime (Deno) to solve the signature
    challenge. tv_embedded does not, so it is the only thing that can work on a
    host where Deno failed to install.
    """
    assert "tv_embedded" in media._PLAYER_CLIENTS
