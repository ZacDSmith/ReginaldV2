import os
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv


def run_bot():
    load_dotenv()
    TOKEN = os.getenv('TOKEN')
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

    @client.event
    async def on_ready():
        print("Bot is running")
        try:
            await tree.sync()
        except Exception as e:
            print(e)

    @tree.command(name="hello")
    async def hello(interaction: discord.Integration):
        await interaction.response.send_message(f"Hey {interaction.user.mention}! This is a slash command!")

    @tree.command(name="clear")
    async def clear(ctx: commands.Context, amount: int,):
        try:
            await ctx.response.send_message(f"Clearing {amount} messages...", ephemeral=True)
            channel = await ctx.guild.fetch_channel(ctx.channel.id)
            await discord.channel.TextChannel.purge(channel, limit=amount)

        except Exception as e:
            print(e)

    client.run(token=TOKEN)
