"""
Logging in survives a DNS outage.

On 2026-09-11 an unattended-upgrade replaced glibc — which *is* the DNS
resolver — and needrestart restarted the bot in the middle of that window.
`getaddrinfo("discord.com")` returned EAI_AGAIN, `bot.start()` propagated it,
and the process exited 1. systemd restarted it twice before one attempt landed.

Only the login half needs this. `Client.connect(reconnect=True)` already catches
`aiohttp.ClientError` and retries with backoff on its own; `Client.login()` has
no retry at all, which is why the failure was fatal.
"""

import asyncio
import socket
from unittest.mock import MagicMock

import aiohttp
import discord
import pytest
from aiohttp.client_exceptions import ClientConnectorDNSError

from utils.startup import LOGIN_RETRY_DELAYS, login_with_retry, start

TOKEN = "not-a-real-token"


def dns_failure() -> ClientConnectorDNSError:
    """The exact exception that killed the bot on 2026-09-11 at 06:18 UTC."""
    return ClientConnectorDNSError(
        MagicMock(host="discord.com", port=443, ssl=True),
        socket.gaierror(-3, "Temporary failure in name resolution"),
    )


def http_error(status: int) -> discord.HTTPException:
    response = MagicMock()
    response.status = status
    response.reason = "because"
    return discord.HTTPException(response, {"message": "nope", "code": 0})


class FakeHTTP:
    """Stands in for ``bot.http``, recording how often its session was closed."""

    def __init__(self) -> None:
        self.closes = 0

    async def close(self) -> None:
        self.closes += 1


class FakeBot:
    """
    A client whose ``login`` replays a scripted list of outcomes.

    An entry that is an exception is raised; anything else counts as a
    successful login.
    """

    def __init__(self, *outcomes) -> None:
        self._outcomes = list(outcomes)
        self.attempts = 0
        self.http = FakeHTTP()
        self.calls: list[str] = []
        self.connect_kwargs: dict = {}

    async def login(self, token: str) -> None:
        assert token == TOKEN
        self.attempts += 1
        self.calls.append("login")
        outcome = self._outcomes.pop(0) if self._outcomes else None
        if isinstance(outcome, BaseException):
            raise outcome

    async def connect(self, **kwargs) -> None:
        self.calls.append("connect")
        self.connect_kwargs = kwargs


@pytest.fixture
def slept():
    """Replaces the real sleep, recording the delays that were waited."""
    delays: list[float] = []

    async def _sleep(seconds: float) -> None:
        delays.append(seconds)

    _sleep.delays = delays
    return _sleep


# -- the incident ------------------------------------------------------

async def test_a_dns_failure_is_retried_until_it_succeeds(slept):
    bot = FakeBot(dns_failure(), dns_failure(), None)

    await login_with_retry(bot, TOKEN, sleep=slept)

    assert bot.attempts == 3, "it must keep trying while DNS is down"


async def test_a_dns_failure_no_longer_kills_the_process(slept):
    """The regression itself: one blip used to propagate out of main()."""
    bot = FakeBot(dns_failure(), None)
    await login_with_retry(bot, TOKEN, sleep=slept)


async def test_it_waits_between_attempts_with_growing_delays(slept):
    bot = FakeBot(dns_failure(), dns_failure(), dns_failure(), None)

    await login_with_retry(bot, TOKEN, sleep=slept)

    assert slept.delays == list(LOGIN_RETRY_DELAYS[:3])
    assert slept.delays == sorted(slept.delays), "delays must back off, not shrink"


async def test_the_stale_session_is_closed_before_each_retry(slept):
    """
    ``HTTPClient.static_login`` builds a fresh ``aiohttp.ClientSession`` every
    call and drops the previous one on the floor. Without closing it, each retry
    leaks a session — which on a 768 MB box is exactly the kind of slow leak the
    project brief forbids.
    """
    bot = FakeBot(dns_failure(), dns_failure(), None)

    await login_with_retry(bot, TOKEN, sleep=slept)

    assert bot.http.closes == 2, "one close per discarded session"


async def test_nothing_is_closed_when_the_first_attempt_works(slept):
    bot = FakeBot(None)

    await login_with_retry(bot, TOKEN, sleep=slept)

    assert bot.http.closes == 0
    assert slept.delays == []


# -- what must NOT be retried ------------------------------------------

async def test_a_bad_token_fails_immediately(slept):
    """Waiting cannot fix a wrong token, and retrying risks a login rate-limit."""
    bot = FakeBot(discord.LoginFailure("Improper token has been passed."))

    with pytest.raises(discord.LoginFailure):
        await login_with_retry(bot, TOKEN, sleep=slept)

    assert bot.attempts == 1
    assert slept.delays == []


async def test_a_client_side_http_error_is_not_retried(slept):
    """A 4xx is our fault and stays our fault."""
    bot = FakeBot(http_error(403))

    with pytest.raises(discord.HTTPException):
        await login_with_retry(bot, TOKEN, sleep=slept)

    assert bot.attempts == 1


