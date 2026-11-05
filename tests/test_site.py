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
import subprocess
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

import discord
import pytest
from discord.ext import commands

from config import COGS

SITE = Path(__file__).resolve().parent.parent / "site"
PUBLIC = SITE / "public"
PAGES = ["index.html", "commands.html", "404.html"]
COMMAND_PAGES = ["index.html", "commands.html"]
# Indexable pages and the path Cloudflare serves each one at.
INDEXED = {"index.html": "", "commands.html": "commands"}

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
        self.examples: dict[str, str] = {}
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
        elif tag == "code" and "data-example" in attributes:
            self._open = (self.examples, attributes["data-example"] or "")
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

    def links(self) -> list[str]:
        return [a.get("href") or "" for t, a in self.tags if t == "a"]

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


@pytest.mark.parametrize("name", COMMAND_PAGES)
async def test_the_page_lists_exactly_the_registered_commands(bot, name):
    listed = set(parse(name).commands)
    registered = {c.name for c in public_commands(bot)} | BOT_LEVEL_HYBRID_COMMANDS
    assert listed - registered == set(), "on the page but not in the bot"
    assert registered - listed == set(), "in the bot but not on the page"


@pytest.mark.parametrize("name", COMMAND_PAGES)
async def test_the_page_lists_exactly_the_registered_aliases(bot, name):
    page = parse(name)
    registered = {a for c in public_commands(bot) for a in c.aliases}
    assert set(page.aliases) - registered == set(), "on the page but not in the bot"
    assert registered - set(page.aliases) == set(), "in the bot but not on the page"
    for alias, text in page.aliases.items():
        assert text.startswith(f"!{alias}"), "aliases only exist as ! commands"


def hybrid_names(bot: commands.Bot) -> set[str]:
    return ({c.name for c in public_commands(bot) if isinstance(c, commands.HybridCommand)}
            | BOT_LEVEL_HYBRID_COMMANDS)


@pytest.mark.parametrize("page", COMMAND_PAGES)
async def test_only_hybrid_commands_are_shown_as_slash_commands(bot, page):
    """`!bass` has no `/bass`; showing one would send people to a dead command."""
    hybrid = hybrid_names(bot)
    for name, text in parse(page).commands.items():
        expected = "/" if name in hybrid else "!"
        assert text.startswith(f"{expected}{name}"), f"{name} is shown as {text!r}"


async def test_every_example_runs_the_command_of_its_row(bot):
    """An example someone pastes must reach the command it sits next to."""
    page = parse("commands.html")
    assert set(page.commands) - set(page.examples) == set(), "commands without an example"

    owner = {name: name for name in BOT_LEVEL_HYBRID_COMMANDS}
    for c in public_commands(bot):
        owner[c.name] = c.name
        owner.update(dict.fromkeys(c.aliases, c.name))
    hybrid = hybrid_names(bot)

    for row, text in page.examples.items():
        prefix, word = text[0], text.split()[0][1:]
        assert prefix in "/!", f"{text!r} has no prefix"
        assert word in owner, f"example {text!r} is not a command"
        assert owner[word] == owner[row], f"example {text!r} sits in the {row!r} row"
        if prefix == "/":
            assert word in hybrid, f"{text!r}: aliases and ! commands have no / form"


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


@pytest.mark.parametrize(("name", "path"), INDEXED.items())
def test_every_public_url_uses_the_custom_domain(name, path):
    """Search Console treats a mismatch as a second, duplicate page."""
    url = site_origin() + path
    page = parse(name)

    canonical = [a.get("href") for t, a in page.tags
                 if t == "link" and a.get("rel") == "canonical"]
    assert canonical == [url]
    assert page.meta("property", "og:url") == url
    assert page.meta("property", "og:image").startswith(site_origin())
    for json_ld in page.json_ld:
        assert json.loads(json_ld)["url"] == url


def last_commit_day(path: Path) -> date | None:
    git = ["git", "-C", str(SITE.parent)]
    shallow = subprocess.run([*git, "rev-parse", "--is-shallow-repository"],
                             capture_output=True, text=True, check=True).stdout.strip()
    assert shallow == "false", "needs the full history (actions/checkout fetch-depth: 0)"
    day = subprocess.run([*git, "log", "-1", "--format=%cs", "--", str(path)],
                         capture_output=True, text=True, check=True).stdout.strip()
    return date.fromisoformat(day) if day else None


def test_the_sitemap_lists_every_indexed_page_with_its_last_change():
    """Google trusts lastmod only while it stays accurate, so a page commit must bump it."""
    origin = site_origin()
    sitemap = (PUBLIC / "sitemap.xml").read_text(encoding="utf-8")
    entries = re.findall(
        r"<url>\s*<loc>\s*(.*?)\s*</loc>\s*<lastmod>\s*(.*?)\s*</lastmod>\s*</url>",
        sitemap)
    assert sitemap.count("<url>") == len(entries), "a <url> without <lastmod>"
    assert [loc for loc, _ in entries] == [origin + path for path in INDEXED.values()]
    for (loc, lastmod), name in zip(entries, INDEXED, strict=True):
        day = date.fromisoformat(lastmod)
        assert day <= date.today(), f"{loc}: lastmod in the future"
        changed = last_commit_day(PUBLIC / name)
        assert changed is None or day >= changed, \
            f"{name} changed on {changed}; set its <lastmod> in sitemap.xml"

    robots = (PUBLIC / "robots.txt").read_text(encoding="utf-8")
    assert f"Sitemap: {origin}sitemap.xml" in robots.splitlines()


@pytest.mark.parametrize("name", INDEXED)
def test_every_indexed_page_links_back_to_the_parent_site(name):
    """cloudils.com is the only indexed page on the domain, and it links here."""
    assert "https://cloudils.com/" in parse(name).links()


def test_the_home_page_links_to_the_command_reference():
    assert "/commands" in parse("index.html").links()


def served_file(url: str) -> Path:
    """The file Cloudflare serves for a local URL: `/` is index.html, `/x` is x.html."""
    path = url.split("#")[0].split("?")[0].lstrip("/") or "index.html"
    if "." not in Path(path).name:
        path += ".html"
    return PUBLIC / path


@pytest.mark.parametrize("name", PAGES)
def test_every_local_reference_exists(name):
    for tag, attributes in parse(name).tags:
        for key in ("href", "src"):
            value = attributes.get(key)
            if value and not value.startswith(EXTERNAL):
                assert served_file(value).is_file(), f"<{tag} {key}={value!r}> is missing"


@pytest.mark.parametrize("name", PAGES)
def test_every_local_anchor_has_a_target(name):
    for link in parse(name).links():
        page, _, anchor = link.partition("#")
        if anchor and not page.startswith(("http://", "https://")):
            target = parse(served_file(page).name) if page else parse(name)
            assert anchor in {a.get("id") for _, a in target.tags}, f"{link!r} points nowhere"


def test_every_stylesheet_url_exists():
    css = (PUBLIC / "styles.css").read_text(encoding="utf-8")
    for value in re.findall(r"url\(\s*[\"']?([^\"')]+)[\"']?\s*\)", css):
        if not value.startswith(EXTERNAL):
            assert (PUBLIC / value.lstrip("/")).is_file(), f"url({value}) is missing"
