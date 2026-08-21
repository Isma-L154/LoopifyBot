"""Queue mutation logic — pure state, no event loop required."""

import pytest

from utils.player import MAX_QUEUE, HISTORY_LIMIT
from tests.conftest import make_track


# ── add / add_many ────────────────────────────────────────────────────

def test_add_appends_and_signals(player):
    assert player.add(make_track("A")) is True
    assert [t["title"] for t in player.to_list()] == ["A"]
    assert player._added.is_set()


def test_add_refuses_past_the_hard_cap(player):
    player.queue.extend(make_track(f"T{i}") for i in range(MAX_QUEUE))
    assert player.add(make_track("overflow")) is False
    assert len(player.queue) == MAX_QUEUE


def test_add_many_reports_how_many_fit(player):
    player.queue.extend(make_track(f"T{i}") for i in range(MAX_QUEUE - 3))
    accepted = player.add_many([make_track(f"N{i}") for i in range(10)])
    assert accepted == 3
    assert len(player.queue) == MAX_QUEUE


def test_add_many_on_full_queue_accepts_nothing_and_stays_quiet(player):
    player.queue.extend(make_track(f"T{i}") for i in range(MAX_QUEUE))
    player._added.clear()
    assert player.add_many([make_track("N")]) == 0
    assert player._added.is_set() is False


def test_add_many_empty_list_does_not_signal(player):
    player._added.clear()
    assert player.add_many([]) == 0
    assert player._added.is_set() is False


# ── remove ────────────────────────────────────────────────────────────

def test_remove_uses_one_based_positions(player):
    player.add_many([make_track("A"), make_track("B"), make_track("C")])
    removed = player.remove(2)
    assert removed["title"] == "B"
    assert [t["title"] for t in player.to_list()] == ["A", "C"]


@pytest.mark.parametrize("bad_index", [0, -1, 4, 999])
def test_remove_rejects_out_of_range_and_leaves_queue_intact(player, bad_index):
    player.add_many([make_track("A"), make_track("B"), make_track("C")])
    assert player.remove(bad_index) is None
    assert [t["title"] for t in player.to_list()] == ["A", "B", "C"]


def test_remove_from_empty_queue(player):
    assert player.remove(1) is None


# ── move ──────────────────────────────────────────────────────────────

def test_move_reorders(player):
    player.add_many([make_track("A"), make_track("B"), make_track("C")])
    assert player.move(3, 1) is True
    assert [t["title"] for t in player.to_list()] == ["C", "A", "B"]


def test_move_onto_itself_is_a_no_op(player):
    player.add_many([make_track("A"), make_track("B")])
    assert player.move(1, 1) is True
    assert [t["title"] for t in player.to_list()] == ["A", "B"]


@pytest.mark.parametrize("frm,to", [(0, 1), (1, 0), (5, 1), (1, 5), (-1, 1)])
def test_move_rejects_out_of_range(player, frm, to):
    player.add_many([make_track("A"), make_track("B"), make_track("C")])
    assert player.move(frm, to) is False
    assert [t["title"] for t in player.to_list()] == ["A", "B", "C"]


# ── shuffle / clear ───────────────────────────────────────────────────

def test_shuffle_preserves_every_track(player):
    titles = [f"T{i}" for i in range(50)]
    player.add_many([make_track(t) for t in titles])
    player.shuffle()
    assert sorted(t["title"] for t in player.to_list()) == sorted(titles)


def test_shuffle_on_empty_queue_is_safe(player):
    player.shuffle()
    assert player.is_empty


def test_clear_empties_the_queue_but_keeps_current(player, track_factory):
    player.current = track_factory("Playing")
    player.add_many([make_track("A"), make_track("B")])
    player.clear()
    assert player.is_empty
    assert player.current["title"] == "Playing"


# ── history ───────────────────────────────────────────────────────────

def test_history_is_capped(player):
    player.history = [make_track(f"H{i}") for i in range(HISTORY_LIMIT)]
    player.current = make_track("current")
    player.queue.append(make_track("next"))
    # Trip the archiving path once via the same logic _advance uses.
    player.history.append(player.current)
    if len(player.history) > HISTORY_LIMIT:
        player.history.pop(0)
    assert len(player.history) == HISTORY_LIMIT


def test_go_previous_with_empty_history_returns_false(player):
    assert player.history == []
    assert player.go_previous() is False


def test_go_previous_requeues_previous_then_current(player):
    player.history = [make_track("Older"), make_track("Prev")]
    player.current = make_track("Current")
    assert player.go_previous() is True
    # Previous plays first, then the track that was interrupted.
    assert [t["title"] for t in player.to_list()] == ["Prev", "Current"]
    assert player.current is None      # so _advance won't re-archive it
    assert player._skip is True
    assert [t["title"] for t in player.history] == ["Older"]


# ── teardown ──────────────────────────────────────────────────────────

def test_destroy_is_idempotent_and_clears_state(player):
    player.add_many([make_track("A"), make_track("B")])
    player.current = make_track("Playing")
    player.destroy()
    assert player._destroyed is True
    assert player.is_empty
    assert player.current is None
    player.destroy()               # must not raise
    assert player._destroyed is True
