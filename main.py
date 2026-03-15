import discord
from discord.ext import commands
import yt_dlp
import asyncio
import os
import tempfile
from typing import Optional
from urllib.parse import urlparse
from dotenv import load_dotenv
import requests
from gtts import gTTS

load_dotenv()


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
        await self.tree.sync()


bot = MusicBot()

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "extract_flat": "in_playlist",
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)


SUPPORTED_AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".flac")


class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, url, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get("title")
        self.url = url

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))

        if "entries" in data:
            data = data["entries"][0]

        filename = data["url"] if stream else ytdl.prepare_filename(data)

        return cls(
            discord.FFmpegPCMAudio(
                filename,
                **{
                    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
                    "options": "-vn",
                },
            ),
            data=data,
            url=url,
        )

    @classmethod
    async def from_audio_file(cls, source_path_or_url, *, title=None):
        track_title = title or os.path.basename(urlparse(source_path_or_url).path) or "Audio Track"
        return cls(
            discord.FFmpegPCMAudio(source_path_or_url, options="-vn"),
            data={"title": track_title, "is_audio_file": True},
            url=source_path_or_url,
        )


class Queue:
    def __init__(self):
        self._queue = []
        self.now_playing = None

    def add(self, track):
        self._queue.append(track)

    def next(self):
        if len(self._queue) == 0:
            return None

        self.now_playing = self._queue.pop(0)
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


def is_supported_audio_source(source: str) -> bool:
    parsed = urlparse(source)
    if parsed.path.lower().endswith(SUPPORTED_AUDIO_EXTENSIONS):
        return True

    return os.path.isfile(source) and source.lower().endswith(SUPPORTED_AUDIO_EXTENSIONS)


async def play_next(guild):
    queue = bot.queues.get(guild.id)
    if not queue or queue.is_empty:
        voice_client = guild.voice_client
        if voice_client and not voice_client.is_playing():
            await asyncio.sleep(300)
            if not voice_client.is_playing():
                await voice_client.disconnect()
                del bot.queues[guild.id]
        return

    voice_client = guild.voice_client
    if not voice_client:
        return

    player = queue.next()
    if not player:
        return

    try:
        if player.data.get("is_audio_file"):
            new_player = await YTDLSource.from_audio_file(player.url, title=player.title)
        else:
            new_player = await YTDLSource.from_url(player.url, loop=bot.loop, stream=True)
    except Exception as e:
        print(f"Error creating player: {e}")
        await play_next(guild)
        return

    def after_playing(error):
        if error:
            print(f"Player error: {error}")

        coro = play_next(guild)
        fut = asyncio.run_coroutine_threadsafe(coro, bot.loop)
        try:
            fut.result()
        except Exception as e:
            print(f"Error in after_playing: {e}")

    voice_client.play(new_player, after=after_playing)
    queue.now_playing = new_player


@bot.tree.command(name="random", description="Sends random meme")
async def random(interaction: discord.Interaction):
    await interaction.response.defer()

    api_url = os.environ["MEME"]

    try:
        response = requests.get(api_url, timeout=15)
        response.raise_for_status()
    except requests.RequestException as error:
        return await interaction.followup.send(f"Failed to fetch meme: {error}")

    embed = discord.Embed(title="Here's a random meme!")
    embed.set_image(url=response.text)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="tts", description="Text to speech")
async def tts(interaction: discord.Interaction, text: str):
    try:
        await interaction.response.defer()

        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.followup.send("You need to be in a voice channel to use this command!")

        voice_client = interaction.guild.voice_client
        if not voice_client:
            voice_client = await interaction.user.voice.channel.connect()

        if voice_client.is_playing():
            return await interaction.followup.send("Wait until I'm finished speaking!")

        tts_audio = gTTS(text=text, lang="en")
        temp_file = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        file_path = temp_file.name
        temp_file.close()
        tts_audio.save(file_path)

        def cleanup_tts(_):
            if os.path.exists(file_path):
                os.remove(file_path)

        source = discord.FFmpegPCMAudio(file_path)
        voice_client.play(source, after=cleanup_tts)

        await interaction.followup.send(f"Speaking: {text}")
    except Exception as e:
        if interaction.response.is_done():
            await interaction.followup.send(str(e))
        else:
            await interaction.response.send_message(str(e), ephemeral=True)


@bot.tree.command(name="addmeme", description="Add a meme")
async def addmeme(interaction: discord.Interaction, url: str):
    await interaction.response.defer()
    api_url = os.environ["CREATEMEME"]
    headers = {"Content-Type": "application/json"}
    post = {"Link": f"{url}"}

    try:
        response = requests.post(api_url, headers=headers, json=post, timeout=15)
        response.raise_for_status()
    except requests.RequestException as error:
        return await interaction.followup.send(f"Failed to add meme: {error}")

    await interaction.followup.send(response.text)


