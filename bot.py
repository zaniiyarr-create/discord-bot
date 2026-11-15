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

@bot.command(name="setup")
@commands.has_permissions(administrator=True)
async def setup(ctx):
    guild = ctx.guild
    await ctx.send("🔄 سێرڤەر دەست بە پاککردنەوە و ڕێکخستن دەکات...")
    for channel in guild.channels:
        if "FRIENDS" not in channel.name.upper():
            try:
                await channel.delete()
            except Exception as e:
                print(f"Could not delete {channel.name}: {e}")

    category_name = "── • 👑 LOBBY • ──"
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }
    new_category = await guild.create_category(category_name, overwrites=overwrites)
    await guild.create_text_channel("general-chat", category=new_category)
    await guild.create_text_channel("bot-commands", category=new_category)
    await ctx.send("✨ سێرڤەر بە سەرکەوتوویی تەسفیە بوو و کاتێگۆرییە نوێیەکە دروست کرا!")

# خوێندنەوەی ڕاستەوخۆ لە متغیڕێن ڕەیلوەی
bot.run(os.getenv("TOKEN"))
