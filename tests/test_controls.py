"""
The playback actions shared by commands and Now Playing buttons.

Each action is checked with no player, with an idle one and with one that is
playing, since the buttons can be pressed in any of those states.
"""

from unittest.mock import MagicMock

import pytest

from utils import controls


@pytest.fixture
def voice(fake_guild):
    vc = MagicMock()
    vc.is_playing.return_value = True
    vc.is_paused.return_value = False
    fake_guild.voice_client = vc
    return vc


def test_pause_a_playing_track(player, voice):
    outcome = controls.pause(player)
    assert outcome.ok and "Paused" in outcome.message
    voice.pause.assert_called_once()


def test_resume_a_paused_track(player, voice):
    voice.is_playing.return_value = False
    voice.is_paused.return_value = True
    outcome = controls.resume(player)
    assert outcome.ok and "Resumed" in outcome.message
    voice.resume.assert_called_once()


@pytest.mark.parametrize("action", [controls.pause, controls.skip])
def test_actions_on_an_idle_player_report_nothing_playing(player, action):
    outcome = action(player)
    assert not outcome.ok and "Nothing is playing" in outcome.message


@pytest.mark.parametrize("action", [
    controls.pause, controls.resume, controls.skip,
    controls.previous, controls.shuffle, controls.cycle_loop, controls.stop,
])
def test_actions_without_a_player_fail_politely(action):
    assert not action(None).ok


def test_resume_when_nothing_is_paused(player):
    assert controls.resume(player).message == "Nothing is paused."


def test_skip_a_playing_track(player, voice):
    outcome = controls.skip(player)
    assert outcome.ok and "Skipped" in outcome.message
    voice.stop.assert_called_once()


def test_previous_with_no_history(player):
    outcome = controls.previous(player)
    assert not outcome.ok and "No previous track" in outcome.message


def test_previous_with_history(player, track_factory):
    player.history.append(track_factory("Earlier"))
    assert controls.previous(player).ok


def test_shuffle_an_empty_queue(player):
    outcome = controls.shuffle(player)
    assert not outcome.ok and "empty" in outcome.message


def test_shuffle_a_queue(player, track_factory):
    player.add_many([track_factory(str(i)) for i in range(5)])
    assert controls.shuffle(player).ok
    assert len(player.queue) == 5


@pytest.mark.parametrize("start, expected", [
    ("off", "track"), ("track", "queue"), ("queue", "off"),
])
def test_cycle_loop_walks_every_mode(player, start, expected):
    player.loop_mode = start
    outcome = controls.cycle_loop(player)
    assert outcome.ok and player.loop_mode == expected
    assert expected in outcome.message


def test_set_loop(player):
    assert controls.set_loop(player, "queue").ok
    assert player.loop_mode == "queue"


def test_stop_destroys_the_player(player):
    outcome = controls.stop(player)
    assert outcome.ok and player.is_destroyed