async def test_a_discord_outage_is_retried(slept):
    """A 5xx is Discord's problem and usually passes."""
    bot = FakeBot(http_error(503), None)

    await login_with_retry(bot, TOKEN, sleep=slept)

    assert bot.attempts == 2


# -- giving up ---------------------------------------------------------

async def test_it_gives_up_and_reraises_when_the_outage_outlasts_the_retries(slept):
    """
    systemd is still the backstop. Retrying forever would leave a bot that looks
    alive to `systemctl` while never being usable, and never trips OnFailure.
    """
    last = dns_failure()
    bot = FakeBot(*[dns_failure() for _ in LOGIN_RETRY_DELAYS], last)

    with pytest.raises(ClientConnectorDNSError) as caught:
        await login_with_retry(bot, TOKEN, sleep=slept)

    assert caught.value is last, "the final failure is what the operator sees"
    assert bot.attempts == len(LOGIN_RETRY_DELAYS) + 1
    assert len(slept.delays) == len(LOGIN_RETRY_DELAYS)


async def test_the_retry_budget_is_survivable_but_not_endless():
    """Long enough to outlast a package upgrade, short enough to surface."""
    assert 60 <= sum(LOGIN_RETRY_DELAYS) <= 600
    assert len(LOGIN_RETRY_DELAYS) >= 3


# -- transient classification -------------------------------------------

@pytest.mark.parametrize("error", [
    dns_failure(),
    OSError(101, "Network is unreachable"),
    asyncio.TimeoutError(),
    discord.GatewayNotFound(),
    http_error(500),
    http_error(502),
])
async def test_transient_failures_are_retried(slept, error):
    bot = FakeBot(error, None)
    await login_with_retry(bot, TOKEN, sleep=slept)
    assert bot.attempts == 2


@pytest.mark.parametrize("error", [
    discord.LoginFailure("bad token"),
    discord.PrivilegedIntentsRequired(shard_id=None),
    http_error(400),
    http_error(401),
    TypeError("expected token to be a str"),
])
async def test_permanent_failures_are_raised_at_once(slept, error):
    bot = FakeBot(error)
    with pytest.raises(type(error)):
        await login_with_retry(bot, TOKEN, sleep=slept)
    assert bot.attempts == 1


# -- handing off to the gateway ----------------------------------------

async def test_start_logs_in_before_connecting(slept):
    bot = FakeBot(None)

    await start(bot, TOKEN, sleep=slept)

    assert bot.calls == ["login", "connect"]


async def test_start_lets_discord_py_own_the_gateway_reconnects(slept):
    """
    ``connect(reconnect=True)`` already retries the gateway with its own
    backoff. Wrapping it in ours as well would stack two retry policies on the
    same failure and double every wait.
    """
    bot = FakeBot(None)

    await start(bot, TOKEN, sleep=slept)

    assert bot.connect_kwargs == {"reconnect": True}


async def test_start_retries_the_login_before_connecting(slept):
    bot = FakeBot(dns_failure(), None)

    await start(bot, TOKEN, sleep=slept)

    assert bot.calls == ["login", "login", "connect"]


async def test_start_never_connects_when_the_login_never_lands(slept):
    bot = FakeBot(*[dns_failure() for _ in range(len(LOGIN_RETRY_DELAYS) + 1)])

    with pytest.raises(ClientConnectorDNSError):
        await start(bot, TOKEN, sleep=slept)

    assert "connect" not in bot.calls


# -- against a real client, with real aiohttp sessions ------------------

async def test_a_real_login_retry_leaves_no_unclosed_session(monkeypatch):
    """
    The leak check, with nothing faked but DNS.

    Every ``static_login`` opens a real ``aiohttp.ClientSession``. This drives
    three real attempts against a resolver that always fails and asserts that no
    session is left open behind them — the invariant the FakeHTTP test can only
    describe.
    """
    import gc
    import socket as socket_module

    def no_dns(*args, **kwargs):
        raise socket_module.gaierror(-3, "Temporary failure in name resolution")

    monkeypatch.setattr(socket_module, "getaddrinfo", no_dns)

    def live_sessions() -> set[int]:
        return {id(o) for o in gc.get_objects()
                if isinstance(o, aiohttp.ClientSession) and not o.closed}

    before = live_sessions()
    client = discord.Client(intents=discord.Intents.none())
    try:
        with pytest.raises(aiohttp.ClientError):
            await login_with_retry(client, TOKEN, delays=(0.0, 0.0), sleep=_no_wait)
    finally:
        await client.http.close()       # the final attempt's session
        await client.close()

    gc.collect()
    assert live_sessions() - before == set(), "a retry left an aiohttp session open"


async def _no_wait(seconds: float) -> None:
    """A sleep that does not."""
