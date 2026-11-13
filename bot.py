import os
import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ID یا کانالا دەنگی یا تە:
VOICE_CHANNEL_ID = 1174375300134613089

@bot.event
async def on_ready():
    print(f"بۆت ب سەرکەفتن وەک {bot.user} ڤەبوو!")
    
    # چوونە ناو کانالێ ب خۆکار دەما بۆت دەست پێدکەت
    if VOICE_CHANNEL_ID != 0:
        channel = bot.get_channel(VOICE_CHANNEL_ID)
        if channel and isinstance(channel, discord.VoiceChannel):
            try:
                await channel.connect(self_deafen=True)
                print(f"بۆت ب سەرکەفتن چوو ناو کانالا: {channel.name} و بەخۆ میوت بوو.")
            except Exception as e:
                print(f"نەشییا بچیتە ناو کانالێ: {e}")

# فەرمان بۆ چوونە ناو وێ کانالا تو تێدا 
@bot.command()
async def join(ctx):
    if ctx.author.voice:
        channel = ctx.author.voice.channel
        # self_deafen=True وای ل بۆت دکەت کە ل ناو Callێدا میوت و بێدەنگ بیت
        await channel.connect(self_deafen=True)
        await ctx.send(f"ئەز هاتمە ناو کانالا: {channel.name} و میوت مە.")
    else:
        await ctx.send("تۆ نەد ناو چ Call یا یان Voice Channel دا!")

# فەرمان بۆ دەرکەتنا بۆتی ژ Callێ
@bot.command()
async def leave(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("ئەز ژ Call دەرکەتم.")
    else:
        await ctx.send("بۆت د ناو چ Call دا نینە!")

# وەرگرتنا Token-ا بۆتێ ژ پشکا Environment Variables یا Railway
TOKEN = os.getenv("TOKEN")

if not TOKEN:
    print("هەڵە: Token نەهاتییە دیتن! دڤێت ل Railway پشکا Variables زێدە بکەی.")
else:
    bot.run(TOKEN)
