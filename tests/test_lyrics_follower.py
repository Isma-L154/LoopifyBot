"""
The follower: one message kept in step with the music.

Driven by a scripted clock rather than real time, so the loop runs to completion
in microseconds. Nothing here touches Discord or LRCLIB — the follower is given
a message to edit and a way to load lyrics, and both are plain fakes.
"""

import discord
import pytest

from cogs.lyrics import MAX_SLEEP, MIN_EDIT_INTERVAL, LyricsFollower
from services.synced_lyrics import Lyrics

SONG = Lyrics(title="Song", artist="Artist",
              lines=((0.0, "one"), (10.0, "two"), (20.0, "three"), (30.0, "four")))
OTHER = Lyrics(title="Other", artist="Artist", lines=((0.0, "otra"), (5.0, "linea")))
UNSYNCED = Lyrics(title="Song", artist="Artist", plain="just words")


def shown_text(edit: dict) -> str:
    """The lyrics an edit actually rendered, not the repr of the Embed object."""
    return edit["embed"].description or ""


class FakeMessage:
    def __init__(self, raises: Exception | None = None) -> None:
        self.edits: list[dict] = []
        self._raises = raises

    async def edit(self, **kwargs) -> None:
        if self._raises is not None:
            raise self._raises
        self.edits.append(kwargs)


class FakePlayer:
    def __init__(self, current=None, position: float = 0.0, rate: float = 1.0) -> None:
        self.current = current
        self.position = position
        self.effect_rate = rate
        self.is_destroyed = False


class Conductor:
    """Advances the fake clock and position, then stops the follower."""

    def __init__(self, player: FakePlayer, script: list) -> None:
        self.player = player
        self.script = list(script)
        self.now = 1000.0
        self.slept: list[float] = []
        self.follower: LyricsFollower | None = None

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += max(seconds, 0.01)
        if not self.script:
            return self.follower.stop()
        step = self.script.pop(0)
        if callable(step):
            step(self.player)
        else:
            self.player.position = step

    def clock(self) -> float:
        return self.now


def build(player, script, *, lyrics=SONG, message=None):
    """A follower wired to a scripted clock, ready to run."""
    loaded = []

    async def load(track):
        loaded.append(track)
        return lyrics(track) if callable(lyrics) else lyrics

    conductor = Conductor(player, script)
    follower = LyricsFollower(message or FakeMessage(), player, load,
                              sleep=conductor.sleep, now=conductor.clock)
    conductor.follower = follower
    return follower, conductor, loaded


# -- following ---------------------------------------------------------

async def test_it_edits_when_the_line_changes():
    player = FakePlayer(current={"t": 1}, position=0.0)
    follower, conductor, _ = build(player, [15.0, 25.0])

    await follower.run()

    assert len(follower.message.edits) == 3, "one per line reached"


async def test_it_does_not_edit_while_the_same_line_plays():
    """Editing on every wakeup would burn the channel's rate limit for nothing."""
    player = FakePlayer(current={"t": 1}, position=0.0)
    follower, _, _ = build(player, [2.0, 4.0, 6.0, 8.0])

    await follower.run()

    assert len(follower.message.edits) == 1, "the line never changed"


async def test_the_minimum_interval_stays_under_the_wakeup_cap():
    """
    A threshold above the longest sleep would delay every ordinary line change
    by a whole wakeup, which on lyrics is plainly visible.
    """
    assert MIN_EDIT_INTERVAL < MAX_SLEEP


async def test_edits_are_spaced_out():
    """
    Discord allows roughly five edits per five seconds per channel, and a fast
    song changes lines more often than that.
    """
    player = FakePlayer(current={"t": 1}, position=0.0)
    fast = Lyrics(title="F", artist="A",
                  lines=tuple((i * 0.5, f"line {i}") for i in range(20)))
    follower, _, _ = build(player, [0.5, 1.0, 1.5, 2.0, 2.5], lyrics=fast)

    await follower.run()

    assert len(follower.message.edits) <= 2, "the minimum interval was ignored"


