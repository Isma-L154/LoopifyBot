"""
The Genius client, built against the lyricsgenius that is actually installed.

Issue #37: `!lyrics` had never worked with the pinned lyricsgenius. The client
was constructed with `quiet=True`, an argument from the 2.x series, so every
lookup died in the constructor with a TypeError and the command answered
"Couldn't find lyrics" — which reads like a failed search, not a broken build.

Nothing here talks to Genius. The point is the wiring: that the constructor
accepts what we pass it, that a missing token is handled before it can raise,
and that the call is bounded.
"""

import threading
from unittest.mock import MagicMock

import lyricsgenius
import pytest

from services import lyrics_api

TOKEN = "not-a-real-token"


@pytest.fixture
def token(monkeypatch):
    """
    A token present in the module, and a client cache cleared around it.

    The real cached function is held onto rather than looked up again on the way
    out: tests that replace ``_client`` outright would otherwise have the
    teardown reach for ``cache_clear`` on their own stand-in.
    """
    cached = lyrics_api._client
    cached.cache_clear()
    monkeypatch.setattr(lyrics_api, "GENIUS_TOKEN", TOKEN)
    yield TOKEN
    cached.cache_clear()


def _recording_client(calls: list) -> MagicMock:
    """A client whose ``search_song`` records the positional arguments it got."""
    def search_song(*args):
        calls.append(args)
        return FakeSong()
    return MagicMock(search_song=search_song)


class FakeSong:
    title = "Bohemian Rhapsody"
    artist = "Queen"
    lyrics = "Is this the real life?"
    url = "https://genius.invalid/song"


# -- building the client -----------------------------------------------

def test_the_client_is_accepted_by_the_installed_lyricsgenius(token):
    """
    The regression itself: this raised
    ``TypeError: Genius.__init__() got an unexpected keyword argument 'quiet'``.
    """
    assert isinstance(lyrics_api._client(), lyricsgenius.Genius)


def test_the_client_is_reused(token):
    assert lyrics_api._client() is lyrics_api._client()


def test_section_headers_are_kept(token):
    """`[Chorus]` and friends are wanted in the embed, so they stay."""
    assert lyrics_api._client().remove_section_headers is False


def test_non_songs_are_skipped(token):
    """Genius indexes tracklists and credits pages; those are not lyrics."""
    assert lyrics_api._client().skip_non_songs is True


def test_the_lookup_is_bounded(token):
    """
    An unbounded lookup pins an executor thread, and a pinned executor thread
    delays the event loop's shutdown — the ceiling that #35 was about.
    """
    assert 0 < lyrics_api._client().timeout <= 30


# -- fetching ----------------------------------------------------------

async def test_a_missing_token_gives_up_before_building_a_client(monkeypatch):
    """
    Without a token lyricsgenius falls back to $GENIUS_ACCESS_TOKEN and raises
    KeyError, so the guard has to come first. config.validate() already warns
    about this at startup; a user typing !lyrics should just get an answer.
    """
    lyrics_api._client.cache_clear()
    monkeypatch.setattr(lyrics_api, "GENIUS_TOKEN", None)
    monkeypatch.setattr(lyrics_api, "_client",
                        MagicMock(side_effect=AssertionError("must not be built")))

    assert await lyrics_api.fetch("Bohemian Rhapsody") is None


async def test_a_found_song_becomes_a_result(token, monkeypatch):
    monkeypatch.setattr(lyrics_api, "_client",
                        lambda: MagicMock(search_song=lambda *a, **k: FakeSong()))

    result = await lyrics_api.fetch("Bohemian Rhapsody", "Queen")

    assert result == {
        "title": "Bohemian Rhapsody",
        "artist": "Queen",
        "lyrics": "Is this the real life?",
        "url": "https://genius.invalid/song",
    }


async def test_no_match_is_not_an_error(token, monkeypatch):
    monkeypatch.setattr(lyrics_api, "_client",
                        lambda: MagicMock(search_song=lambda *a, **k: None))

    assert await lyrics_api.fetch("asdkjhaskdjh") is None


async def test_an_unreachable_genius_is_not_an_error(token, monkeypatch):
    """A missing lyric is never a reason to take the bot down."""
    def explode(*args, **kwargs):
        raise ConnectionError("genius is down")

    monkeypatch.setattr(lyrics_api, "_client",
                        lambda: MagicMock(search_song=explode))

    assert await lyrics_api.fetch("Bohemian Rhapsody") is None


async def test_the_artist_is_used_when_given(token, monkeypatch):
    calls = []
    monkeypatch.setattr(lyrics_api, "_client", lambda: _recording_client(calls))

    await lyrics_api.fetch("Bohemian Rhapsody", "Queen")

    assert calls == [("Bohemian Rhapsody", "Queen")]


async def test_a_bare_title_does_not_pass_an_empty_artist(token, monkeypatch):
    """Genius matches worse against an empty artist than against none at all."""
    calls = []
    monkeypatch.setattr(lyrics_api, "_client", lambda: _recording_client(calls))

    await lyrics_api.fetch("Bohemian Rhapsody")

    assert calls == [("Bohemian Rhapsody",)]


async def test_the_search_never_runs_on_the_event_loop(token, monkeypatch):
    """
    lyricsgenius is blocking and synchronous. Calling it on the loop would freeze
    every other guild's playback for the length of an HTTP round trip.
    """
    threads = []

    def record(*args):
        threads.append(threading.get_ident())
        return FakeSong()

    monkeypatch.setattr(lyrics_api, "_client", lambda: MagicMock(search_song=record))

    await lyrics_api.fetch("Bohemian Rhapsody")

    assert threads and threads[0] != threading.get_ident(),         "the blocking call ran on the event loop thread"
