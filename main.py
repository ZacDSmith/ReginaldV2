import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp as youtube_dl
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

# Suppress noise about console usage from errors
youtube_dl.utils.bug_reports_message = lambda: ''


class MusicBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            activity=discord.Activity(type=discord.ActivityType.listening, name="/play")
        )

        self.queues = {}  # Guild ID -> Queue

    async def setup_hook(self):
        # Sync slash commands
        await self.tree.sync()


bot = MusicBot()


# Audio source for YouTube playback
class YTDLSource(discord.PCMVolumeTransformer):
    YTDL_OPTIONS = {
        'format': 'bestaudio/best',
        'extractaudio': True,
        'audioformat': 'mp3',
        'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
        'restrictfilenames': True,
        'noplaylist': True,
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'logtostderr': False,
        'quiet': True,
        'no_warnings': True,
        'default_search': 'auto',
        'source_address': '0.0.0.0',
    }

    FFMPEG_OPTIONS = {
        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
        'options': '-vn',
    }

    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        ytdl = youtube_dl.YoutubeDL(cls.YTDL_OPTIONS)

        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))

        if 'entries' in data:
            data = data['entries'][0]

        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **cls.FFMPEG_OPTIONS), data=data)


# Queue system
class Queue:
    def __init__(self):
        self._queue = []
        self.now_playing = None

    def add(self, track):
        self._queue.append(track)

    def next(self):
        if len(self._queue) == 0:
            return None

        self.now_playing = self._queue.pop(0)  # Remove and return first item
        return self.now_playing

    def clear(self):
        self._queue = []
        self.now_playing = None

    def remove(self, index):
        if 0 <= index < len(self._queue):
            return self._queue.pop(index)
        return None

    @property
    def is_empty(self):
        return len(self._queue) == 0

    @property
    def upcoming(self):
        return self._queue.copy()

    def __len__(self):
        return len(self._queue)


async def play_next(guild):
    queue = bot.queues.get(guild.id)
    if not queue or queue.is_empty:
        # Disconnect after some time if queue is empty
        voice_client = guild.voice_client
        if voice_client and not voice_client.is_playing():
            await asyncio.sleep(300)  # 5 minutes
            if not voice_client.is_playing():
                await voice_client.disconnect()
                del bot.queues[guild.id]
        return

    voice_client = guild.voice_client
    if not voice_client:
        return

    # Get the next track (this removes it from queue)
    player = queue.next()
    if not player:
        return

    try:
        # Create fresh source to avoid issues
        new_player = await YTDLSource.from_url(player.url, loop=bot.loop, stream=True)
    except Exception as e:
        print(f"Error creating player: {e}")
        # Try to play next if this one fails
        await play_next(guild)
        return

    def after_playing(error):
        if error:
            print(f"Player error: {error}")

        # Schedule next track
        coro = play_next(guild)
        fut = asyncio.run_coroutine_threadsafe(coro, bot.loop)
        try:
            fut.result()
        except Exception as e:
            print(f"Error in after_playing: {e}")

    voice_client.play(new_player, after=after_playing)
    queue.now_playing = new_player


@bot.tree.command(name="play", description="Play a song from YouTube")
async def play(interaction: discord.Interaction, url: str):
    """Play a song from YouTube"""

    await interaction.response.defer()

    # Check if user is in a voice channel
    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.followup.send("You need to be in a voice channel to use this command!")

    # Get or create queue for guild
    if interaction.guild.id not in bot.queues:
        bot.queues[interaction.guild.id] = Queue()

    queue = bot.queues[interaction.guild.id]

    # Connect to voice channel if not already connected
    voice_client = interaction.guild.voice_client
    if not voice_client:
        voice_client = await interaction.user.voice.channel.connect()

    # Process the query (URL or search)
    try:
        player = await YTDLSource.from_url(url, loop=bot.loop, stream=True)
    except Exception as e:
        return await interaction.followup.send(f"Error: {e}")

    # Add to queue
    queue.add(player)

    # If nothing is playing, start playback
    if not voice_client.is_playing():
        await play_next(interaction.guild)
        await interaction.followup.send(f"Now playing: {player.title}")
    else:
        await interaction.followup.send(f"Added to queue: {player.title}")