async def test_it_picks_up_the_next_track():
    player = FakePlayer(current={"t": 1}, position=0.0)

    def switch(p):
        p.current = {"t": 2}
        p.position = 0.0

    follower, _, loaded = build(
        player, [switch, 6.0],
        lyrics=lambda track: SONG if track["t"] == 1 else OTHER)

    await follower.run()

    assert loaded == [{"t": 1}, {"t": 2}], "the new track's lyrics were fetched"
    assert any("otra" in shown_text(edit) for edit in follower.message.edits),         "the message never showed the new song"


async def test_an_unsynced_track_is_not_followed():
    """Nothing to sync to, so it waits quietly rather than editing on a loop."""
    player = FakePlayer(current={"t": 1}, position=0.0)
    follower, _, _ = build(player, [5.0, 10.0], lyrics=UNSYNCED)

    await follower.run()

    assert follower.message.edits == []


# -- stopping ----------------------------------------------------------

async def test_it_stops_when_the_player_is_destroyed():
    player = FakePlayer(current={"t": 1}, position=0.0)

    def destroy(p):
        p.is_destroyed = True

    follower, conductor, _ = build(player, [destroy, 15.0, 25.0, 35.0])

    await follower.run()

    assert len(conductor.script) > 0, "it kept running after the player died"


async def test_it_stops_when_the_music_stops():
    player = FakePlayer(current={"t": 1}, position=0.0)

    def clear(p):
        p.current = None

    follower, conductor, _ = build(player, [clear, 15.0, 25.0])

    await follower.run()

    assert len(conductor.script) > 0


async def test_a_deleted_message_ends_it_quietly():
    """Someone tidying the channel must not leave a task spinning forever."""
    player = FakePlayer(current={"t": 1}, position=0.0)
    gone = FakeMessage(raises=discord.NotFound(_response(404), "gone"))
    follower, conductor, _ = build(player, [15.0, 25.0], message=gone)

    await follower.run()          # must not raise

    assert len(conductor.script) > 0


async def test_stop_is_idempotent():
    player = FakePlayer(current={"t": 1}, position=0.0)
    follower, _, _ = build(player, [])

    follower.stop()
    follower.stop()

    await follower.run()


# -- how long it waits -------------------------------------------------

async def test_it_sleeps_until_the_next_line_not_on_a_tick():
    """Polling would burn CPU on a small box for no benefit."""
    player = FakePlayer(current={"t": 1}, position=0.0)
    follower, conductor, _ = build(player, [15.0])

    await follower.run()

    assert conductor.slept[0] == pytest.approx(MAX_SLEEP), \
        "the next line is 10s away, so it waits the cap and re-checks"


async def test_the_wait_never_exceeds_the_cap():
    """A pause or a skip has to be noticed promptly, even mid-verse."""
    player = FakePlayer(current={"t": 1}, position=0.0)
    sparse = Lyrics(title="S", artist="A", lines=((0.0, "one"), (600.0, "much later")))
    follower, conductor, _ = build(player, [1.0, 2.0], lyrics=sparse)

    await follower.run()

    assert all(seconds <= MAX_SLEEP for seconds in conductor.slept)


async def test_the_wait_accounts_for_a_speed_effect():
    """At 1.25x a line ten song-seconds away arrives in eight real seconds."""
    player = FakePlayer(current={"t": 1}, position=0.0, rate=1.25)
    near = Lyrics(title="N", artist="A", lines=((0.0, "one"), (1.25, "two")))
    follower, conductor, _ = build(player, [1.5], lyrics=near)

    await follower.run()

    assert conductor.slept[0] == pytest.approx(1.0), "1.25 song-seconds at 1.25x"


def _response(status: int):
    """Minimal stand-in for the aiohttp response discord.NotFound wants."""
    class R:
        def __init__(self) -> None:
            self.status = status
            self.reason = "Not Found"
    return R()
