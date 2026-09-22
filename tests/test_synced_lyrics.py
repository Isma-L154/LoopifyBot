"""
Reading LRC, the format LRCLIB returns.

The parser is where the awkward cases live, so it is a pure function with no
network and no Discord anywhere near it. The samples below are shaped like real
LRCLIB bodies, including the ones that trip a naive `split("]")`.
"""

import pytest

from services import synced_lyrics
from services.synced_lyrics import index_at, parse_lrc

# Trimmed from the real payload for Queen — Bohemian Rhapsody (LRCLIB id 19079).
REAL = """[00:00.15] Is this the real life? Is this just fantasy?
[00:07.13] Caught in a landslide, no escape from reality
[00:14.77] Open your eyes, look up to the skies and see
[00:25.37] I'm just a poor boy, I need no sympathy
[03:37.85] No, we will not let you go (let him go)
[03:40.52] بِسْمِ ٱللَّٰهِ"""


# -- parsing -----------------------------------------------------------

def test_a_timestamp_becomes_seconds_and_text():
    assert parse_lrc("[01:23.45] hello") == ((83.45, "hello"),)


def test_minutes_are_not_capped_at_sixty():
    """A ten-minute track keeps counting in minutes, not hours."""
    assert parse_lrc("[12:05.00] late") == ((725.0, "late"),)


def test_centiseconds_are_optional():
    assert parse_lrc("[00:09] no fraction") == ((9.0, "no fraction"),)


def test_milliseconds_are_read_at_the_right_scale():
    """Three digits is thousandths, two is hundredths — not the same number."""
    assert parse_lrc("[00:01.5] a") == ((1.5, "a"),)
    assert parse_lrc("[00:01.50] b") == ((1.5, "b"),)
    assert parse_lrc("[00:01.500] c") == ((1.5, "c"),)


def test_one_line_can_carry_several_timestamps():
    """A repeated chorus is written once with every time it occurs."""
    assert parse_lrc("[00:10.00][01:20.00] chorus") == (
        (10.0, "chorus"), (80.0, "chorus"))


def test_metadata_tags_are_not_lyrics():
    body = "[ar: Queen]\n[ti: Bohemian Rhapsody]\n[length: 5:55]\n[00:01.00] real line"
    assert parse_lrc(body) == ((1.0, "real line"),)


def test_untimed_lines_are_dropped():
    assert parse_lrc("no timestamp here\n[00:01.00] kept") == ((1.0, "kept"),)


def test_an_empty_line_is_kept_as_a_pause():
    """
    LRCLIB marks instrumental gaps with a timestamp and no words. Dropping them
    would leave the previous line on screen through the whole break, and the
    window would jump when singing resumed.
    """
    assert parse_lrc("[00:01.00] a\n[00:05.00]\n[00:09.00] b") == (
        (1.0, "a"), (5.0, ""), (9.0, "b"))


def test_lines_come_back_in_time_order():
    assert parse_lrc("[00:09.00] third\n[00:01.00] first\n[00:05.00] second") == (
        (1.0, "first"), (5.0, "second"), (9.0, "third"))


def test_leading_space_after_the_stamp_is_not_part_of_the_lyric():
    assert parse_lrc("[00:01.00]    padded   ") == ((1.0, "padded"),)


def test_a_body_with_no_timestamps_parses_to_nothing():
    """Plain lyrics must not be mistaken for a synced body."""
    assert parse_lrc("Is this the real life?\nIs this just fantasy?") == ()


def test_an_empty_body_parses_to_nothing():
    assert parse_lrc("") == ()


def test_the_real_payload_parses():
    lines = parse_lrc(REAL)
    assert len(lines) == 6
    assert lines[0] == (0.15, "Is this the real life? Is this just fantasy?")
    assert lines[-1][0] == pytest.approx(220.52)
    assert lines[-1][1] == "بِسْمِ ٱللَّٰهِ", "unicode must survive the parse"


# -- finding the line that is playing ----------------------------------

LINES = ((0.0, "zero"), (10.0, "ten"), (20.0, "twenty"))


@pytest.mark.parametrize("position,expected", [
    (0.0, 0),      # exactly on the first mark
    (5.0, 0),
    (9.99, 0),
    (10.0, 1),     # exactly on a mark belongs to that line
    (15.0, 1),
    (20.0, 2),
    (999.0, 2),    # past the end, the last line stays
])
def test_the_line_playing_at_a_position(position, expected):
    assert index_at(LINES, position) == expected


def test_before_the_first_line_there_is_none():
    """A song with an intro should show nothing rather than the first line."""
    assert index_at(((5.0, "first"),), 1.0) == -1


def test_no_lines_at_all():
    assert index_at((), 12.0) == -1


# -- the LRCLIB client -------------------------------------------------
#
# Shaped like the real response for Queen — Bohemian Rhapsody (LRCLIB id 19079).
# Nothing here touches the network.

