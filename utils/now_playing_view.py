"""
The buttons under each Now Playing message.

Every button runs the same action as its command (see :mod:`utils.controls`);
what differs is the delivery. A button that changes what the message shows
(pause, loop) edits it in place. One that changes playback for everyone (skip,
previous, stop, shuffle) answers publicly, naming who pressed it. Anything
personal — the queue, a refusal, an error — is ephemeral.

The lyrics button needs the Lyrics cog, which this module must not import: the
cog registers itself with :func:`set_lyrics_provider` on load, and without a
provider the button is left off.
"""

from typing import TYPE_CHECKING, Awaitable, Callable, Optional

import discord

from services.media import Track
from utils import controls
from utils.embeds import error_embed, now_playing_embed, queue_embed, success_embed

if TYPE_CHECKING:
    from utils.player import MusicPlayer

# Posts the playing track's lyrics to a channel; False when there are none.
LyricsProvider = Callable[[discord.abc.Messageable, "MusicPlayer"], Awaitable[bool]]

_lyrics: Optional[LyricsProvider] = None

PAUSE, RESUME = "⏸", "▶️"


def set_lyrics_provider(provider: Optional[LyricsProvider]) -> None:
    global _lyrics
    _lyrics = provider


class NowPlayingControls(discord.ui.View):
    def __init__(self, player: "MusicPlayer", track: Track,
                 requester: discord.abc.User) -> None:
        # No timeout: a long track must not lose its controls halfway through.
        # The announcer retires the view instead, which also frees it.
        super().__init__(timeout=None)
        self.player = player
        self.track = track
        self.requester = requester
        if _lyrics is None:
            self.remove_item(self.lyrics_button)

    def embed(self) -> discord.Embed:
        return now_playing_embed(self.track, self.requester,
                                 loop_mode=self.player.loop_mode)

    def retire(self) -> None:
        """Grey every button out and release the view from discord.py's store."""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.player.is_destroyed:
            await _private_error(interaction, "This player has ended.")
            return False
        vc = self.player.voice
        user = interaction.user
        listening = (isinstance(user, discord.Member) and user.voice is not None
                     and vc is not None and user.voice.channel == vc.channel)
        if not listening:
            await _private_error(interaction,
                                 "Join my voice channel to use these controls.")
            return False
        return True

    async def _announce(self, interaction: discord.Interaction,
                        outcome: controls.Outcome) -> None:
        if not outcome.ok:
            return await _private_error(interaction, outcome.message)
        await interaction.response.send_message(embed=success_embed(
            f"{outcome.message} — {interaction.user.mention}"))

    async def _edit_in_place(self, interaction: discord.Interaction,
                       outcome: controls.Outcome) -> None:
        if not outcome.ok:
            return await _private_error(interaction, outcome.message)
        await interaction.response.edit_message(embed=self.embed(), view=self)

    # -- Row 1: transport ---------------------------------------------------

    @discord.ui.button(emoji="⏮", style=discord.ButtonStyle.secondary, row=0)
    async def previous_button(self, interaction: discord.Interaction,
                              _button: "discord.ui.Button[NowPlayingControls]") -> None:
        await self._announce(interaction, controls.previous(self.player))

    @discord.ui.button(emoji=PAUSE, style=discord.ButtonStyle.primary, row=0)
    async def pause_button(self, interaction: discord.Interaction,
                           button: "discord.ui.Button[NowPlayingControls]") -> None:
        if controls.pause(self.player).ok:
            button.emoji = RESUME
        elif controls.resume(self.player).ok:
            button.emoji = PAUSE
        else:
            return await _private_error(interaction, controls.NOTHING_PLAYING)
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(emoji="⏭", style=discord.ButtonStyle.secondary, row=0)
    async def skip_button(self, interaction: discord.Interaction,
                          _button: "discord.ui.Button[NowPlayingControls]") -> None:
        await self._announce(interaction, controls.skip(self.player))

    @discord.ui.button(emoji="⏹", style=discord.ButtonStyle.danger, row=0)
    async def stop_button(self, interaction: discord.Interaction,
                          _button: "discord.ui.Button[NowPlayingControls]") -> None:
        await self._announce(interaction, controls.stop(self.player))

    # -- Row 2: queue and extras ------------------------------------------

    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.secondary, row=1)
    async def loop_button(self, interaction: discord.Interaction,
                          _button: "discord.ui.Button[NowPlayingControls]") -> None:
        await self._edit_in_place(interaction, controls.cycle_loop(self.player))

    @discord.ui.button(emoji="🔀", style=discord.ButtonStyle.secondary, row=1)
    async def shuffle_button(self, interaction: discord.Interaction,
                             _button: "discord.ui.Button[NowPlayingControls]") -> None:
        await self._announce(interaction, controls.shuffle(self.player))

    @discord.ui.button(emoji="📜", style=discord.ButtonStyle.secondary, row=1)
    async def queue_button(self, interaction: discord.Interaction,
                           _button: "discord.ui.Button[NowPlayingControls]") -> None:
        await interaction.response.send_message(
            embed=queue_embed(self.player.to_list(), self.player.current),
            ephemeral=True)

    @discord.ui.button(emoji="🎤", style=discord.ButtonStyle.secondary, row=1)
    async def lyrics_button(self, interaction: discord.Interaction,
                            _button: "discord.ui.Button[NowPlayingControls]") -> None:
        channel = interaction.channel
        if _lyrics is None or not isinstance(channel, discord.abc.Messageable):
            return await _private_error(interaction, "Lyrics are unavailable.")
        # The lookup can take seconds; Discord wants an answer within three.
        await interaction.response.defer()
        if not await _lyrics(channel, self.player):
            await interaction.followup.send(embed=error_embed(
                "Couldn't find lyrics for this song."), ephemeral=True)


async def _private_error(interaction: discord.Interaction, message: str) -> None:
    await interaction.response.send_message(embed=error_embed(message),
                                            ephemeral=True)
