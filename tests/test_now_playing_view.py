"""
The Now Playing buttons, pressed through faked interactions.

Button callbacks are invoked directly with a fake interaction, the way
discord.py would after its own dispatch; ``interaction_check`` is tested on its
own, since discord.py runs it before any callback.
"""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from utils import now_playing_view
from utils.now_playing_view import NowPlayingControls


@pytest.fixture
def voice(fake_guild):
    vc = MagicMock()
    vc.is_playing.return_value = True
    vc.is_paused.return_value = False
    vc.channel = MagicMock(name="voice-channel")
    vc.disconnect = AsyncMock()
    fake_guild.voice_client = vc
    return vc


@pytest.fixture
def interaction(voice):
    i = MagicMock()
    i.user = MagicMock(spec=discord.Member)
    i.user.mention = "<@42>"
    i.user.voice.channel = voice.channel
    i.response.send_message = AsyncMock()
    i.response.edit_message = AsyncMock()
    i.response.defer = AsyncMock()
    i.followup.send = AsyncMock()
    i.channel = MagicMock(spec=discord.TextChannel)
    return i


@pytest.fixture
def no_lyrics(monkeypatch):
    monkeypatch.setattr(now_playing_view, "_lyrics", None)


@pytest.fixture
async def view(player, track_factory, no_lyrics):
    return NowPlayingControls(player, track_factory("Song"), MagicMock())


def reply(interaction) -> tuple[str, bool]:
    """The text of the interaction's reply, and whether it was ephemeral."""
    call = interaction.response.send_message.await_args
    return call.kwargs["embed"].description, call.kwargs.get("ephemeral", False)


def button(view: NowPlayingControls, emoji: str) -> discord.ui.Button:
    return next(b for b in view.children
                if isinstance(b, discord.ui.Button) and str(b.emoji) == emoji)


async def press(view: NowPlayingControls, emoji: str, interaction) -> None:
    b = button(view, emoji)
    await b.callback(interaction)


# -- who may press ------------------------------------------------------

async def test_a_listener_in_the_bots_channel_may_press(view, interaction):
    assert await view.interaction_check(interaction) is True


async def test_someone_outside_the_voice_channel_is_refused(view, interaction):
    interaction.user.voice.channel = MagicMock(name="another-channel")
    assert await view.interaction_check(interaction) is False
    text, ephemeral = reply(interaction)
    assert ephemeral and "voice channel" in text


async def test_someone_not_in_voice_at_all_is_refused(view, interaction):
    interaction.user.voice = None
    assert await view.interaction_check(interaction) is False


async def test_a_destroyed_player_refuses_every_press(view, interaction):
    view.player.destroy()
    assert await view.interaction_check(interaction) is False
    text, ephemeral = reply(interaction)
    assert ephemeral and "ended" in text


# -- each button ----------------------------------------------------------

async def test_pause_edits_the_message_and_flips_the_button(view, interaction, voice):
    await press(view, "⏸", interaction)
    voice.pause.assert_called_once()
    interaction.response.edit_message.assert_awaited_once()
    assert str(view.pause_button.emoji) == "▶️"


async def test_pause_again_resumes(view, interaction, voice):
    await press(view, "⏸", interaction)
    voice.is_playing.return_value = False
    voice.is_paused.return_value = True
    await press(view, "▶️", interaction)
    voice.resume.assert_called_once()
    assert str(view.pause_button.emoji) == "⏸"


async def test_skip_is_announced_publicly_with_who_pressed(view, interaction, voice):
    await press(view, "⏭", interaction)
    voice.stop.assert_called_once()
    text, ephemeral = reply(interaction)
    assert not ephemeral and "Skipped" in text and "<@42>" in text


async def test_skip_with_nothing_playing_is_a_private_error(view, interaction, voice):
    voice.is_playing.return_value = False
    await press(view, "⏭", interaction)
    text, ephemeral = reply(interaction)
    assert ephemeral and "Nothing is playing" in text


async def test_previous_with_no_history_is_a_private_error(view, interaction):
    await press(view, "⏮", interaction)
    text, ephemeral = reply(interaction)
    assert ephemeral and "No previous track" in text


async def test_stop_destroys_the_player(view, interaction):
    await press(view, "⏹", interaction)
    assert view.player.is_destroyed
    text, ephemeral = reply(interaction)
    assert not ephemeral and "Disconnected" in text


async def test_loop_cycles_and_shows_the_mode_in_place(view, interaction):
    await press(view, "🔁", interaction)
    assert view.player.loop_mode == "track"
    embed = interaction.response.edit_message.await_args.kwargs["embed"]
    assert any(f.value == "Track" for f in embed.fields)


async def test_shuffle_an_empty_queue_is_a_private_error(view, interaction):
    await press(view, "🔀", interaction)
    text, ephemeral = reply(interaction)
    assert ephemeral and "empty" in text


async def test_queue_is_shown_only_to_who_asked(view, interaction):
    await press(view, "📜", interaction)
    call = interaction.response.send_message.await_args
    assert call.kwargs["ephemeral"] is True
    assert "Queue" in call.kwargs["embed"].title


# -- lyrics -------------------------------------------------------------------

async def test_no_lyrics_button_without_a_provider(view):
    assert all(str(getattr(b, "emoji", "")) != "🎤" for b in view.children)


async def test_lyrics_button_hands_the_channel_to_the_provider(
        player, track_factory, interaction, monkeypatch):
    provider = AsyncMock(return_value=True)
    monkeypatch.setattr(now_playing_view, "_lyrics", provider)
    view = NowPlayingControls(player, track_factory("Song"), MagicMock())
    await press(view, "🎤", interaction)
    provider.assert_awaited_once_with(interaction.channel, player)
    interaction.followup.send.assert_not_awaited()


async def test_lyrics_not_found_is_a_private_error(
        player, track_factory, interaction, monkeypatch):
    monkeypatch.setattr(now_playing_view, "_lyrics", AsyncMock(return_value=False))
    view = NowPlayingControls(player, track_factory("Song"), MagicMock())
    await press(view, "🎤", interaction)
    call = interaction.followup.send.await_args
    assert call.kwargs["ephemeral"] is True
    assert "Couldn't find lyrics" in call.kwargs["embed"].description


def test_registering_and_clearing_the_provider(monkeypatch):
    monkeypatch.setattr(now_playing_view, "_lyrics", None)
    provider = AsyncMock()
    now_playing_view.set_lyrics_provider(provider)
    assert now_playing_view._lyrics is provider
    now_playing_view.set_lyrics_provider(None)
    assert now_playing_view._lyrics is None


# -- retiring -------------------------------------------------------------------

async def test_retire_disables_every_button_and_stops_the_view(view):
    view.retire()
    assert all(item.disabled for item in view.children)
    assert view.is_finished()