SYNCED_PAYLOAD = {
    "id": 19079,
    "trackName": "Bohemian Rhapsody",
    "artistName": "Queen",
    "albumName": "Stone Cold Classics",
    "duration": 355.0,
    "instrumental": False,
    "plainLyrics": "Is this the real life?\nIs this just fantasy?",
    "syncedLyrics": "[00:00.15] Is this the real life?\n[00:07.13] Caught in a landslide",
}


class FakeResponse:
    def __init__(self, status: int, payload=None, raises: Exception | None = None):
        self.status = status
        self._payload = payload
        self._raises = raises

    async def json(self, **kwargs):
        if self._raises is not None:
            raise self._raises
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Stands in for aiohttp, recording what was asked for."""

    def __init__(self, response):
        self._response = response
        self.calls: list[dict] = []

    def get(self, url, *, params=None, **kwargs):
        self.calls.append({"url": url, "params": params})
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


async def test_a_synced_payload_comes_back_parsed():
    session = FakeSession(FakeResponse(200, SYNCED_PAYLOAD))

    result = await synced_lyrics.fetch(session, "Bohemian Rhapsody", "Queen", 355)

    assert result.synced is True
    assert result.title == "Bohemian Rhapsody"
    assert result.artist == "Queen"
    assert result.lines[0] == (0.15, "Is this the real life?")


async def test_the_duration_is_sent_when_known():
    """It is what separates a studio cut from a nine-minute live version."""
    session = FakeSession(FakeResponse(200, SYNCED_PAYLOAD))

    await synced_lyrics.fetch(session, "Bohemian Rhapsody", "Queen", 355)

    assert session.calls[0]["params"] == {
        "track_name": "Bohemian Rhapsody", "artist_name": "Queen", "duration": 355,
    }


async def test_a_live_stream_has_no_duration_to_send():
    session = FakeSession(FakeResponse(200, SYNCED_PAYLOAD))

    await synced_lyrics.fetch(session, "Bohemian Rhapsody", "Queen", None)

    assert "duration" not in session.calls[0]["params"]


async def test_a_plain_only_payload_is_not_synced():
    payload = {**SYNCED_PAYLOAD, "syncedLyrics": None}
    session = FakeSession(FakeResponse(200, payload))

    result = await synced_lyrics.fetch(session, "x", "y", None)

    assert result.synced is False
    assert result.plain.startswith("Is this the real life?")


async def test_a_synced_body_with_no_usable_stamps_falls_back_to_plain():
    payload = {**SYNCED_PAYLOAD, "syncedLyrics": "no stamps in here at all"}
    session = FakeSession(FakeResponse(200, payload))

    result = await synced_lyrics.fetch(session, "x", "y", None)

    assert result.synced is False
    assert result.plain


async def test_an_instrumental_track_says_so():
    """Answering "no lyrics found" for an instrumental is a worse answer."""
    payload = {**SYNCED_PAYLOAD, "instrumental": True,
               "syncedLyrics": None, "plainLyrics": None}
    session = FakeSession(FakeResponse(200, payload))

    result = await synced_lyrics.fetch(session, "x", "y", None)

    assert result.instrumental is True
    assert result.synced is False


async def test_an_unknown_track_is_not_an_error():
    session = FakeSession(FakeResponse(404))

    assert await synced_lyrics.fetch(session, "zxqwv", "nobody", None) is None


async def test_a_server_error_is_not_an_error_here():
    session = FakeSession(FakeResponse(503))

    assert await synced_lyrics.fetch(session, "x", "y", None) is None


async def test_a_network_failure_never_reaches_the_caller():
    """LRCLIB being down is not a reason to stop the music."""
    session = FakeSession(OSError("connection refused"))

    assert await synced_lyrics.fetch(session, "x", "y", None) is None


async def test_a_malformed_body_is_not_an_error():
    session = FakeSession(FakeResponse(200, raises=ValueError("not json")))

    assert await synced_lyrics.fetch(session, "x", "y", None) is None


async def test_a_payload_with_nothing_in_it_is_no_result():
    payload = {**SYNCED_PAYLOAD, "syncedLyrics": None,
               "plainLyrics": None, "instrumental": False}
    session = FakeSession(FakeResponse(200, payload))

    assert await synced_lyrics.fetch(session, "x", "y", None) is None


async def test_plain_text_is_derived_when_only_the_synced_body_exists():
    """
    `!lyrics <search>` shows static text, and a payload can carry timings with
    no plain copy. Without this the page would render empty.
    """
    payload = {**SYNCED_PAYLOAD, "plainLyrics": None}
    session = FakeSession(FakeResponse(200, payload))

    result = await synced_lyrics.fetch(session, "x", "y", None)

    assert result.synced is True
    assert result.plain == "Is this the real life?\nCaught in a landslide"


async def test_derived_plain_text_keeps_instrumental_gaps_as_blank_lines():
    payload = {**SYNCED_PAYLOAD, "plainLyrics": None,
               "syncedLyrics": "[00:01.00] sing\n[00:05.00]\n[00:09.00] again"}
    session = FakeSession(FakeResponse(200, payload))

    assert (await synced_lyrics.fetch(session, "x", "y", None)).plain == "sing\n\nagain"
