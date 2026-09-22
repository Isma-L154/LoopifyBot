"""
Rendering lyrics: the moving window, and the static pages.

Both are pure functions over text. Discord is not involved in any of it, which
is what makes the awkward cases — the first line, the last line, an instrumental
gap — cheap to pin down.
"""

import pytest

from utils.embeds import clock, lyrics_pages, lyrics_window

LINES = (
    (0.0, "one"), (10.0, "two"), (20.0, "three"),
    (30.0, "four"), (40.0, "five"), (50.0, "six"), (60.0, "seven"),
)


# -- the moving window -------------------------------------------------

def test_the_playing_line_stands_out():
    window = lyrics_window(LINES, index=3)

    assert "**▶ four**" in window
    assert "four" in window and "**▶ three**" not in window


def test_context_on_both_sides():
    window = lyrics_window(LINES, index=3, context=2)

    for word in ("two", "three", "four", "five", "six"):
        assert word in window
    assert "one" not in window and "seven" not in window


def test_the_start_of_a_song_does_not_pad_with_blanks():
    """At line 0 there is nothing before it; the window just starts shorter."""
    window = lyrics_window(LINES, index=0, context=2)

    assert window.splitlines()[0].strip() == "▶ one".replace("▶ ", "**▶ ") + "**"


def test_the_end_of_a_song_does_not_run_off():
    window = lyrics_window(LINES, index=6, context=2)

    assert "**▶ seven**" in window
    assert "five" in window and "six" in window


def test_before_the_first_line_nothing_is_playing_yet():
    """
    A song with an instrumental intro should not pretend line one is singing.
    """
    window = lyrics_window(LINES, index=-1, context=2)

    assert "**▶" not in window
    assert "one" in window, "what is coming should still be visible"


def test_an_instrumental_gap_reads_as_one():
    """A timed line with no words is a pause, and should look like a pause."""
    lines = ((0.0, "sing"), (10.0, ""), (20.0, "sing again"))

    assert "♪" in lyrics_window(lines, index=1)


def test_no_lyrics_at_all_renders_nothing():
    assert lyrics_window((), index=-1) == ""


def test_a_single_line_song_works():
    assert "**▶ only**" in lyrics_window(((0.0, "only"),), index=0)


# -- the progress readout ----------------------------------------------

@pytest.mark.parametrize("seconds,expected", [
    (0, "0:00"),
    (9, "0:09"),
    (61, "1:01"),
    (226, "3:46"),
    (3600, "60:00"),     # an hour-long mix stays in minutes, not "1:00:00"
])
def test_the_clock_reads_like_a_music_player(seconds, expected):
    assert clock(seconds) == expected


def test_a_negative_position_does_not_render_a_minus():
    assert clock(-3) == "0:00"


# -- static pages ------------------------------------------------------

def test_short_lyrics_are_one_page():
    assert lyrics_pages("a\nb\nc") == ["a\nb\nc"]


def test_long_lyrics_are_split():
    text = "\n".join(f"line {i}" for i in range(2000))

    pages = lyrics_pages(text, limit=1000)

    assert len(pages) > 1
    assert all(len(page) <= 1000 for page in pages)


def test_a_page_break_never_lands_mid_line():
    """
    The old version sliced at a fixed character count, which cut words in half.
    """
    text = "\n".join(f"line number {i}" for i in range(500))

    for page in lyrics_pages(text, limit=200):
        for line in page.splitlines():
            assert line == "" or line.startswith("line number"), \
                f"a line was cut in half: {line!r}"


def test_nothing_is_lost_across_the_split():
    text = "\n".join(f"line {i}" for i in range(300))

    assert "\n".join(lyrics_pages(text, limit=250)) == text


def test_a_single_line_longer_than_the_limit_is_split_anyway():
    """One enormous line must not produce a page Discord will reject."""
    pages = lyrics_pages("x" * 500, limit=100)

    assert all(len(page) <= 100 for page in pages)
    assert "".join(pages) == "x" * 500


def test_empty_lyrics_give_one_empty_page():
    assert lyrics_pages("") == [""]


@pytest.mark.parametrize("seconds,expected", [
    (210, "0:03:30"),
    (187.43, "0:03:07"),        # SoundCloud reports fractional durations
    (3725.9, "1:02:05"),
])
def test_durations_are_shown_in_whole_seconds(seconds, expected):
    from utils.embeds import format_duration

    assert format_duration(seconds) == expected
