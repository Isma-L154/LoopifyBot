"""
Publishing player events to a Discord text channel.

The playback loop decides *that* something is worth announcing; this decides how
it reads and what to do when the channel has gone away. Keeping the two apart is
what lets :class:`~utils.player.MusicPlayer` run in a test without Discord, and
what stops a change of wording from reaching into the audio core.
"""

import logging

import discord

from services.media import Track
from utils.embeds import info_embed, load_error_embed, now_playing_embed

log = logging.getLogger("loopify.announcer")


class ChannelAnnouncer:
    """Sends one guild's player notifications to a text channel."""

    def __init__(self, channel: discord.abc.Messageable) -> None:
        self.channel = channel

    async def now_playing(self, track: Track, requester: discord.abc.User,
                          loop_mode: str) -> None:
        await self._send(now_playing_embed(track, requester, loop_mode=loop_mode))

    async def load_failed(self, track: Track) -> None:
        await self._send(load_error_embed(track))

    async def idle_disconnect(self, after_seconds: float) -> None:
        await self._send(info_embed(
            "👋 Left the channel",
            f"Disconnected after {after_seconds / 60:.0f} minutes of inactivity.",
        ))

    async def _send(self, embed: discord.Embed) -> None:
        """A channel that has been deleted, or that we lost access to, is not an
        error worth propagating into the playback loop."""
        try:
            await self.channel.send(embed=embed)
        except discord.HTTPException as e:
            log.debug("Could not announce to channel: %s", e)