@bot.tree.command(name="play", description="Play from YouTube URL or uploaded audio file")
async def play(
    interaction: discord.Interaction,
    url: Optional[str] = None,
    file: Optional[discord.Attachment] = None,
):
    await interaction.response.defer()

    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.followup.send("You need to be in a voice channel to use this command!")

    if not url and not file:
        return await interaction.followup.send("Provide a YouTube URL or upload an audio file.")

    if url and file:
        return await interaction.followup.send("Please provide either a URL or a file, not both.")

    if interaction.guild.id not in bot.queues:
        bot.queues[interaction.guild.id] = Queue()

    queue = bot.queues[interaction.guild.id]

    voice_client = interaction.guild.voice_client
    if not voice_client:
        voice_client = await interaction.user.voice.channel.connect()

    source = url
    if file:
        if not file.filename.lower().endswith(SUPPORTED_AUDIO_EXTENSIONS):
            allowed = ", ".join(SUPPORTED_AUDIO_EXTENSIONS)
            return await interaction.followup.send(f"Only these audio attachments are supported: {allowed}")
        source = file.url

    try:
        if source and is_supported_audio_source(source):
            title = file.filename if file else None
            player = await YTDLSource.from_audio_file(source, title=title)
        else:
            player = await YTDLSource.from_url(source, loop=bot.loop, stream=True)
    except Exception as e:
        return await interaction.followup.send(f"Error: {e}")

    queue.add(player)

    if not voice_client.is_playing():
        await play_next(interaction.guild)
        await interaction.followup.send(f"Now playing: {player.title}")
    else:
        await interaction.followup.send(f"Added to queue: {player.title}")


@bot.tree.command(name="clear", description="Clear messages")
async def clear(interaction: discord.Interaction, amount: int):
    await interaction.response.defer(ephemeral=True)

    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"Cleared {len(deleted)} messages.", ephemeral=True)


@bot.tree.command(name="skip", description="Skip the current song")
async def skip(interaction: discord.Interaction):
    await interaction.response.defer()

    voice_client = interaction.guild.voice_client
    if not voice_client or not voice_client.is_playing():
        return await interaction.followup.send("Nothing is playing right now!")

    voice_client.stop()
    await interaction.followup.send("Skipped!")


@bot.tree.command(name="queue", description="Show the current queue")
async def show_queue(interaction: discord.Interaction):
    if interaction.guild.id not in bot.queues or bot.queues[interaction.guild.id].is_empty:
        return await interaction.response.send_message("The queue is empty!")

    queue = bot.queues[interaction.guild.id]

    embed = discord.Embed(title="Music Queue", color=discord.Color.blue())

    if queue.now_playing:
        embed.add_field(name="Now Playing", value=queue.now_playing.title, inline=False)

    if len(queue.upcoming) > 0:
        upcoming = "\n".join(f"{i + 1}. {track.title}" for i, track in enumerate(queue.upcoming))
        embed.add_field(name="Up Next", value=upcoming, inline=False)

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="pause", description="Pause the current song")
async def pause(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if not voice_client or not voice_client.is_playing():
        return await interaction.response.send_message("Nothing is playing right now!")

    if voice_client.is_paused():
        return await interaction.response.send_message("Already paused!")

    voice_client.pause()
    await interaction.response.send_message("Paused the music!")


@bot.tree.command(name="resume", description="Resume the current song")
async def resume(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if not voice_client or not voice_client.is_paused():
        return await interaction.response.send_message("Nothing is paused right now!")

    voice_client.resume()
    await interaction.response.send_message("Resumed the music!")


@bot.tree.command(name="stop", description="Stop the music and clear the queue")
async def stop(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if not voice_client or not (voice_client.is_playing() or voice_client.is_paused()):
        return await interaction.response.send_message("Nothing is playing right now!")

    if interaction.guild.id in bot.queues:
        bot.queues[interaction.guild.id].clear()

    voice_client.stop()
    await interaction.response.send_message("Stopped the music and cleared the queue!")


@bot.tree.command(name="disconnect", description="Disconnect the bot from voice")
async def disconnect(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if not voice_client:
        return await interaction.response.send_message("I'm not connected to a voice channel!")

    if interaction.guild.id in bot.queues:
        bot.queues[interaction.guild.id].clear()

    await voice_client.disconnect()
    await interaction.response.send_message("Disconnected from voice channel!")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")


if __name__ == "__main__":
    bot.run(token=os.environ["TOKEN"])