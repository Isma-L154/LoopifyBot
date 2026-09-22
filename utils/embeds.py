from datetime import timedelta
from typing import Optional, Sequence

import discord

from config import COMMAND_PREFIX
from services.media import Track
from services.synced_lyrics import Line

GREEN = 0x1DB954
BLURPLE = 0x5865F2
RED = 0xFF4444
GOLD = 0xFFD700


def format_duration(seconds: Optional[float]) -> str:
    if not seconds:
        return "🔴 LIVE"
    # Whole seconds: SoundCloud reports durations like 187.43, which timedelta
    # would render as 0:03:07.430000.
    return str(timedelta(seconds=int(seconds)))


def _linked_title(track: Track) -> str:
    url = track.get("url")
    return f"[{track['title']}]({url})" if url else track["title"]


def now_playing_embed(track: Track, requester: discord.abc.User,
                      loop_mode: str = "off") -> discord.Embed:
    embed = discord.Embed(
        title="🎵 Now Playing",
        description=f"**{_linked_title(track)}**",
        color=GREEN,
    )
    embed.add_field(name="⏱ Duration", value=format_duration(track.get("duration")), inline=True)
    embed.add_field(name="👤 Requested by", value=requester.mention, inline=True)
    embed.add_field(name="🔁 Loop", value=loop_mode.capitalize(), inline=True)
    if track.get("uploader"):
        embed.add_field(name="📺 Channel", value=track["uploader"], inline=True)
    if track.get("thumbnail"):
        embed.set_thumbnail(url=track["thumbnail"])
    embed.set_footer(text=f"🎧 Use {COMMAND_PREFIX}queue to see upcoming tracks")
    return embed


def added_embed(track: Track) -> discord.Embed:
    embed = discord.Embed(
        description=f"➕ Added to queue: **{_linked_title(track)}**", color=BLURPLE)
    if track.get("thumbnail"):
        embed.set_thumbnail(url=track["thumbnail"])
    return embed


def queue_embed(queue: list[Track], current: Optional[Track], page: int = 1,
                per_page: int = 10) -> discord.Embed:
    embed = discord.Embed(title="📋 Music Queue", color=BLURPLE)

    if current:
        embed.add_field(
            name="🎵 Now Playing",
            value=f"**{current['title']}** `{format_duration(current.get('duration'))}`",
            inline=False,
        )

    start = (page - 1) * per_page
    page_items = queue[start:start + per_page]
    if page_items:
        lines = [
            f"`{i}.` **{track['title']}** `{format_duration(track.get('duration'))}`"
            for i, track in enumerate(page_items, start=start + 1)
        ]
        value = "\n".join(lines)
    else:
        value = "*Queue is empty*"
    embed.add_field(name="⏭ Up Next", value=value, inline=False)

    total_pages = max(1, (len(queue) + per_page - 1) // per_page)
    embed.set_footer(text=f"Page {page}/{total_pages} • {len(queue)} tracks in queue")
    return embed


def load_error_embed(track: Track) -> discord.Embed:
    """Explain why a track produced no audio, per ``AudioStream.classify_error``."""
    title = track.get("title", "track")
    if track.get("error") == "blocked":
        return error_embed(
            f"YouTube is rate-limiting this server, so **{title}** can't be "
            f"loaded right now. Try SoundCloud instead — e.g. `{COMMAND_PREFIX}play sc: {title}`."
        )
    return error_embed(f"Couldn't load **{title}** — skipping.")


def error_embed(message: str) -> discord.Embed:
    return discord.Embed(description=f"❌ {message}", color=RED)


def success_embed(message: str) -> discord.Embed:
    return discord.Embed(description=f"✅ {message}", color=GREEN)


def info_embed(title: str, message: str) -> discord.Embed:
    return discord.Embed(title=title, description=message, color=BLURPLE)


EMBED_LIMIT = 4000       # Discord's cap on an embed description
CONTEXT_LINES = 2        # lines shown either side of the one playing


def clock(seconds: float) -> str:
    """``3:46`` — a position readout, not a duration. Minutes never roll over."""
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def lyrics_window(lines: Sequence[Line], index: int,
                  context: int = CONTEXT_LINES) -> str:
    """
    The line playing now, with a little of what came before and what is next.

    ``index`` of -1 means the song has not reached its first line yet — during an
    intro nothing is highlighted, but what is coming is still shown. A timed line
    with no words is an instrumental gap and reads as one.
    """
    if not lines:
        return ""
    first = max(0, index - context)
    rendered = []
    for position in range(first, min(len(lines), max(index, 0) + context + 1)):
        text = lines[position][1] or "♪"
        rendered.append(f"**▶ {text}**" if position == index else f"　{text}")
    return "\n".join(rendered)


def lyrics_pages(text: str, limit: int = EMBED_LIMIT) -> list[str]:
    """
    Split lyrics into embed-sized pages, breaking between lines.

    Slicing at a fixed character count cuts words, and sometimes whole verses,
    in half. A single line longer than the limit still has to be broken, but
    that is rare enough to be worth handling bluntly.
    """
    pages: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                pages.append(current)
                current = ""
            pages.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            pages.append(current)
            current = line
        else:
            current = candidate
    pages.append(current)
    return pages


def synced_lyrics_embed(title: str, artist: str, lines: Sequence[Line], index: int,
                        position: float, duration: Optional[float]) -> discord.Embed:
    """The live view: a window on the lyrics plus where the song is."""
    embed = discord.Embed(
        title=f"🎤 {title} — {artist}",
        description=lyrics_window(lines, index),
        color=GOLD,
    )
    total = f" / {clock(duration)}" if duration else ""
    embed.set_footer(text=f"{clock(position)}{total}")
    return embed


def lyrics_embed(title: str, artist: str, lyrics: str,
                 page: int = 0, note: str = "") -> discord.Embed:
    """One page of static lyrics."""
    pages = lyrics_pages(lyrics)
    page = max(0, min(page, len(pages) - 1))
    embed = discord.Embed(
        title=f"🎤 {title} — {artist}",
        description=pages[page],
        color=GOLD,
    )
    footer = f"Page {page + 1}/{len(pages)}" if len(pages) > 1 else ""
    embed.set_footer(text=" • ".join(part for part in (footer, note) if part))
    return embed
