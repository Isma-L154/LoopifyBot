import discord
from datetime import timedelta


def format_duration(seconds: int) -> str:
    if not seconds:
        return "🔴 LIVE"
    return str(timedelta(seconds=seconds))


def now_playing_embed(track: dict, requester: discord.Member, loop_mode: str = "off") -> discord.Embed:
    embed = discord.Embed(
        title="🎵 Now Playing",
        description=f"**[{track['title']}]({track['url']})**",
        color=0x1DB954
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


def queue_embed(queue: list, current: dict, page: int = 1, per_page: int = 10) -> discord.Embed:
    embed = discord.Embed(title="📋 Music Queue", color=0x5865F2)

    if current:
        embed.add_field(
            name="🎵 Now Playing",
            value=f"**{current['title']}** `{format_duration(current.get('duration'))}`",
            inline=False
        )

    start = (page - 1) * per_page
    end = start + per_page
    page_items = queue[start:end]

    if page_items:
        lines = []
        for i, track in enumerate(page_items, start=start + 1):
            lines.append(f"`{i}.` **{track['title']}** `{format_duration(track.get('duration'))}`")
        embed.add_field(name="⏭ Up Next", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="⏭ Up Next", value="*Queue is empty*", inline=False)

    total_pages = max(1, (len(queue) + per_page - 1) // per_page)
    embed.set_footer(text=f"Page {page}/{total_pages} • {len(queue)} tracks in queue")
    return embed


def error_embed(message: str) -> discord.Embed:
    return discord.Embed(description=f"❌ {message}", color=0xFF4444)


def success_embed(message: str) -> discord.Embed:
    return discord.Embed(description=f"✅ {message}", color=0x1DB954)


def info_embed(title: str, message: str) -> discord.Embed:
    return discord.Embed(title=title, description=message, color=0x5865F2)


def lyrics_embed(title: str, artist: str, lyrics: str) -> list[discord.Embed]:
    """Split lyrics into multiple embeds if too long."""
    max_len = 4000
    chunks = [lyrics[i:i + max_len] for i in range(0, len(lyrics), max_len)]
    embeds = []
    for i, chunk in enumerate(chunks):
        embed = discord.Embed(
            title=f"🎤 {title} — {artist}" if i == 0 else f"🎤 {title} (cont.)",
            description=chunk,
            color=0xFFD700
        )
        embeds.append(embed)
    return embeds
