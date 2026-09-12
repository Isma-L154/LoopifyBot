"""
Shutting down inside the time systemd allows.

Issue #35: a restart of a bot that had joined a voice channel timed out, got
SIGKILLed, and tripped the OnFailure alert. The cause is arithmetic, not a race.

``Client.close()`` disconnects every voice client, and discord.py reuses the
*connect* timeout as the deadline for Discord to confirm the departure:

    # discord/voice_state.py
    await asyncio.wait_for(self._disconnected.wait(), timeout=self.timeout)

This bot connects with ``timeout=20`` and the unit allowed ``TimeoutStopSec=15``,
so a shutdown that was merely being patient was killed 5 seconds early — and
because all of it happens inside ``async with bot``, the ``except
KeyboardInterrupt`` handler never ran, which is why no shutdown line was logged.

A bot that never joined voice has no voice client to wait on, which is why every
other shutdown looked clean.
"""

import asyncio
import os
import re
import signal
import sys
from pathlib import Path

import pytest

from cogs.music import VOICE_CONNECT_TIMEOUT
from utils.startup import VOICE_DISCONNECT_TIMEOUT, leave_voice, serve

TOKEN = "not-a-real-token"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class FakeVoice:
    """A voice client that can disconnect, hang forever, or fail."""

    def __init__(self, *, hangs: bool = False, raises: Exception | None = None) -> None:
        self.hangs = hangs
        self.raises = raises
        self.disconnected = False
        self.forced: bool | None = None

    async def disconnect(self, *, force: bool) -> None:
        self.forced = force
        if self.raises is not None:
            raise self.raises
        if self.hangs:
            await asyncio.sleep(3600)
        self.disconnected = True


class FakeBot:
    def __init__(self, *voices, login_error: Exception | None = None) -> None:
        self.voice_clients = list(voices)
        self._login_error = login_error
        self.calls: list[str] = []

    async def login(self, token: str) -> None:
        self.calls.append("login")
        if self._login_error is not None:
            raise self._login_error

    async def connect(self, **kwargs) -> None:
        self.calls.append("connect")
        await asyncio.sleep(3600)        # a real gateway loop never returns


# -- leaving voice, bounded --------------------------------------------

async def test_every_voice_client_is_disconnected():
    first, second = FakeVoice(), FakeVoice()

    await leave_voice(FakeBot(first, second))

    assert first.disconnected and second.disconnected
    assert first.forced is True, "a shutdown is not the moment to be polite"


async def test_a_disconnect_that_never_returns_is_abandoned():
    """The regression itself: this used to hang past systemd's patience."""
    stuck = FakeVoice(hangs=True)

    await asyncio.wait_for(leave_voice(FakeBot(stuck), timeout=0.05), timeout=2.0)

    assert stuck.forced is True, "it was asked to leave"
    assert not stuck.disconnected, "and it never confirmed"


async def test_one_stuck_client_does_not_strand_the_others():
    stuck, healthy = FakeVoice(hangs=True), FakeVoice()

    await leave_voice(FakeBot(stuck, healthy), timeout=0.05)

    assert healthy.disconnected


async def test_a_failing_disconnect_does_not_stop_the_rest():
    broken, healthy = FakeVoice(raises=RuntimeError("gateway gone")), FakeVoice()

    await leave_voice(FakeBot(broken, healthy), timeout=0.05)

    assert healthy.disconnected


async def test_a_bot_that_never_joined_voice_has_nothing_to_do():
    await leave_voice(FakeBot())


# -- serve: run, then shut down ----------------------------------------

async def test_serve_returns_when_asked_to_stop():
    stop = asyncio.Event()
    bot = FakeBot()
    stop.set()

    await asyncio.wait_for(serve(bot, TOKEN, stop=stop), timeout=2.0)


async def test_serve_leaves_voice_on_the_way_out():
    """
    The whole point: voice is released in our own bounded step, so ``bot.close()``
    finds nothing left to wait 20 seconds for.
    """
    voice = FakeVoice()
    stop = asyncio.Event()
    stop.set()

    await asyncio.wait_for(serve(FakeBot(voice), TOKEN, stop=stop), timeout=2.0)

    assert voice.disconnected


async def test_serve_leaves_voice_even_when_a_stuck_client_will_not_go():
    stuck = FakeVoice(hangs=True)
    stop = asyncio.Event()
    stop.set()

    await asyncio.wait_for(
        serve(FakeBot(stuck), TOKEN, stop=stop, voice_timeout=0.05), timeout=2.0)

    assert stuck.forced is True


async def test_serve_propagates_a_login_failure():
    """A broken deployment must still reach systemd."""
    import discord

    bot = FakeBot(login_error=discord.LoginFailure("bad token"))

    with pytest.raises(discord.LoginFailure):
        await asyncio.wait_for(serve(bot, TOKEN, stop=asyncio.Event()), timeout=2.0)


async def test_serve_still_releases_voice_when_the_login_fails():
    import discord

    voice = FakeVoice()
    bot = FakeBot(voice, login_error=discord.LoginFailure("bad token"))

    with pytest.raises(discord.LoginFailure):
        await asyncio.wait_for(serve(bot, TOKEN, stop=asyncio.Event()), timeout=2.0)

    assert voice.disconnected


# -- the arithmetic that caused #35 ------------------------------------

def unit_setting(name: str) -> float:
    """Read a systemd setting out of the deploy script that writes the unit."""
    script = (PROJECT_ROOT / "deploy" / "install-units.sh").read_text(encoding="utf-8")
    match = re.search(rf"^{name}=(\d+)", script, re.MULTILINE)
    assert match, f"{name} is not set in deploy/install-units.sh"
    return float(match.group(1))


def test_systemd_waits_longer_than_a_voice_disconnect_can():
    """
    The bug, as a test. discord.py can spend the whole connect timeout waiting
    for Discord to confirm a voice departure, and does it for every voice client
    while closing. If the unit allows less than that, systemd kills a shutdown
    that was going to finish — taking the child reaping with it.
    """
    assert unit_setting("TimeoutStopSec") > VOICE_CONNECT_TIMEOUT


def test_our_own_voice_teardown_is_far_inside_that_budget():
    """Normal shutdowns must be quick, not merely survivable."""
    assert VOICE_DISCONNECT_TIMEOUT * 2 < unit_setting("TimeoutStopSec")


# -- the real signal path ----------------------------------------------

@pytest.mark.skipif(sys.platform == "win32",
                    reason="loop.add_signal_handler is POSIX-only")
async def test_a_real_stop_signal_ends_serve():
    """
    Production never injects the event — systemd sends a signal.

    Worth its own test because ``add_signal_handler`` is POSIX-only, so the
    path that actually runs on the server is the one the other tests skip. If
    the handler were not installed, this SIGTERM would kill the test run
    outright rather than fail it.
    """
    voice = FakeVoice()
    task = asyncio.create_task(serve(FakeBot(voice), TOKEN))
    await asyncio.sleep(0.1)                  # let the handler be installed

    os.kill(os.getpid(), signal.SIGTERM)

    await asyncio.wait_for(task, timeout=3.0)
    assert voice.disconnected, "a signalled shutdown must release voice too"
