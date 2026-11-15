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

# ئەمە فەرمانەیە بۆ ڕێکخستنی سێرڤەری تو و سڕینەوەی کۆنەکان بە بێ دەستکاریکردنا ڤۆیسا تە
@bot.command(name="setup")
@commands.has_permissions(administrator=True)
async def setup(ctx):
    guild = ctx.guild
    await ctx.send("🔄 سێرڤەر دەست بە پاککردنەوە و ڕێکخستن دەکات...")

    # ١. سڕینەوەی هەمی کاتێگۆری و چانالێن کەڤن ژبلی 🫂 FRIENDS | VOICE CALL
    for channel in guild.channels:
        if "FRIENDS" not in channel.name.upper():
            try:
                await channel.delete()
            except Exception as e:
                print(f"Could not delete {channel.name}: {e}")

    # ٢. دروستکرنا کاتێگۆریا نوێ و خاوێن
    category_name = "── • 👑 LOBBY • ──"
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }
    new_category = await guild.create_category(category_name, overwrites=overwrites)

    # ٣. دروستکرنا چانالێن سەرەتایی
    await guild.create_text_channel("general-chat", category=new_category)
    await guild.create_text_channel("bot-commands", category=new_category)

    await ctx.send("✨ سێرڤەر بە سەرکەوتوویی تەسفیە بوو و کاتێگۆرییە نوێیەکە دروست کرا!")

bot.run("MTU3MDkxODY3MTgwNTcxODY5OQ.GqW91u.dL2UbQIYWvKi1iZRBOmMsopmYN9_x4-DthS4WQ")
