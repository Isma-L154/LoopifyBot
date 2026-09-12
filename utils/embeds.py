from datetime import timedelta
from typing import Optional

import discord

GREEN = 0x1DB954
BLURPLE = 0x5865F2
RED = 0xFF4444
GOLD = 0xFFD700


def format_duration(seconds: Optional[int]) -> str:
    if not seconds:
        return "🔴 LIVE"
    return str(timedelta(seconds=seconds))


def _linked_title(track: dict) -> str:
    url = track.get("url")
    return f"[{track['title']}]({url})" if url else track["title"]


def now_playing_embed(track: dict, requester: discord.abc.User,
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
    embed.set_footer(text="🎧 Use !queue to see upcoming tracks")
    return embed


def added_embed(track: dict) -> discord.Embed:
    embed = discord.Embed(
        description=f"➕ Added to queue: **{_linked_title(track)}**", color=BLURPLE)
    if track.get("thumbnail"):
        embed.set_thumbnail(url=track["thumbnail"])
    return embed


def queue_embed(queue: list, current: Optional[dict], page: int = 1,
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


def load_error_embed(track: dict) -> discord.Embed:
    """Explain why a track produced no audio, per ``AudioStream.classify_error``."""
    title = track.get("title", "track")
    if track.get("error") == "blocked":
        return error_embed(
            f"YouTube is rate-limiting this server, so **{title}** can't be "
            f"loaded right now. Try SoundCloud instead — e.g. `!play sc: {title}`."
        )
    return error_embed(f"Couldn't load **{title}** — skipping.")


def error_embed(message: str) -> discord.Embed:
    return discord.Embed(description=f"❌ {message}", color=RED)


def success_embed(message: str) -> discord.Embed:
    return discord.Embed(description=f"✅ {message}", color=GREEN)


def info_embed(title: str, message: str) -> discord.Embed:
    return discord.Embed(title=title, description=message, color=BLURPLE)


def lyrics_embed(title: str, artist: str, lyrics: str) -> list[discord.Embed]:
    """Split lyrics across as many embeds as Discord's length limit requires."""
    max_len = 4000
    chunks = [lyrics[i:i + max_len] for i in range(0, len(lyrics), max_len)]
    return [
        discord.Embed(
            title=f"🎤 {title} — {artist}" if i == 0 else f"🎤 {title} (cont.)",
            description=chunk,
            color=GOLD,
        )
        for i, chunk in enumerate(chunks)
    ]
