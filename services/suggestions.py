"""
Search suggestions for `/play`, from YouTube's autocomplete endpoint.

The endpoint is unofficial and undocumented, so everything here is best
effort: a slow, changed or unreachable endpoint yields no suggestions, never an
error. Discord drops an autocomplete answer after 3 seconds, hence the short
timeout.
"""

import logging
from typing import Any

import aiohttp

log = logging.getLogger("loopify.suggestions")

SUGGEST_URL = "https://suggestqueries.google.com/complete/search"
REQUEST_TIMEOUT = 1.5
MAX_SUGGESTIONS = 10
MAX_CHOICE_LEN = 100        # Discord's cap on a choice's name and value


async def fetch(session: aiohttp.ClientSession, query: str) -> list[str]:
    """Up to MAX_SUGGESTIONS search terms completing ``query``."""
    query = query.strip()
    if not query or query.startswith("http"):
        return []
    try:
        async with session.get(
            SUGGEST_URL,
            # client=firefox answers plain JSON; ds=yt restricts to YouTube.
            params={"client": "firefox", "ds": "yt", "q": query},
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
        ) as response:
            if response.status != 200:
                return []
            payload = await response.json(content_type=None)
    except Exception as e:
        log.debug("Suggestions failed for %r: %s", query, e)
        return []
    return _terms(payload)


def _terms(payload: Any) -> list[str]:
    """The suggestions from ``[query, [term, ...], ...]``, or none if it isn't that."""
    if not (isinstance(payload, list) and len(payload) > 1
            and isinstance(payload[1], list)):
        return []
    terms = [t[:MAX_CHOICE_LEN] for t in payload[1] if isinstance(t, str) and t]
    return terms[:MAX_SUGGESTIONS]
