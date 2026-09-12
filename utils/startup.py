"""
Logging in to Discord without dying on a passing network failure.

``Client.start()`` is ``login()`` followed by ``connect()``. Only the first half
needs anything from us: ``connect(reconnect=True)`` already catches
``aiohttp.ClientError``, ``OSError`` and friends and retries with its own
backoff, so a gateway that drops mid-song recovers on its own. ``login()`` has
no retry whatsoever, so a resolver that is down for the few seconds the bot
happens to be starting takes the whole process with it.

That is not hypothetical: on 2026-09-11 an unattended-upgrade replaced glibc —
which *is* the DNS resolver — and needrestart restarted the bot inside that
window. Two starts died on ``EAI_AGAIN`` before a third one landed.
"""

import asyncio
import logging

import aiohttp
import discord

log = logging.getLogger("loopify.startup")

# Waits between login attempts, in seconds. The total has to outlast a package
# upgrade restarting the resolver, but stay short enough that a deployment which
# is genuinely broken still fails and trips systemd's OnFailure alert rather
# than sitting there looking alive.
LOGIN_RETRY_DELAYS = (5.0, 15.0, 30.0, 60.0, 120.0)


def is_transient(error: BaseException) -> bool:
    """
    Whether waiting could plausibly fix this.

    A wrong token or a missing privileged intent is a deployment mistake: it
    will fail identically forever, and hammering the login endpoint over it
    invites a rate-limit on top. Discord's own 5xx, a refused connection and a
    name that will not resolve are all things that pass.
    """
    if isinstance(error, (discord.LoginFailure, discord.PrivilegedIntentsRequired)):
        return False
    if isinstance(error, discord.HTTPException):
        return error.status >= 500
    return isinstance(error, (OSError, aiohttp.ClientError,
                              asyncio.TimeoutError, discord.GatewayNotFound))


async def _discard_session(http) -> None:
    """
    Release the aiohttp session a failed login left behind.

    Two details make this less obvious than it looks:

    * ``HTTPClient.static_login`` builds a new ``ClientSession`` on every call
      and abandons the previous one, so without closing it each retry leaks a
      session.
    * That session is handed the HTTPClient's connector, and aiohttp's default
      ``connector_owner=True`` means closing the session closes the *shared*
      connector too. ``ClientSession.closed`` is then True for every later
      session built on it, so the next attempt dies with "Session is closed"
      rather than retrying. Clearing the connector makes ``static_login`` build
      a fresh pair.
    """
    await http.close()
    http.connector = discord.utils.MISSING


async def login_with_retry(bot, token: str, *,
                           delays: tuple[float, ...] = LOGIN_RETRY_DELAYS,
                           sleep=asyncio.sleep) -> None:
    """
    Log ``bot`` in, retrying while the failure looks like weather.

    Raises the last error once the delays run out, so a lasting outage still
    reaches systemd instead of being swallowed.
    """
    for delay in (*delays, None):        # the trailing None is the last attempt
        try:
            await bot.login(token)
            return
        except Exception as error:
            if delay is None or not is_transient(error):
                raise
            await _discard_session(bot.http)
            log.warning("Login failed (%s: %s) — retrying in %.0fs",
                        type(error).__name__, error, delay)
            await sleep(delay)


async def start(bot, token: str, **retry_options) -> None:
    """
    Bring the bot online: a retried login, then discord.py's own gateway loop.

    The split matters. ``connect(reconnect=True)`` already retries gateway drops
    with its own backoff, so it is handed over untouched — wrapping it here too
    would stack two retry policies on one failure.
    """
    await login_with_retry(bot, token, **retry_options)
    await bot.connect(reconnect=True)
