import discord
from discord import Embed
from discord.ext import commands
import asyncio
from asyncio import run_coroutine_threadsafe
from yt_dlp import YoutubeDL
import os
import json


class MusicCog(commands.Cog):
    def __init__(self, bot): #Happens at the start of the initialization of the cog
        self.bot = bot

        # We use dictionaries with guild.id as the key to handle multiple servers at the same time.
        # This way, each server has its own state (playing, paused, queue, current index, and voice client).
        self.is_playing = {}
        self.is_paused = {}
        self.music_queue = {}
        self.queue_index = {}
        self.vc = {}

        #YT Variables
        self.YDL_OPTIONS = {
            'format': 'bestaudio/best', 
            'noplaylist': True,
            'quiet': True,
            'default_search': 'ytsearch'
        }
        
        self.FFMPEG_OPTIONS = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn'
        }

    @commands.Cog.listener()
    async def on_ready(self): #When it becomes online will do this
        for guild in self.bot.guilds:
            id = int(guild.id)
            self.is_playing[id] = self.is_paused[id] = False
            self.music_queue[id] = []
            self.queue_index[id] = 0
            self.vc[id] = None


    #Take the message from the user and join the voice channel
    async def join_VC(self, ctx, channel): 
        id = int(ctx.guild.id)
        if self.vc[id] is None or not self.vc[id].is_connected():
            self.vc[id] = await channel.connect() #Connect to the voice channel after we assign the id

            if self.vc[id] == None:
                await ctx.send("Failed to connect to the voice channel")
                return
        
        else:
            await self.vc[id].move_to(channel)


    #We adapt this search method to receive a link or to search directly from YT
    async def search_YT(self, ctx, search: str):
        id = int(ctx.guild.id)
        if not self.vc[id]:
            await ctx.send("Not connected to a voice channel")
            return

        with YoutubeDL(self.YDL_OPTIONS) as ydl:
            try:
                info = ydl.extract_info(search, download=False)
                url = info['formats'][0]['url'] #We receive a list of results, we take the first one
                title = info['title']
                await ctx.send(f"Found: {title}")
                return url
            except Exception as e:
                await ctx.send(f"Error: {e}")
                return None


    # Play music, in ds we use !play
    async def play_music(self, ctx, url):
        id = int(ctx.guild.id)
        if not self.vc[id]:
            await ctx.send("Not connected to a voice channel")
            return
        
        song = await self.search_YT(ctx, url) # Search for the song or the URL using the method we have above

        if song is not None:
            self.music_queue[id].append(song) #Add the URL to the queue

            if not self.is_playing[id]: #If nothing is playing, we start the playback with the method 'play_next_song'
                await self.play_next_song(ctx)
        
        else:
            await ctx.send("Could not find a valid URL or song to play")


    async def play_next_song(self, ctx):
        id = int(ctx.guild.id)
        if not self.vc[id]:
            await ctx.send("Not connected to a voice channel")
            return

        if self.queue_index[id] < len(self.music_queue[id]):
            self.is_playing[id] = True
            url = self.music_queue[id][self.queue_index[id]]
            with YoutubeDL(self.YDL_OPTIONS) as ydl:
                try:
                    info = ydl.extract_info(url, download=False)
                    source = discord.FFmpegPCMAudio(info['formats'][0]['url'], **self.FFMPEG_OPTIONS) #Create the audio source with FFMPEG_OPTIONS
                    self.queue_index[id] += 1
                    self.vc[id].play(source, after=lambda e: self.bot.loop.create_task(self.play_next_song(ctx))) #Play the next song after the current one finishes, we play it on the VC
                    await self.playing_embed(ctx, info) #Send an embed with the song info
                except Exception as e:
                    await ctx.send(f"Error playing song: {e}")
                    self.is_playing[id] = False
        else:
            self.is_playing[id] = False
            self.queue_index[id] = 0
            self.music_queue[id] = []
            await ctx.send("Queue finished")


    # Send an embed with the song info
    async def playing_embed(self, ctx, song):
        title = song['title']
        url = song['url']
        thumbnail = song['thumbnail']

        embed = Embed(
            title="Now Playing", 
            description= f'[{title}]({url})', 
            color=0x00ff00)


        embed.set_thumbnail(url=thumbnail)
        embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.avatar.url)
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(MusicCog(bot))
