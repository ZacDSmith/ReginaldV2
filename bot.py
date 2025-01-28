import os
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import ollama


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
            await tree.sync()
            print("Synced")
        except Exception as e:
            await tree.sync()
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

    client.run(token=token)