@bot.tree.command(name="clear")
async def clear(ctx: commands.Context, amount: int):
    try:
        await ctx.response.send_message(f"Clearing {amount} messages...", ephemeral=True)
        channel = await ctx.guild.fetch_channel(ctx.channel.id)
        await discord.channel.TextChannel.purge(channel, limit=amount)

    except Exception as e:
        print(e)


@bot.tree.command(name="skip", description="Skip the current song")
async def skip(interaction: discord.Interaction):
    """Skip the current song"""
    await interaction.response.defer()

    voice_client = interaction.guild.voice_client
    if not voice_client or not voice_client.is_playing():
        return await interaction.followup.send("Nothing is playing right now!")

    # Stop the current player
    voice_client.stop()

    # Get the queue
    if interaction.guild.id not in bot.queues:
        return await interaction.followup.send("No queue exists!")

    queue = bot.queues[interaction.guild.id]

    # Play the next track
    await play_next(interaction.guild)

    if queue.current_track:
        await interaction.followup.send(f"Skipped! Now playing: {queue.current_track.title}")
    else:
        await interaction.followup.send("Skipped! The queue is now empty.")


@bot.tree.command(name="queue", description="Show the current queue")
async def show_queue(interaction: discord.Interaction):
    """Show the current queue"""

    if interaction.guild.id not in bot.queues or bot.queues[interaction.guild.id].is_empty:
        return await interaction.response.send_message("The queue is empty!")

    queue = bot.queues[interaction.guild.id]

    embed = discord.Embed(title="Music Queue", color=discord.Color.blue())

    # Current track
    if queue.now_playing:
        embed.add_field(name="Now Playing", value=queue.now_playing.title, inline=False)

    # Upcoming tracks
    if len(queue.upcoming) > 0:
        upcoming = "\n".join(f"{i + 1}. {track.title}" for i, track in enumerate(queue.upcoming))
        embed.add_field(name="Up Next", value=upcoming, inline=False)

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="pause", description="Pause the current song")
async def pause(interaction: discord.Interaction):
    """Pause the current song"""

    voice_client = interaction.guild.voice_client
    if not voice_client or not voice_client.is_playing():
        return await interaction.response.send_message("Nothing is playing right now!")

    if voice_client.is_paused():
        return await interaction.response.send_message("Already paused!")

    voice_client.pause()
    await interaction.response.send_message("Paused the music!")


@bot.tree.command(name="resume", description="Resume the current song")
async def resume(interaction: discord.Interaction):
    """Resume the current song"""

    voice_client = interaction.guild.voice_client
    if not voice_client or not voice_client.is_paused():
        return await interaction.response.send_message("Nothing is paused right now!")

    voice_client.resume()
    await interaction.response.send_message("Resumed the music!")


@bot.tree.command(name="stop", description="Stop the music and clear the queue")
async def stop(interaction: discord.Interaction):
    """Stop the music and clear the queue"""

    voice_client = interaction.guild.voice_client
    if not voice_client or not (voice_client.is_playing() or voice_client.is_paused()):
        return await interaction.response.send_message("Nothing is playing right now!")

    if interaction.guild.id in bot.queues:
        bot.queues[interaction.guild.id].clear()

    voice_client.stop()
    await interaction.response.send_message("Stopped the music and cleared the queue!")


@bot.tree.command(name="disconnect", description="Disconnect the bot from voice")
async def disconnect(interaction: discord.Interaction):
    """Disconnect the bot from voice"""

    voice_client = interaction.guild.voice_client
    if not voice_client:
        return await interaction.response.send_message("I'm not connected to a voice channel!")

    if interaction.guild.id in bot.queues:
        bot.queues[interaction.guild.id].clear()

    await voice_client.disconnect()
    await interaction.response.send_message("Disconnected from voice channel!")


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')


if __name__ == "__main__":
    bot.run(token=os.environ['TOKEN'])
