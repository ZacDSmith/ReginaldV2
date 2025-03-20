import os
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import ollama
import yt_dlp
import asyncio

FFMPEG_OPTIONS = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5', 'options': '-vn'}
voice_clients = {}
yt_dl_options = {"format": "bestaudio/best"}
ytdl = yt_dlp.YoutubeDL(yt_dl_options)


def run_bot():
    load_dotenv()
    token = os.getenv('TOKEN')
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)
    clients = ollama.Client()

    @client.event
    async def on_ready():
        print("Bot is running")
        try:
            await tree.sync(guild=discord.Object(id=os.getenv('ID')))
            await tree.sync()
            print("Connected")
        except Exception as e:
            print(e)

    def split_string_every_2000_chars(input_string):
        return [input_string[i:i + 2000] for i in range(0, len(input_string), 2000)]

    @tree.command(name="google", description="ai chatbot that can write code.")
    async def google(interaction: discord.Integration, prompt: str):
        model = "deepseek-r1:8b"
        await interaction.response.defer()
        try:
            response = clients.generate(model=model, prompt=prompt)
            input_string = response.response  # replace with your string
            chunks = split_string_every_2000_chars(input_string)
            await interaction.followup.send(f"Prompt: {prompt}. \n Response:")
            for chunk in chunks:
                await interaction.followup.send(f"{chunk}")
            # await interaction.followup.send(f"Prompt: {prompt}. \n Response: {response.response}")
        except Exception as e:
            print("Error occurred: ", e)

    @tree.command(name="clear")
    async def clear(ctx: commands.Context, amount: int):
        try:
            await ctx.response.send_message(f"Clearing {amount} messages...", ephemeral=True)
            channel = await ctx.guild.fetch_channel(ctx.channel.id)
            await discord.channel.TextChannel.purge(channel, limit=amount)

        except Exception as e:
            print(e)

    @tree.command(name="play", description="Play a song from a URL")
    async def play(interaction: discord.Interaction, url: str):
        user = interaction.user

        # Check if the user is in a voice channel
        if not user.voice:
            await interaction.response.send_message("You are not in a voice channel!", ephemeral=True)
            return

        # Connect to the user's voice channel
        try:
            voice_channel = user.voice.channel
            voice_client = await voice_channel.connect()
            voice_clients[interaction.guild.id] = voice_client
        except Exception as e:
            print(e)

        # Respond to the interaction
        await interaction.response.send_message(f"{user.name} is playing: {url}")

        # Extract audio from the URL
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=False))
            song = data['url']
            player = discord.FFmpegPCMAudio(song, **FFMPEG_OPTIONS)
            voice_clients[interaction.guild.id].play(player)
        except Exception as e:
            print(e)
            await interaction.followup.send("Failed to play the song.", ephemeral=True)

    client.run(token=token)
