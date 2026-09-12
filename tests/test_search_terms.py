"""
Turning a YouTube title into something a lyrics database will recognise.

`!lyrics` worked for some songs and silently found nothing for others, and this
is why: the bot searched with the raw video title and the channel name. Checked
against the real LRCLIB, the difference is total —

    "Music n Lyrics" / "The Police   Every Breath You Take (Lyrics)"  -> HTTP 404
    "The Police"     / "Every Breath You Take"                        -> synced

Every sample below is the shape of a real YouTube title.
"""

import pytest

from services.synced_lyrics import search_terms


# -- the reported case -------------------------------------------------

def test_the_song_that_returned_nothing():
    """Reported against the live bot: no answer at all for this one."""
    assert search_terms("The Police   Every Breath You Take (Lyrics)",
                        "Music n Lyrics") == ("Every Breath You Take", "The Police")


# -- splitting artist from title ---------------------------------------

@pytest.mark.parametrize("raw,track,artist", [
    ("Queen - Bohemian Rhapsody", "Bohemian Rhapsody", "Queen"),
    ("Queen – Bohemian Rhapsody", "Bohemian Rhapsody", "Queen"),   # en dash
    ("Queen — Bohemian Rhapsody", "Bohemian Rhapsody", "Queen"),   # em dash
    ("Queen   Bohemian Rhapsody", "Bohemian Rhapsody", "Queen"),   # padded gap
])
def test_the_artist_comes_from_the_title_when_it_is_there(raw, track, artist):
    """A channel is often a lyrics aggregator; the title is more trustworthy."""
    assert search_terms(raw, "Some Lyrics Channel") == (track, artist)


def test_only_the_first_separator_splits():
    """`Artist - Song - Remix` is an artist and a song, not three pieces."""
    assert search_terms("Artist - Song - Extended", "")[0] == "Song - Extended"


def test_the_first_of_several_credited_artists_is_used():
    """Databases file a song under its lead artist, not the whole credit list."""
    assert search_terms("KAROL G, Judeline, rusowsky - BbY WOW (Visualizer)",
                        "KAROL G") == ("BbY WOW", "KAROL G")


def test_a_title_with_no_separator_falls_back_to_the_channel():
    assert search_terms("Never Gonna Give You Up", "Rick Astley") == (
        "Never Gonna Give You Up", "Rick Astley")


def test_a_separator_with_nothing_on_one_side_is_not_a_separator():
    assert search_terms("- Just A Title", "Channel") == ("Just A Title", "Channel")


# -- stripping the noise -----------------------------------------------

@pytest.mark.parametrize("noise", [
    "(Official Video)", "(Official Music Video)", "[Official Video]",
    "(Lyrics)", "(Lyric Video)", "(Visualizer)", "(Audio)", "(Official Audio)",
    "[4K]", "(HD)", "(Remastered)", "(Remastered 2011)", "(Video Oficial)",
    "(Letra)", "(Live)", "[Explicit]", "(MV)",
])
def test_video_furniture_is_not_part_of_the_song_name(noise):
    assert search_terms(f"Queen - Bohemian Rhapsody {noise}", "") == (
        "Bohemian Rhapsody", "Queen")


def test_a_meaningful_bracket_survives():
    """
    Not everything in brackets is furniture — some of it is the song's name.
    """
    assert search_terms("Artist - Song (Acoustic Version)", "")[0] == \
        "Song (Acoustic Version)"


def test_a_trailing_pipe_segment_is_dropped():
    assert search_terms("Bad Bunny - Tití Me Preguntó | Un Verano Sin Ti", "") == (
        "Tití Me Preguntó", "Bad Bunny")


@pytest.mark.parametrize("credit", ["ft. Someone", "feat. Someone",
                                    "Ft. Someone", "FEAT. Someone"])
def test_featured_credits_are_dropped(credit):
    """Databases file the song under the lead artist alone."""
    assert search_terms(f"Artist - Song {credit}", "")[0] == "Song"


def test_noise_and_credits_together():
    assert search_terms(
        "Eminem - Lose Yourself ft. Someone [Official Music Video] [4K]",
        "EminemMusic") == ("Lose Yourself", "Eminem")


# -- cleaning the channel name -----------------------------------------

@pytest.mark.parametrize("channel,artist", [
    ("Rick Astley - Topic", "Rick Astley"),      # auto-generated music channels
    ("QueenVEVO", "Queen"),
    ("Queen VEVO", "Queen"),
])
def test_channel_suffixes_are_not_part_of_the_artist(channel, artist):
    assert search_terms("Some Song", channel)[1] == artist


def test_a_channel_that_is_only_a_suffix_leaves_no_artist():
    assert search_terms("Some Song", "- Topic")[1] == ""


# -- degenerate input --------------------------------------------------

def test_nothing_in_nothing_out():
    assert search_terms("", "") == ("", "")


def test_a_title_that_is_all_noise_keeps_something_to_search_for():
    """Stripping everything would search for an empty string."""
    track, _ = search_terms("(Official Video)", "Channel")
    assert track == "(Official Video)"


def test_whitespace_is_collapsed():
    assert search_terms("  Artist  -   Song   ", "") == ("Song", "Artist")
