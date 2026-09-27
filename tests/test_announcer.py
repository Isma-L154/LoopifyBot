"""Now Playing messages: each one gets controls, and the previous one loses them."""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from utils import now_playing_view
from utils.announcer import ChannelAnnouncer
from utils.now_playing_view import NowPlayingControls


@pytest.fixture
def channel():
    ch = MagicMock()
    ch.send = AsyncMock(side_effect=lambda **kw: MagicMock(edit=AsyncMock()))
    return ch


@pytest.fixture(autouse=True)
def no_lyrics(monkeypatch):
    monkeypatch.setattr(now_playing_view, "_lyrics", None)


async def test_now_playing_is_sent_with_controls(channel, player, track_factory):
    announcer = ChannelAnnouncer(channel)
    await announcer.now_playing(player, track_factory("Song"), MagicMock())
    view = channel.send.await_args.kwargs["view"]
    assert isinstance(view, NowPlayingControls) and not view.is_finished()


async def test_the_next_track_retires_the_previous_controls(channel, player, track_factory):
    announcer = ChannelAnnouncer(channel)
    await announcer.now_playing(player, track_factory("One"), MagicMock())
    first_view = channel.send.await_args.kwargs["view"]
    first_message = announcer._live[0]

    await announcer.now_playing(player, track_factory("Two"), MagicMock())

    assert first_view.is_finished()
    assert all(item.disabled for item in first_view.children)
    first_message.edit.assert_awaited_once_with(view=first_view)


async def test_retire_with_nothing_shown_does_nothing(channel):
    await ChannelAnnouncer(channel).retire()


async def test_retire_survives_a_deleted_message(channel, player, track_factory):
    announcer = ChannelAnnouncer(channel)
    await announcer.now_playing(player, track_factory("Song"), MagicMock())
    announcer._live[0].edit.side_effect = discord.NotFound(MagicMock(), "gone")
    await announcer.retire()
    assert announcer._live is None


async def test_a_failed_send_leaves_no_live_view(channel, player, track_factory):
    channel.send.side_effect = discord.Forbidden(MagicMock(), "no access")
    announcer = ChannelAnnouncer(channel)
    await announcer.now_playing(player, track_factory("Song"), MagicMock())
    assert announcer._live is None


def test_destroying_the_player_retires_its_controls(player):
    player.announcer.retire = MagicMock()
    player.destroy()
    player.announcer.retire.assert_called_once()
