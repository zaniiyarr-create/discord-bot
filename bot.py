import os
import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")

@bot.command(name="join")
async def join(ctx):
    await ctx.send("✅ بۆت ئامادەیە و لە خزمەت تەدایە!")

# وەرگرتنی تۆکەن لە ڕەیلوەی بێ ئەوەی لە کۆدەکەدا بشکێت
TOKEN = os.getenv("DISCORD_TOKEN")
bot.run(TOKEN)
