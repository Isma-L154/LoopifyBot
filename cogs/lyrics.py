from discord.ext import commands
from services import lyrics_api
from utils.queue_manager import queue_manager
from utils.embeds import lyrics_embed, error_embed


class Lyrics(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(aliases=["ly"])
    async def lyrics(self, ctx, *, query: str = None):
        """
        Fetch lyrics for the current song or a specific query.
        Usage: !lyrics               → current song
               !lyrics <title>       → search by title
               !lyrics <title> - <artist>  → title + artist
        """
        async with ctx.typing():
            title, artist = "", ""

            if query:
                # Support "title - artist" format
                if " - " in query:
                    parts = query.split(" - ", 1)
                    title, artist = parts[0].strip(), parts[1].strip()
                else:
                    title = query.strip()
            else:
                # Fall back to current playing track
                gq = queue_manager.get(ctx.guild.id)
                if not gq.current:
                    return await ctx.send(embed=error_embed(
                        "Nothing is playing. Provide a song name: `!lyrics <title>`"
                    ))
                title = gq.current["title"]
                artist = gq.current.get("uploader", "")

            result = await lyrics_api.fetch(title, artist, loop=self.bot.loop)

            if not result:
                return await ctx.send(embed=error_embed(
                    f"Couldn't find lyrics for **{title}**."
                ))

            embeds = lyrics_embed(result["title"], result["artist"], result["lyrics"])
            for embed in embeds:
                await ctx.send(embed=embed)

    @lyrics.error
    async def lyrics_error(self, ctx, error):
        await ctx.send(embed=error_embed(str(error)))


async def setup(bot):
    await bot.add_cog(Lyrics(bot))
