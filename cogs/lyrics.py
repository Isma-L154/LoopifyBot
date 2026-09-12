from discord.ext import commands

from services import lyrics_api
from utils.embeds import error_embed, lyrics_embed
from utils.player import players


class Lyrics(commands.Cog, name="🎤 Lyrics"):
    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    def _split_query(query: str) -> tuple[str, str]:
        """Split a ``title - artist`` query. A query without the separator is all title."""
        if " - " in query:
            title, artist = query.split(" - ", 1)
            return title.strip(), artist.strip()
        return query.strip(), ""

    def _current_track(self, ctx) -> tuple[str, str]:
        """Title and artist of whatever is playing, or ``("", "")`` if nothing is."""
        player = players.get(ctx.guild.id)
        if not player or not player.current:
            return "", ""
        return player.current["title"], player.current.get("uploader", "")

    @commands.command(aliases=["ly"])
    async def lyrics(self, ctx, *, query: str = None):
        """
        Fetch lyrics for the current song or a specific query.
        Usage: !lyrics               → current song
               !lyrics <title>       → search by title
               !lyrics <title> - <artist>  → title + artist
        """
        async with ctx.typing():
            title, artist = (self._split_query(query) if query
                             else self._current_track(ctx))
            if not title:
                return await ctx.send(embed=error_embed(
                    "Nothing is playing. Provide a song name: `!lyrics <title>`"
                ))

            result = await lyrics_api.fetch(title, artist, loop=self.bot.loop)
            if not result:
                return await ctx.send(embed=error_embed(
                    f"Couldn't find lyrics for **{title}**."
                ))

            for embed in lyrics_embed(result["title"], result["artist"], result["lyrics"]):
                await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Lyrics(bot))
