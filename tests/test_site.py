"""
The landing site (site/public) must describe the bot that actually exists, and
must keep working under its own Content Security Policy.

The command list is hand-written HTML, so nothing but these tests stops it from
drifting away from the bot the way the old hardcoded !help did. And the CSP
blocks inline scripts and styles without any visible error, so an inline
`style=""` would break the live page silently.
"""

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import discord
import pytest
from discord.ext import commands

from config import COGS

SITE = Path(__file__).resolve().parent.parent / "site"
PUBLIC = SITE / "public"
PAGES = ["index.html", "404.html"]

# Registered on the bot in main.py rather than in a cog, and main.py exits on
# import without a token, so it cannot be loaded here.
BOT_LEVEL_HYBRID_COMMANDS = {"help"}

EXTERNAL = ("http://", "https://", "mailto:", "data:", "#")


class Page(HTMLParser):
    """Collects what the tests inspect from one HTML file."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.commands: dict[str, str] = {}
        self.aliases: dict[str, str] = {}
        self.json_ld: list[str] = []
        self._open: tuple[dict[str, str], str] | None = None
        self._in_json_ld = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        self.tags.append((tag, attributes))
        if tag == "code" and "data-command" in attributes:
            self._open = (self.commands, attributes["data-command"] or "")
        elif tag == "code" and "data-alias" in attributes:
            self._open = (self.aliases, attributes["data-alias"] or "")
        if tag == "script" and attributes.get("type") == "application/ld+json":
            self._in_json_ld = True
            self.json_ld.append("")

    def handle_endtag(self, tag: str) -> None:
        if tag == "code":
            self._open = None
        if tag == "script":
            self._in_json_ld = False

    def handle_data(self, data: str) -> None:
        if self._open is not None:
            target, name = self._open
            target[name] = target.get(name, "") + data
        if self._in_json_ld:
            self.json_ld[-1] += data

    def meta(self, key: str, value: str) -> str:
        for tag, attributes in self.tags:
            if tag == "meta" and attributes.get(key) == value:
                return attributes.get("content") or ""
        raise AssertionError(f"no <meta {key}={value!r}>")


def parse(name: str) -> Page:
    page = Page()
    page.feed((PUBLIC / name).read_text(encoding="utf-8"))
    return page


@pytest.fixture
async def bot():
    """A bot with the real cogs loaded, but no gateway connection."""
    b = commands.Bot(command_prefix="!", intents=discord.Intents.none(),
                     help_command=None)
    for cog in COGS:
        await b.load_extension(cog)
    return b


def public_commands(bot: commands.Bot) -> list[commands.Command]:
    return [c for c in bot.commands if not c.hidden]


async def test_the_page_lists_exactly_the_registered_commands(bot):
    listed = set(parse("index.html").commands)
    registered = {c.name for c in public_commands(bot)} | BOT_LEVEL_HYBRID_COMMANDS
    assert listed - registered == set(), "on the page but not in the bot"
    assert registered - listed == set(), "in the bot but not on the page"


async def test_the_page_lists_exactly_the_registered_aliases(bot):
    page = parse("index.html")
    registered = {a for c in public_commands(bot) for a in c.aliases}
    assert set(page.aliases) - registered == set(), "on the page but not in the bot"
    assert registered - set(page.aliases) == set(), "in the bot but not on the page"
    for alias, text in page.aliases.items():
        assert text.startswith(f"!{alias}"), "aliases only exist as ! commands"


async def test_only_hybrid_commands_are_shown_as_slash_commands(bot):
    """`!bass` has no `/bass`; showing one would send people to a dead command."""
    hybrid = ({c.name for c in public_commands(bot) if isinstance(c, commands.HybridCommand)}
              | BOT_LEVEL_HYBRID_COMMANDS)
    for name, text in parse("index.html").commands.items():
        expected = "/" if name in hybrid else "!"
        assert text.startswith(f"{expected}{name}"), f"{name} is shown as {text!r}"


@pytest.mark.parametrize("name", PAGES)
def test_nothing_inline_that_the_csp_would_block(name):
    for tag, attributes in parse(name).tags:
        if tag == "script":
            assert "src" in attributes or attributes.get("type") == "application/ld+json", \
                "inline script"
        assert tag != "style", "inline <style> element"
        assert "style" not in attributes, f"style attribute on <{tag}>"
        handlers = [a for a in attributes if a.startswith("on")]
        assert handlers == [], f"event handler attribute on <{tag}>: {handlers}"


def site_origin() -> str:
    config = (SITE / "wrangler.jsonc").read_text(encoding="utf-8")
    patterns = re.findall(r'"pattern"\s*:\s*"([^"]+)"', config)
    assert len(patterns) == 1, "expected exactly one custom domain"
    return f"https://{patterns[0]}/"


def test_every_public_url_uses_the_custom_domain():
    """Search Console treats a mismatch as a second, duplicate page."""
    origin = site_origin()
    page = parse("index.html")

    canonical = [a.get("href") for t, a in page.tags
                 if t == "link" and a.get("rel") == "canonical"]
    assert canonical == [origin]
    assert page.meta("property", "og:url") == origin
    assert page.meta("property", "og:image").startswith(origin)

    (json_ld,) = page.json_ld
    assert json.loads(json_ld)["url"] == origin

    sitemap = (PUBLIC / "sitemap.xml").read_text(encoding="utf-8")
    assert re.findall(r"<loc>\s*(.*?)\s*</loc>", sitemap) == [origin]

    robots = (PUBLIC / "robots.txt").read_text(encoding="utf-8")
    assert f"Sitemap: {origin}sitemap.xml" in robots.splitlines()


@pytest.mark.parametrize("name", PAGES)
def test_every_local_reference_exists(name):
    for tag, attributes in parse(name).tags:
        for key in ("href", "src"):
            value = attributes.get(key)
            if value and not value.startswith(EXTERNAL):
                path = value.split("#")[0].split("?")[0].lstrip("/")
                assert (PUBLIC / path).is_file(), f"<{tag} {key}={value!r}> is missing"


def test_every_stylesheet_url_exists():
    css = (PUBLIC / "styles.css").read_text(encoding="utf-8")
    for value in re.findall(r"url\(\s*[\"']?([^\"')]+)[\"']?\s*\)", css):
        if not value.startswith(EXTERNAL):
            assert (PUBLIC / value.lstrip("/")).is_file(), f"url({value}) is missing"
