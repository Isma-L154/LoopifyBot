"""
Command bodies, driven through a mocked context.

Commands are invoked via ``.callback(cog, ctx, ...)``, which runs the real
command body without a gateway connection. Decorator checks (cooldowns,
``@same_voice_channel``) are covered separately in ``test_checks.py``.

The focus is the edge cases called out in the project brief: acting on an empty
queue, acting while nothing is playing, out-of-range input, and the bot being
disconnected from voice by someone else.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cogs.music import Music, MAX_QUERY_LEN, _is_playlist_url
from cogs.effects import Effects
from utils.player import players


@pytest.fixture
def ctx(fake_guild):
    c = MagicMock()
    c.guild = fake_guild
    c.send = AsyncMock()
    c.voice_client = None
    c.author = MagicMock()
    c.typing = MagicMock(return_value=AsyncMock())
    return c


@pytest.fixture
def music_cog():
    return Music(MagicMock())


@pytest.fixture
def effects_cog():
    return Effects(MagicMock())


def sent_text(ctx) -> str:
    """The text of the last embed the command sent."""
    assert ctx.send.await_count >= 1, "the command sent nothing at all"
    embed = ctx.send.await_args.kwargs.get("embed") or ctx.send.await_args.args[0]
    return (embed.description or "") + (embed.title or "")


# -- playlist URL detection -------------------------------------------

@pytest.mark.parametrize("url", [
    "https://www.youtube.com/playlist?list=PL123",
    "https://soundcloud.com/artist/sets/my-set",
    "https://artist.bandcamp.com/album/thing",
])
def test_playlist_urls_are_detected(url):
    assert _is_playlist_url(url) is True


@pytest.mark.parametrize("query", [
    "https://www.youtube.com/watch?v=abc&list=PL123",   # a track inside a playlist
    "https://www.youtube.com/watch?v=abc",
    "bohemian rhapsody",
    "sc: lofi",
    "playlist",                                          # bare word, not a URL
])
def test_non_playlist_queries_are_not_treated_as_playlists(query):
    assert _is_playlist_url(query) is False


# -- acting when nothing is playing ------------------------------------

async def test_skip_with_nothing_playing_reports_it(music_cog, ctx):
    await Music.skip.callback(music_cog, ctx)
    assert "Nothing is playing" in sent_text(ctx)


async def test_skip_with_an_empty_queue_does_not_raise(music_cog, ctx, fake_bot, fake_guild):
    """An empty queue must not crash the command."""
    player = players.get_or_create(fake_bot, ctx)
    try:
        player.queue.clear()
        fake_guild.voice_client = None
        await Music.skip.callback(music_cog, ctx)
        assert "Nothing is playing" in sent_text(ctx)
    finally:
        players.discard(fake_guild.id)


async def test_pause_with_nothing_playing(music_cog, ctx):
    await Music.pause.callback(music_cog, ctx)
    assert "Nothing is playing" in sent_text(ctx)


async def test_resume_with_nothing_paused(music_cog, ctx):
    await Music.resume.callback(music_cog, ctx)
    assert "Nothing is paused" in sent_text(ctx)


async def test_previous_with_no_history(music_cog, ctx):
    await Music.previous.callback(music_cog, ctx)
    assert "No previous track" in sent_text(ctx)


async def test_nowplaying_with_no_player(music_cog, ctx):
    await Music.nowplaying.callback(music_cog, ctx)
    assert "Nothing is playing" in sent_text(ctx)


async def test_queue_with_no_player(music_cog, ctx):
    await Music.queue.callback(music_cog, ctx)
    assert "Nothing is playing" in sent_text(ctx)


async def test_stop_with_no_player_and_no_voice_still_confirms(music_cog, ctx):
    await Music.stop.callback(music_cog, ctx)
    assert "Disconnected" in sent_text(ctx)


# -- out-of-range and oversized input ----------------------------------

@pytest.mark.parametrize("vol", [-1, 101, 1000])
async def test_volume_out_of_range_is_rejected(music_cog, ctx, vol):
    await Music.volume.callback(music_cog, ctx, vol)
    assert "between 0 and 100" in sent_text(ctx)


@pytest.mark.parametrize("vol", [0, 50, 100])
async def test_volume_boundaries_are_accepted(music_cog, ctx, vol):
    """0 and 100 are valid - an off-by-one here would reject mute and max."""
    await Music.volume.callback(music_cog, ctx, vol)
    assert "between 0 and 100" not in sent_text(ctx)


async def test_loop_rejects_an_unknown_mode(music_cog, ctx):
    await Music.loop.callback(music_cog, ctx, "banana")
    assert "must be" in sent_text(ctx)


async def test_play_rejects_an_over_length_query(music_cog, ctx):
    await Music.play.callback(music_cog, ctx, query="x" * (MAX_QUERY_LEN + 1))
    assert "too long" in sent_text(ctx)
    ctx.typing.assert_not_called()      # rejected before any yt-dlp work


async def test_shuffle_on_an_empty_queue(music_cog, ctx):
    await Music.shuffle.callback(music_cog, ctx)
    assert "empty" in sent_text(ctx).lower()


async def test_remove_on_an_empty_queue(music_cog, ctx):
    await Music.remove.callback(music_cog, ctx, 1)
    assert "No track at position" in sent_text(ctx)


async def test_move_with_no_player(music_cog, ctx):
    await Music.move.callback(music_cog, ctx, 1, 2)
    assert "Invalid positions" in sent_text(ctx)


# -- effects with no active player -------------------------------------

async def test_effect_command_with_no_player(effects_cog, ctx):
    await Effects.bassboost.callback(effects_cog, ctx)
    assert "Nothing is playing" in sent_text(ctx)


async def test_effect_changes_are_throttled_per_guild(effects_cog, ctx):
    await Effects.bass.callback(effects_cog, ctx)
    ctx.send.reset_mock()
    await Effects.nightcore.callback(effects_cog, ctx)
    assert "wait" in sent_text(ctx).lower(), "a second effect inside the cooldown must be refused"


async def test_current_effect_reports_none_when_idle(effects_cog, ctx):
    await Effects.current_effect.callback(effects_cog, ctx)
    assert "none" in sent_text(ctx).lower()


# -- the bot being disconnected by someone else ------------------------

def _guild_with_voice(guild_id: int, members: list):
    guild = MagicMock()
    guild.id = guild_id
    vc = MagicMock()
    vc.is_connected.return_value = True
    vc.disconnect = AsyncMock()
    channel = MagicMock()
    channel.members = members
    vc.channel = channel
    guild.voice_client = vc
    return guild, vc, channel


async def test_bot_left_alone_in_voice_disconnects(music_cog):
    """Everyone leaving must not strand the bot in an empty channel."""
    guild, vc, channel = _guild_with_voice(999, [MagicMock(bot=True)])
    member = MagicMock(bot=False)
    member.guild = guild

    with patch("asyncio.sleep", new=AsyncMock()):
        await Music.on_voice_state_update(
            music_cog, member, MagicMock(channel=channel), MagicMock(channel=None)
        )

    vc.disconnect.assert_awaited_once()


async def test_bot_stays_when_a_human_is_still_in_the_channel(music_cog):
    guild, vc, channel = _guild_with_voice(
        998, [MagicMock(bot=True), MagicMock(bot=False)]
    )
    member = MagicMock(bot=False)
    member.guild = guild

    with patch("asyncio.sleep", new=AsyncMock()):
        await Music.on_voice_state_update(
            music_cog, member, MagicMock(channel=channel), MagicMock(channel=None)
        )

    vc.disconnect.assert_not_awaited()


async def test_another_bot_leaving_is_ignored(music_cog):
    member = MagicMock(bot=True)
    member.guild = MagicMock()
    # Must return before touching voice state at all.
    await Music.on_voice_state_update(music_cog, member, MagicMock(), MagicMock())


async def test_voice_update_in_a_different_channel_is_ignored(music_cog):
    guild, vc, _channel = _guild_with_voice(997, [MagicMock(bot=True)])
    member = MagicMock(bot=False)
    member.guild = guild

    other_channel = MagicMock()
    with patch("asyncio.sleep", new=AsyncMock()):
        await Music.on_voice_state_update(
            music_cog, member, MagicMock(channel=other_channel), MagicMock(channel=None)
        )
    vc.disconnect.assert_not_awaited()
