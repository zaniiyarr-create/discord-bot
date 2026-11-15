import os
import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ID یا کانالا دەنگی یا تە:
VOICE_CHANNEL_ID = 1174375300134613089

@bot.event
async def on_ready():
    print(f"بۆت ب سەرکەفتن وەک {bot.user} ڤەبوو!")
    if VOICE_CHANNEL_ID != 0:
        channel = bot.get_channel(VOICE_CHANNEL_ID)
        if channel and isinstance(channel, discord.VoiceChannel):
            try:
                await channel.connect(self_deaf=True)
                print(f"بۆت ب سەرکەفتن چوو ناو کانالا: {channel.name}")
            except Exception as e:
                print(f"تێبینی دەنگ: {e}")

@bot.command()
async def join(ctx):
    if ctx.author.voice:
        channel = ctx.author.voice.channel
        try:
            if ctx.voice_client:
                await ctx.voice_client.move_to(channel)
            else:
                await channel.connect(self_deaf=True)
            await ctx.send(f"✅ بۆت هاتە ناو کانالا: {channel.name}")
        except Exception as e:
            await ctx.send(f"❌ هەڵە لە چوونە ناو تێلێ: {e}")
    else:
        await ctx.send("❌ تو لە ناو هیچ Voice Channelـەکدا نینەی!")

@bot.command()
async def leave(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("✅ بۆت لە Call دەرکەوت.")
    else:
        await ctx.send("❌ بۆت لە هیچ Callـەکدا نینە!")

@bot.command()
@commands.has_permissions(administrator=True)
async def setup(ctx):
    guild = ctx.guild
    await ctx.send("⚙️ دەست پێکر بۆ دروستکرنا کانال و کاتەگۆریان... هندیەکێ بوەستە.")

    structure = {
        "---❖--- WELCOME ---❖---": [
            ("text", "📜-rules"),
            ("text", "🌻-welcome"),
            ("text", "🔮-boosts")
        ],
        "---❖--- GENERAL ---❖---": [
            ("text", "💬-public-chat"),
            ("voice", "🔊 PUBLIC VOICE"),
            ("voice", "🔒 FAMILY 96")
        ],
        "---❖--- BOT INFO ---❖---": [
            ("text", "🤖-bot-use"),
            ("text", "🎰-owo"),
            ("voice", "🎷 MUSIC"),
            ("voice", "🕋 QURAN")
        ],
        "---❖--- VIP ---❖---": [
            ("voice", "🔊 96"),
            ("voice", "🔴 VIP 1"),
            ("voice", "🟡 VIP 2"),
            ("voice", "🔵 VIP 3"),
            ("voice", "⚪ VIP 4"),
            ("voice", "⚫ VIP 5"),
            ("voice", "🟢 VIP 6"),
            ("voice", "🟠 VIP 7"),
            ("voice", "🔴 VIP 8")
        ],
        "---❖--- GALLERY ---❖---": [
            ("text", "💖-family-96"),
            ("text", "📷-photo"),
            ("text", "✒️-editors-video-photo"),
            ("text", "📹-video"),
            ("text", "❤️-payv"),
            ("text", "🙏-gotnen-xere"),
            ("text", "🤡-ai")
        ],
        "---❖--- POLICE ---❖---": [
            ("voice", "🔒 POLICE VC"),
            ("voice", "🔒 POLICE2-VC"),
            ("voice", "🔒 POLICE-COMBON")
        ],
        "---❖--- MECHANIC ---❖---": [
            ("text", "🔧-rules-mechanic"),
            ("voice", "🔒 Vc Mechanic")
        ],
        "---❖--- TAXI ---❖---": [
            ("text", "🚕-taxi")
        ],
        "---❖--- GAMING ---❖---": [
            ("voice", "🔒 CS2"),
            ("voice", "🎮 AMONG US"),
            ("voice", "🎮 PUBG"),
            ("voice", "🎮 AMONG US 4"),
            ("voice", "🎮 battlefield"),
            ("voice", "🖥️ GTA"),
            ("voice", "📱 PS MOBILE")
        ],
        "---❖--- MINICRAFT ---❖---": [
            ("text", "✅-minecraft-role"),
            ("text", "📢-minecraft-news"),
            ("text", "📌-ip-port"),
            ("text", "🗿-m-whilist"),
            ("voice", "🔊 Minecraff")
        ],
        "---❖--- LADIES ---❖---": [
            ("voice", "🔒 LADIES VIP"),
            ("voice", "🔒 LADIES VOICE")
        ],
        "---❖--- STAFF ---❖---": [
            ("voice", "🔒 BOMA ZAWQE"),
            ("voice", "🔒 🎭 STAFF VOICE"),
            ("voice", "🔒 👥 FRIENDS")
        ],
        "---❖--- MOVIES ---❖---": [
            ("text", "🎦-movies"),
            ("voice", "🎦 MOVIES VC"),
            ("voice", "🎮 FIFA"),
            ("voice", "🔒 FC-25")
        ]
    }

    try:
        for cat_name, channels in structure.items():
            category = await guild.create_category(cat_name)
            for ch_type, ch_name in channels:
                if ch_type == "text":
                    await guild.create_text_channel(ch_name, category=category)
                elif ch_type == "voice":
                    await guild.create_voice_channel(ch_name, category=category)
        await ctx.send("✅ هەموو کاتەگۆری و کانال ب سەرکەفتن وەک وێنەیان هاتنە دروستکرن!")
    except discord.Forbidden:
        await ctx.send("❌ هەڵە: بۆت دەستهەڵاتا پێدڤی (`Administrator` یا `Manage Channels`) لە سێرڤەریدا نینە!")
    except Exception as e:
        await ctx.send(f"❌ هەڵەیەک ڕووی دا: {e}")

@setup.error
async def setup_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ تو دەستهەڵاتا Adminـت نینە بۆ کارپێکرنا ڤی فەرمانێ!")

TOKEN = os.getenv("TOKEN")
if TOKEN:
    bot.run(TOKEN)
