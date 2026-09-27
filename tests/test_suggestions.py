"""Search suggestions for `/play`: best effort, and never an error."""

import asyncio

from services import suggestions
from tests.test_synced_lyrics import FakeResponse, FakeSession

PAYLOAD = ["bohemian", ["bohemian rhapsody", "bohemian rhapsody live"], [], {}]


async def test_returns_the_suggested_terms():
    session = FakeSession(FakeResponse(200, PAYLOAD))
    assert await suggestions.fetch(session, "bohemian") == [
        "bohemian rhapsody", "bohemian rhapsody live"]
    assert session.calls[0]["params"]["q"] == "bohemian"


async def test_urls_and_blank_input_are_not_looked_up():
    session = FakeSession(FakeResponse(200, PAYLOAD))
    assert await suggestions.fetch(session, "https://youtu.be/x") == []
    assert await suggestions.fetch(session, "   ") == []
    assert session.calls == []


async def test_caps_the_number_and_length_of_suggestions():
    long = ["x" * 150] + [f"s{i}" for i in range(30)]
    session = FakeSession(FakeResponse(200, ["q", long]))
    found = await suggestions.fetch(session, "q")
    assert len(found) == suggestions.MAX_SUGGESTIONS
    assert all(len(s) <= suggestions.MAX_CHOICE_LEN for s in found)


async def test_a_failing_endpoint_yields_nothing():
    for session in (
        FakeSession(FakeResponse(503)),
        FakeSession(OSError("connection refused")),
        FakeSession(asyncio.TimeoutError()),
        FakeSession(FakeResponse(200, raises=ValueError("not json"))),
        FakeSession(FakeResponse(200, {"unexpected": "shape"})),
        FakeSession(FakeResponse(200, ["q", "not a list"])),
    ):
        assert await suggestions.fetch(session, "q") == []
