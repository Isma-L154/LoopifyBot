#We only initialize the bot in here and load the Cogs dynamically
import discord
import Config
from discord.ext import commands
from dotenv import load_dotenv
import os


load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=Config.COMMAND_PREFIX, intents=intents)

# Ping command from Cogs
async def load_cogs():
    for filename in os.listdir("./cogs"):
        if filename.endswith(".py"):
            await bot.load_extension(f"cogs.{filename[:-3]}")

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name} ({bot.user.id})")


async def main():
    async with bot:
        await load_cogs() #Load all the cogs dynamically
        await bot.start(TOKEN) #Take care of the token

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())