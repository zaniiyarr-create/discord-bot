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
    
    # چوونە ناو کانالێ ب خۆکار دەما بۆت دەست پێدکەت
    if VOICE_CHANNEL_ID != 0:
        channel = bot.get_channel(VOICE_CHANNEL_ID)
        if channel and isinstance(channel, discord.VoiceChannel):
            try:
                await channel.connect(self_deaf=True)
                print(f"بۆت ب سەرکەفتن چوو ناو کانالا: {channel.name} و بەخۆ میوت بوو.")
            except Exception as e:
                print(f"نەشییا بچیتە ناو کانالێ: {e}")

# فەرمان بۆ چوونە ناو وێ کانالا تو تێدا 
@bot.command()
async def join(ctx):
    if ctx.author.voice:
        channel = ctx.author.voice.channel
        # self_deaf=True وای ل بۆت دکەت کە ل ناو Callێدا میوت و بێدەنگ بیت
        await channel.connect(self_deaf=True)
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

# فەرمانا خودکار بۆ دروستکرنا سێرڤەری وەک وێنەیان
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

    for cat_name, channels in structure.items():
        category = await guild.create_category(cat_name)
        for ch_type, ch_name in channels:
            if ch_type == "text":
                await guild.create_text_channel(ch_name, category=category)
            elif ch_type == "voice":
                await guild.create_voice_channel(ch_name, category=category)

    await ctx.send("✅ هەموو کاتەگۆری و کانال ب سەرکەفتن وەک وێنەیان هاتنە دروستکرن!")

@setup.error
async def setup_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ تو دەستهەڵاتا Adminـت نینە بۆ ڤی کاری!")

# وەرگرتنا Token-ا بۆتێ
TOKEN = os.getenv("TOKEN")

if not TOKEN:
    print("هەڵە: Token نەهاتییە دیتن! دڤێت ل Railway پشکا Variables زێدە بکەی.")
else:
    bot.run(TOKEN)
