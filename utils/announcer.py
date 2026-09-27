"""
Publishing player events to a Discord text channel.

The playback loop decides *that* something is worth announcing; this decides how
it reads and what to do when the channel has gone away. Keeping the two apart is
what lets :class:`~utils.player.MusicPlayer` run in a test without Discord, and
what stops a change of wording from reaching into the audio core.
"""

import logging
from typing import TYPE_CHECKING, Optional

import discord

from services.media import Track
from utils.embeds import info_embed, load_error_embed
from utils.now_playing_view import NowPlayingControls

if TYPE_CHECKING:
    from utils.player import MusicPlayer

log = logging.getLogger("loopify.announcer")


class ChannelAnnouncer:
    """Sends one guild's player notifications to a text channel."""

    def __init__(self, channel: discord.abc.Messageable) -> None:
        self.channel = channel
        # The Now Playing message whose buttons are still live, if any.
        self._live: Optional[tuple[discord.Message, NowPlayingControls]] = None

    async def now_playing(self, player: "MusicPlayer", track: Track,
                          requester: discord.abc.User) -> None:
        await self.retire()
        view = NowPlayingControls(player, track, requester)
        message = await self._send(view.embed(), view)
        if message is None:
            view.stop()
        else:
            self._live = (message, view)

    async def retire(self) -> None:
        """Disable the live Now Playing buttons, so only the current track has any."""
        live, self._live = self._live, None
        if live is None:
            return
        message, view = live
        view.retire()
        try:
            await message.edit(view=view)
        except discord.HTTPException as e:
            log.debug("Could not retire Now Playing controls: %s", e)

    async def load_failed(self, track: Track) -> None:
        await self._send(load_error_embed(track))

    async def idle_disconnect(self, after_seconds: float) -> None:
        await self._send(info_embed(
            "👋 Left the channel",
            f"Disconnected after {after_seconds / 60:.0f} minutes of inactivity.",
        ))

    async def _send(self, embed: discord.Embed,
                    view: Optional[discord.ui.View] = None) -> Optional[discord.Message]:
        """A channel that has been deleted, or that we lost access to, is not an
        error worth propagating into the playback loop."""
        try:
            if view is None:
                return await self.channel.send(embed=embed)
            return await self.channel.send(embed=embed, view=view)
        except discord.HTTPException as e:
            log.debug("Could not announce to channel: %s", e)
            return None
