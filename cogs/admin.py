import logging

from discord.ext import commands

from utils.context import GuildContext
from utils.embeds import success_embed

log = logging.getLogger("loopify.admin")


class Admin(commands.Cog, name="Admin"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # Not run at startup: Discord rate-limits syncing, and the bot restarts on
    # its own (systemd, network loss). Run it once after a deploy that adds,
    # removes or changes a command.
    @commands.command(hidden=True)
    @commands.is_owner()
    async def sync(self, ctx: GuildContext) -> None:
        """Register the slash commands with Discord."""
        synced = await self.bot.tree.sync()
        log.info("Synced %d slash commands", len(synced))
        await ctx.send(embed=success_embed(f"Synced **{len(synced)}** slash commands."))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Admin(bot))
