"""
How the command decides what to look up, and what it does with the answer.

The gap these close is a real one: the cog's own class is called ``Lyrics`` and
it shadowed the dataclass of the same name, so the Genius fallback raised
``TypeError: Lyrics.__init__() got an unexpected keyword argument 'title'`` and
the command answered nothing at all. No test reached that path.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from cogs.lyrics import Lyrics as LyricsCog
from services import lyrics_api, synced_lyrics

GENIUS_HIT = {
    "title": "Every Breath You Take",
    "artist": "The Police",
    "lyrics": "Every breath you take",
    "url": "https://genius.invalid/song",
}


@pytest.fixture
def cog():
    cog = LyricsCog(MagicMock())
    cog._session = MagicMock()      # what cog_load opens; every lookup is mocked
    return cog


@pytest.fixture
def no_lrclib(monkeypatch):
    monkeypatch.setattr(synced_lyrics, "fetch", AsyncMock(return_value=None))


# -- the crash that produced silence -----------------------------------

async def test_the_genius_fallback_produces_lyrics(cog, no_lrclib, monkeypatch):
    """This raised TypeError in production, and the user saw nothing at all."""
    monkeypatch.setattr(lyrics_api, "fetch", AsyncMock(return_value=GENIUS_HIT))

    found = await cog._find("Every Breath You Take", "The Police", None)

    assert found.title == "Every Breath You Take"
    assert found.artist == "The Police"
    assert found.plain == "Every breath you take"
    assert found.synced is False


async def test_nothing_anywhere_is_not_an_error(cog, no_lrclib, monkeypatch):
    monkeypatch.setattr(lyrics_api, "fetch", AsyncMock(return_value=None))

    assert await cog._find("zxqwv", "", None) is None


async def test_lrclib_wins_when_it_has_the_song(cog, monkeypatch):
    """Genius has no timings, so it is only ever the fallback."""
    synced = synced_lyrics.Lyrics("T", "A", lines=((0.0, "line"),))
    monkeypatch.setattr(synced_lyrics, "fetch", AsyncMock(return_value=synced))
    genius = AsyncMock(return_value=GENIUS_HIT)
    monkeypatch.setattr(lyrics_api, "fetch", genius)

    found = await cog._find("T", "A", None)

    assert found.synced is True
    genius.assert_not_awaited()


# -- what gets searched for --------------------------------------------

async def test_a_track_is_looked_up_with_tidied_terms(cog, monkeypatch):
    """
    The reported failure: the raw video title and the channel name 404 on
    LRCLIB, where the tidied pair returns synced lyrics.
    """
    seen = {}

    async def spy(session, title, artist, duration):
        seen.update(title=title, artist=artist, duration=duration)
        return None

    monkeypatch.setattr(synced_lyrics, "fetch", spy)
    monkeypatch.setattr(lyrics_api, "fetch", AsyncMock(return_value=None))

    await cog._for_track({
        "title": "The Police   Every Breath You Take (Lyrics)",
        "uploader": "Music n Lyrics",
        "duration": 253,
    })

    assert seen == {"title": "Every Breath You Take",
                    "artist": "The Police", "duration": 253}


async def test_a_track_with_no_uploader_still_searches(cog, monkeypatch):
    seen = {}

    async def spy(session, title, artist, duration):
        seen.update(title=title, artist=artist)
        return None

    monkeypatch.setattr(synced_lyrics, "fetch", spy)
    monkeypatch.setattr(lyrics_api, "fetch", AsyncMock(return_value=None))

    await cog._for_track({"title": "Some Song", "uploader": None, "duration": None})

    assert seen == {"title": "Some Song", "artist": ""}


# -- a typed query, either way round -----------------------------------

async def test_a_typed_query_is_tried_both_ways_round(cog, monkeypatch):
    """
    The help says `<title> - <artist>`, but `The Police - Every Breath You Take`
    is the way people actually type it. Both should work.
    """
    tried = []

    async def spy(session, title, artist, duration):
        tried.append((title, artist))
        return synced_lyrics.Lyrics("Every Breath You Take", "The Police",
                                    plain="words") if artist == "The Police" else None

    monkeypatch.setattr(synced_lyrics, "fetch", spy)
    monkeypatch.setattr(lyrics_api, "fetch", AsyncMock(return_value=None))

    found = await cog._search("The Police - Every Breath You Take")

    assert found is not None, "the artist-first order found nothing"
    assert tried[0] == ("The Police", "Every Breath You Take"), "documented order first"


async def test_the_documented_order_is_not_searched_twice(cog, monkeypatch):
    """A hit on the first try must not cost a second request."""
    hit = synced_lyrics.Lyrics("Bohemian Rhapsody", "Queen", plain="words")
    fetch = AsyncMock(return_value=hit)
    monkeypatch.setattr(synced_lyrics, "fetch", fetch)

    await cog._search("Bohemian Rhapsody - Queen")

    assert fetch.await_count == 1


async def test_a_query_without_a_separator_is_searched_once(cog, monkeypatch):
    fetch = AsyncMock(return_value=None)
    monkeypatch.setattr(synced_lyrics, "fetch", fetch)
    monkeypatch.setattr(lyrics_api, "fetch", AsyncMock(return_value=None))

    await cog._search("Bohemian Rhapsody")

    assert fetch.await_count == 1, "there is no other order to try"


# -- the Now Playing lyrics button ---------------------------------------

async def test_the_cog_offers_itself_to_the_lyrics_button_while_loaded(monkeypatch):
    from utils import now_playing_view

    monkeypatch.setattr(now_playing_view, "_lyrics", None)
    cog = LyricsCog(MagicMock())
    await cog.cog_load()
    assert now_playing_view._lyrics == cog.show_current
    await cog.cog_unload()
    assert now_playing_view._lyrics is None


async def test_show_current_with_nothing_playing(cog):
    player = MagicMock(current=None)
    assert await cog.show_current(MagicMock(), player) is False


async def test_show_current_when_nothing_is_found(cog, monkeypatch):
    monkeypatch.setattr(cog, "_for_track", AsyncMock(return_value=None))
    channel = MagicMock(send=AsyncMock())
    assert await cog.show_current(channel, MagicMock()) is False
    channel.send.assert_not_awaited()


async def test_show_current_posts_unsynced_lyrics_to_the_channel(cog, monkeypatch):
    plain = synced_lyrics.Lyrics("T", "A", plain="just words")
    monkeypatch.setattr(cog, "_for_track", AsyncMock(return_value=plain))
    channel = MagicMock(send=AsyncMock())
    assert await cog.show_current(channel, MagicMock()) is True
    assert "just words" in channel.send.await_args.kwargs["embed"].description


async def test_show_current_follows_synced_lyrics(cog, monkeypatch):
    synced = synced_lyrics.Lyrics("T", "A", lines=((0.0, "line"),))
    monkeypatch.setattr(cog, "_for_track", AsyncMock(return_value=synced))
    follow = AsyncMock()
    monkeypatch.setattr(cog, "_follow", follow)
    channel, player = MagicMock(), MagicMock()
    assert await cog.show_current(channel, player) is True
    follow.assert_awaited_once_with(channel, player, synced)


# -- /lyrics ----------------------------------------------------------------

async def test_slash_lyrics_go_to_the_channel_not_the_interaction(cog, monkeypatch):
    """An interaction reply stops being editable after 15 minutes; a live
    lyrics message is edited for as long as the music plays."""
    from cogs.lyrics import Lyrics
    from utils.player import players

    monkeypatch.setattr(players, "get", lambda guild_id: None)
    found = synced_lyrics.Lyrics("T", "A", plain="words")
    monkeypatch.setattr(cog, "_search", AsyncMock(return_value=found))
    present = AsyncMock()
    monkeypatch.setattr(cog, "_present", present)
    ctx = MagicMock(send=AsyncMock(), interaction=MagicMock())
    ctx.typing = MagicMock(return_value=AsyncMock())

    await Lyrics.lyrics.callback(cog, ctx, query="T - A")

    assert present.await_args.args[0] is ctx.channel
    assert ctx.send.await_args.kwargs["ephemeral"] is True
