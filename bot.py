import os
import logging
import io
import re
import shutil
import tempfile
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Optional
from pathlib import Path as _Path

import discord
from discord import app_commands
from discord.ext import commands

# -------------------- Configuration --------------------
TOKEN = os.getenv("DISCORD_TOKEN")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("friends-village")

DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "/data/friends_village.db"))
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
if DATABASE_PATH.parent == Path("/data") and not os.path.ismount("/data"):
    log.warning("The /data path is not detected as a mounted volume. Configure a Railway Volume at /data for persistent storage.")
AUDIO_ROOT = Path(os.getenv("AUDIO_ROOT", "/data/audio"))
AUDIO_ROOT.mkdir(parents=True, exist_ok=True)
MAX_AUDIO_BYTES = 20 * 1024 * 1024  # 20 MB per uploaded track/clip
ALLOWED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".flac"}

intents = discord.Intents.default()
intents.guilds = True
intents.members = True  # Enable Server Members Intent in Discord Developer Portal.
intents.message_content = True  # Required for AutoMod and XP.

# Message Content Intent is used by AutoMod and XP; enable it in the Developer Portal.
class FriendsVillageBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._guild_commands_synced = False

    async def setup_hook(self):
        """Register persistent component views. Guild commands sync after the guild cache is ready."""
        self.add_view(TicketPanelView())
        self.add_view(CloseTicketView())

    async def sync_guild_commands(self):
        """Publish commands to each connected server for near-immediate availability."""
        if self._guild_commands_synced:
            return
        if not self.guilds:
            log.warning("No guilds are cached yet; slash-command sync will retry on the next ready event")
            return

        successful = 0
        for guild in self.guilds:
            try:
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                log.info(
                    "Successfully synced %s application command(s) to guild %s (%s): %s",
                    len(synced), guild.name, guild.id,
                    ", ".join(command.name for command in synced),
                )
                successful += 1
            except discord.HTTPException:
                log.exception("Failed to sync application commands to guild %s (%s)", guild.name, guild.id)

        self._guild_commands_synced = successful == len(self.guilds)


bot = FriendsVillageBot(command_prefix="!", intents=intents)


# -------------------- SQLite configuration --------------------
def db_connect():
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY,
                welcome_channel_id INTEGER
            )
        """)
        conn.execute("""CREATE TABLE IF NOT EXISTS member_settings (
            guild_id INTEGER PRIMARY KEY, log_channel_id INTEGER, autorole_id INTEGER,
            automod_enabled INTEGER NOT NULL DEFAULT 1, leveling_enabled INTEGER NOT NULL DEFAULT 1
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            moderator_id INTEGER NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS member_xp (
            guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, xp INTEGER NOT NULL DEFAULT 0,
            level INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (guild_id,user_id)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS mod_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            moderator_id INTEGER NOT NULL, action TEXT NOT NULL, reason TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        # V5.1 community profile and safe self-assignable role configuration.
        conn.execute("""CREATE TABLE IF NOT EXISTS member_profiles (
            guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            bio TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (guild_id, user_id)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS self_assignable_roles (
            guild_id INTEGER NOT NULL, role_id INTEGER NOT NULL,
            added_by INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (guild_id, role_id)
        )""")
        conn.commit()


def set_welcome_channel(guild_id: int, channel_id: int):
    with db_connect() as conn:
        conn.execute("""
            INSERT INTO guild_settings (guild_id, welcome_channel_id)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                welcome_channel_id = excluded.welcome_channel_id
        """, (guild_id, channel_id))
        conn.commit()


def get_welcome_channel(guild_id: int) -> Optional[int]:
    with db_connect() as conn:
        row = conn.execute(
            "SELECT welcome_channel_id FROM guild_settings WHERE guild_id = ?",
            (guild_id,),
        ).fetchone()
    return int(row["welcome_channel_id"]) if row and row["welcome_channel_id"] else None



# -------------------- Luxury edition helpers --------------------
GOLD = discord.Color.from_rgb(212, 175, 55)
BRONZE = discord.Color.from_rgb(145, 110, 30)
_spam_times = {}
_xp_cooldowns = {}

def settings_for(guild_id: int) -> dict:
    with db_connect() as conn:
        conn.execute("INSERT OR IGNORE INTO member_settings (guild_id) VALUES (?)", (guild_id,))
        row = conn.execute("SELECT * FROM member_settings WHERE guild_id=?", (guild_id,)).fetchone()
        conn.commit()
    return dict(row)

def set_setting(guild_id: int, field: str, value):
    if field not in {"log_channel_id", "autorole_id", "automod_enabled", "leveling_enabled"}:
        raise ValueError("Unsupported setting")
    with db_connect() as conn:
        conn.execute("INSERT OR IGNORE INTO member_settings (guild_id) VALUES (?)", (guild_id,))
        conn.execute(f"UPDATE member_settings SET {field}=? WHERE guild_id=?", (value, guild_id))
        conn.commit()

def xp_threshold(level: int) -> int:
    return 100 * level * (level + 1)

async def audit(guild: discord.Guild, title: str, description: str):
    channel_id = settings_for(guild.id).get("log_channel_id")
    channel = guild.get_channel(channel_id or 0)
    if isinstance(channel, discord.TextChannel):
        try:
            embed = discord.Embed(title=f"✦ {title}", description=description[:4000], color=GOLD,
                                  timestamp=discord.utils.utcnow())
            embed.set_footer(text="Friends Village • Luxury Audit")
            await channel.send(embed=embed)
        except discord.HTTPException:
            log.exception("Unable to send audit log")

# -------------------- Helpers --------------------
async def ensure_category(
    guild: discord.Guild,
    name: str,
    overwrites: Optional[dict] = None,
) -> discord.CategoryChannel:
    # Additive setup: never rewrite an existing category's permissions.
    existing = discord.utils.get(guild.categories, name=name)
    if existing:
        return existing
    return await guild.create_category(name, overwrites=overwrites, reason="Friends Village setup")


async def ensure_text_channel_in_category(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    name: str,
    topic: Optional[str] = None,
) -> discord.TextChannel:
    # Search the whole guild to avoid duplicates. Existing channels are never moved,
    # renamed, or edited, even when they currently live in another category.
    existing = discord.utils.find(
        lambda ch: isinstance(ch, discord.TextChannel) and ch.name == name,
        guild.channels,
    )
    if existing:
        return existing
    guide = BADINI_CHANNEL_GUIDES.get(name)
    badini_topic = guide[1] if guide else topic
    return await guild.create_text_channel(
        name,
        category=category,
        topic=badini_topic,
        reason="Friends Village v4.2.1 Badini channel guide",
    )


async def ensure_text_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    name: str,
    topic: Optional[str] = None,
) -> discord.TextChannel:
    # Search the whole guild, not only this category, so rerunning /setup
    # won't create a duplicate if a channel was moved to another category.
    existing = discord.utils.find(
        lambda ch: isinstance(ch, discord.TextChannel) and ch.name == name,
        guild.channels,
    )
    if existing:
        # Preserve the existing channel exactly as it is; do not move or edit it.
        return existing
    guide = BADINI_CHANNEL_GUIDES.get(name)
    badini_topic = guide[1] if guide else topic
    return await guild.create_text_channel(
        name,
        category=category,
        topic=badini_topic,
        reason="Friends Village setup with Badini channel guide",
    )


async def ensure_voice_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    name: str,
) -> discord.VoiceChannel:
    existing = discord.utils.find(
        lambda ch: isinstance(ch, discord.VoiceChannel) and ch.name == name,
        guild.channels,
    )
    if existing:
        # Preserve the existing voice channel and its category/permissions.
        return existing
    return await guild.create_voice_channel(
        name,
        category=category,
        reason="Friends Village setup",
    )


async def ensure_forum_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    name: str,
    topic: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> discord.ForumChannel:
    # CategoryChannel has .channels, not .forum_channels, in discord.py.
    # Search guild.channels so forum channels are found regardless of category.
    existing = discord.utils.find(
        lambda ch: isinstance(ch, discord.ForumChannel) and ch.name == name,
        guild.channels,
    )
    tag_objects = [discord.ForumTag(name=tag[:20]) for tag in (tags or [])[:20]]
    if existing:
        # Existing forum channels and user-managed tags are left untouched.
        return existing
    return await guild.create_forum(
        name,
        category=category,
        topic=topic,
        available_tags=tag_objects,
        reason="Friends Village setup",
    )


async def ensure_role(guild: discord.Guild, name: str, color: discord.Color) -> discord.Role:
    existing = discord.utils.get(guild.roles, name=name)
    if existing:
        return existing
    return await guild.create_role(
        name=name,
        color=color,
        permissions=discord.Permissions.none(),
        hoist=False,
        mentionable=False,
        reason="Friends Village access roles",
    )


async def send_ticket_panel(channel: discord.TextChannel):
    # Avoid posting a duplicate panel when /setup is run again.
    async for message in channel.history(limit=30):
        if message.author == bot.user and message.embeds:
            if message.embeds[0].title == "Friends Village Support":
                return
    embed = discord.Embed(
        title="یارمەتییا Friends Village",
        description="پێویستت ب یارمەتییە؟ ل دوگمێ خوارێ کرتە بکە بۆ دروستکرنا تیکەتەکا تایبەت و کێشەیا خۆ ڕوون بکە.",
        color=GOLD,
    )
    await channel.send(embed=embed, view=TicketPanelView())


# -------------------- Badini channel guides --------------------
# Each guide is posted once and pinned. Existing channel names, positions,
# permissions, topics and messages are not edited or deleted.
BADINI_CHANNEL_GUIDES = {
    "👋・welcome": ("بەخێرهاتی بۆ گوندێ هەڤالان", "ب خێر هاتی بۆ کۆمەڵگا یا Friends Village! ل ڤێرە پەیامێن بەخێرهاتن و زانیارییێن سەرەکی دبینیت. ژ بوو دەستپێکرنێ، یاسایێن سێرڤەری بخوینە و رولێ گونجاو هەلبژێرە. هیڤیدارین کاتەکە تە خۆش بیت! 💛"),
    "📜・server-rules": ("یاسایێن سێرڤەری", "تکایە هەمی یاسایێن ڤێرە بخوینە پێش کو دەست ب چاتکرنێ بکەی. ڕێزگرتن ژ هەمی ئەندامان، نەکرنا سپام و لینکێن نەخوازراو، و پاراستنا ئارامییا کۆمەڵگایێ پێویستن. بەشدارییا تە گرنگە؛ با ژ بوو هەمیان شووەکەکێ خۆش بهێلیت."),
    "📢・announcements": ("ئاگەهداری و هەواڵێن فەرمی", "ل ڤێرە تەنێ ئاگەهداری و هەواڵێن فەرمی یێن Friends Village دێ بهێنە بڵاوکرن. بۆ زانینا نووترین گۆڕانکاری و چالاکییان، ئەم چانەلە بەدواداچوون بکە."),
    "🎭・roles": ("رولێن خۆ هەلبژێرە", "ل ڤێرە رولێن گونجاو بۆ خۆ هەلبژێرە، دا بتوانی چانەل و چالاکییێن پەیوەندیدار ببینی. ئەگەر رولەکێ نەدیت یان پێویستت ب یارمەتی بوو، پەیوەندیێ ب ستافێ بکە."),
    "💭・general-chat": ("چاتا گشتی", "ل ڤێرە دەتوانی ل گەل هەمی ئەندامان ب ئازادی چات بکەی، پرسیاران بکەی و بیرۆکەیان بڵاڤ بکەی. تکایە ڕێزێ ل هەمیان بگرە و چاتێ ل بابەتێن گشتی ڕابگرە."),
    "🤝・introductions": ("خۆ بناسینە", "ب خێر هاتی! ل ڤێرە خۆ بناسینە: ناڤێ تە چییە، حەز ژ چی دکەی و کام یاری یان چالاکی دڤێت؟ زانیارییێن نهێنی و کەسی بڵاو مەکە."),
    "📸・media-gallery": ("پیشاندانا وێنە و ڤیدیۆیان", "وێنە، ڤیدیۆ و داهێنانێن خۆ ل ڤێرە بڵاو بکە. تکایە تەنێ ناوەڕۆکێن گونجاو بڵاو بکە و مافێ خاوەنێ وێنە و ڤیدیۆیان بپارێزە."),
    "💡・suggestions": ("پێشنیارێن تە", "بیرۆکە و پێشنیارێن خۆ بۆ باشترکرنا Friends Village ل ڤێرە بنڤیسە. باشترە پێشنیارا تە ڕوون بیت و بڵێیت چۆن دێ یارمەتی کۆمەڵگایێ بکەت."),
    "🤖・bot-commands": ("چانەلێ فەرمانێن بۆتێ", "فەرمانێن بۆتێ ل ڤێرە بەکار بینە. بۆ دیتنا فەرمانێن بەردەست، /help بنڤیسە. تکایە فەرمانان ب دووبارە و ب شێوەیێ سپام بەکار نەهێنە."),
    "🚀・boosts": ("سوپاس بۆ Boost ـکرنێ!", "ئەڤ چانەلە بۆ سوپاسگوزاری و ئاگەهداریا Boost ـێن سێرڤەرییە. ئەگەر سێرڤەرێت Boost کرد، سوپاس بۆ پشتگیرییا تە! هەر Boost یارمەتی ددەت Friends Village گەشە بکەت. 💜"),
    "📱・social-media": ("تۆڕێن کۆمەڵایەتی", "ل ڤێرە لینک و هەژمارێن فەرمی یێن Friends Village دێ بهێنە دانان. لینکێن خۆ یان یێن پەیوەندیدار ب ڕێزەوە بڵاو بکە؛ لینکێن گومانلێکراو یان زیانبار مەکە."),
    "🎮・gaming-chat": ("گفتوگۆیا یارییان", "ل سەر یارییێن جۆراوجۆر، نووکرن، ئەزموون و هەواڵان گفتوگۆ بکە. بۆ هەر یارییەکا تایبەت، چانەلێ تایبەت بەکار بینە."),
    "👥・find-a-team": ("هاوتیم بدۆزە", "دڤێت ل گەل هەڤالان یاری بکەی؟ ناڤێ یاریێ، پلاتفۆرمێ و کاتیێ یاریێ بنڤیسە. تکایە ڕێزگرتن و هاوکاری ل گەل یاریزانان بکە."),
    "🏆・game-results": ("ئەنجام و سەرکەفتن", "ئەنجامێن یارییان، سکۆر، سەرکەفتن و دەستکەفتێن خۆ ل ڤێرە بڵاو بکە. دەتوانی وێنە یان کلیپێک ژ ئەنجامێ زیاد بکەی."),
    "📰・gaming-news": ("هەواڵێن یارییان", "هەواڵ، نووکرن و زانیارییێن نوو یێن دنیایا یارییان ل ڤێرە بڵاو بکە. سەرچاوەیا هەواڵێ بنڤیسە ئەگەر هەیە."),
    "⚽・pes-efootball": ("چاتا PES و eFootball", "ل سەر PES و eFootball گفتوگۆ بکە، ماتچێن دۆستانە ڕێک بخە، تیم و یاریزانان باس بکە و سکۆرێن خۆ بڵاو بکە. بۆ یاری هاوبەش، ناڤێ خۆ و کاتیێ بەردەست بنڤیسە."),
    "🪖・pubg": ("چاتا PUBG", "هاوتیمی PUBG بدۆزە، سکۆادێک پێک بینە و کاتیێ یاریێ ڕێک بخە. مۆدێ یاریێ و ناڤێ خۆ بنڤیسە؛ ل گەل یاریزانان ب ڕێز و هاوکاری ڕەفتار بکە."),
    "🍃・among-us": ("چاتا Among Us", "ل ڤێرە بۆ Among Us کەس بدۆزە و گرووپێ یاریێ پێک بینە. کاتیێ یاریێ ڕێک بخە و ئەزموونێن خۆ ل گەل ئەندامان پارڤە بکە."),
    "🧱・roblox": ("چاتا Roblox", "یاریێن Roblox یێن حەژێکری، مابەست و داهێنانێن خۆ بڵاو بکە. یاریێن نوو پێشنیار بکە و ل گەل ئەندامان یاری بکە."),
    "👻・horror-games": ("چاتا یاریێن ترسناک", "ل سەر یاریێن ترسناک گفتوگۆ بکە، پێشنیار و ئەزموونێن خۆ پارڤە بکە. ئەگەر ناوەڕۆکێک سپۆیلەر هەیە، پێشتر ئاگەهداری بکە."),
    "🎨・art-gallery": ("گالەریا هونەرێ", "کارێ هونەری، وێنەکێشان، دیزاین و داهێنانێن خۆ ل ڤێرە پیشان بدە. ڕێزێ ل کارێ هەڤالان بگرە و ناڤێ خاوەنێ کارێ بنڤیسە."),
    "📷・photography": ("هونەرا وێنەگرتنێ", "وێنەیێن خۆ و ساتێن جوان ل ڤێرە پارڤە بکە. ئەگەر وێنەکە یا کەسەکێ دییە، پێش بڵاوکرنێ ڕەزامەندیا وی وەرگرە."),
    "🎬・clips-and-videos": ("کلیپ و ڤیدیۆ", "کلیپ و ڤیدیۆیێن خۆ یان ناوەڕۆکێن گونجاو ل ڤێرە بڵاو بکە. تکایە ناوەڕۆکێن نەگونجاو و لینکێن گومانلێکراو مەهێنە."),
    "🎵・music-corner": ("گۆشەیا موزیکێ", "ل سەر موزیک، هونەرمەند و سترانێن حەژێکری گفتوگۆ بکە و پێشنیارێن خۆ بدە. مافێ خاوەنێ کاران بپارێزە."),
    "😂・memes-and-fun": ("خۆشی و پێکەنین", "میم، گاڤ و ناوەڕۆکێن خۆش ل ڤێرە پارڤە بکە. با خۆشی بێت، بەڵام کەس مەئازێنە و گاڤێن دژوار مەکە."),
    "🏡・family-village": ("یادگاریێن Family Village", "چیرۆک، یادگاری و ساتێن تایبەت یێن Family Village ل ڤێرە پارڤە بکە. با ئەڤ چانەلە ببیتە ئەرشیڤەکا جوان بۆ یادگاریێن کۆمەڵگایێ."),
    "📸・photos": ("چانەلێ وێنان", "تەنێ وێنەیێن خۆ یان وێنەیێن ڕێپێدراو ل ڤێرە بڵاو بکە. بۆ هەر وێنەیەک دەتوانی ڕوونکرنەوەیەکا کورت زیاد بکەی."),
    "🎬・videos": ("چانەلێ ڤیدیۆیان", "ڤیدیۆ و کلیپێن خۆ ل ڤێرە بڵاو بکە. تکایە پێش بڵاوکرنێ دڵنیا بە کو ڤیدیۆکە گونجاوە و مافێ خاوەنێ ناڤەڕۆکێ پاراستییە."),
    "💬・payv": ("چانەلێ Payv", "پۆست و ناوەڕۆکێن پەیوەندیدار ب Payv ل ڤێرە پارڤە بکە. تکایە پەیوەندی و گفتوگۆیان ب ڕێزەوە بهێلە و سپام مەکە."),
    "📅・events": ("چالاکی و بۆنە", "زانیاریێن چالاکی و بۆنەیێن Friends Village ل ڤێرە دێ بهێنە بڵاوکرن. بەروار، کات و شێوازێ بەشداریێ بخوینە پێش بەشداریێ."),
    "🎁・giveaways": ("خەڵات و پێشکێشان", "زانیاریێن پێشکێشان و خەڵاتان ل ڤێرە بخوینە. تەنێ ڕێنماییێن فەرمی بەدواداچوون بکە و هیچ زانیارییا نهێنی یان وشەیا نهێنی مەدە."),
    "🏅・competitions": ("پێشبڕکێ و پاڵەوانی", "پێشبڕکێ و پاڵەوانییێن سێرڤەری ل ڤێرە دێ بهێنە ڕێکخستن. یاسایێن پێشبڕکێێ بخوینە و بە ڕەفتارەکا وەرزشی بەشداریێ بکە."),
    "📊・polls": ("دەنگدان و ڕای", "ل دەنگدانان بەشداریێ بکە و ڕایێ خۆ بڵاو بکە. ڕێزێ ل ڕایێ جیاواز یێن ئەندامان بگرە."),
    "🎶・music-requests": ("داخوازییا موزیکێ", "ناڤێ موزیکێ یان داخوازییا خۆ ل ڤێرە بنڤیسە. ئەدمین دکارن فایلێن ڕێپێدراو زیاد بکەن؛ بۆ لیستا موزیکێ /audio-list و بۆ لێدانێ /audio-play بەکار بینە."),
    "📚・music-library": ("کتێبخانەیا موزیکێ", "بۆ دیتنا فایلێن موزیکێ /audio-list kind:Music بەکار بینە. بۆ لێدانا فایلێک، پێویستە ل چانەلەکا دەنگی بیت و /audio-play بەکار بینە."),
    "🕌・quran-library": ("کتێبخانەیا قورئانێ", "ل ڤێرە تلاوەتێن قورئانێ یێن بەرپرسێن سێرڤەری زیادکری دبینیت. بۆ دیتنا فایلان /audio-list kind:Quran بەکار بینە و ب ڕێزەوە گوهداریێ بکە."),
    "🌙・quran-reflections": ("بیر و ئامۆژگاریێن قورئانی", "ل ڤێرە دەتوانی بیر، ئامۆژگاری و گفتوگۆیێن ب ڕێز ل سەر وانەیێن قورئانی پارڤە بکەی. تکایە قورئان و هەمی باوەڕداران ب ڕێز بگرە."),
    "🎛️・soundpad-library": ("کتێبخانەیا Soundpad", "بۆ دیتنا دەنگ و کاریگەرییێن بارکری /audio-list kind:Soundpad بەکار بینە. بۆ لێدانێ ل چانەلەکا دەنگی بیت و /audio-play بەکار بینە."),
    "📤・soundpad-uploads": ("بارکرنا دەنگان", "تەنێ ئەدمین و بەرپرسێن ڕێپێدراو دکارن فایلێن دەنگی بار بکەن. بۆ زیادکرنا فایلێک /audio-upload kind:Soundpad بەکار بینە."),
    "💎・vip-chat": ("چاتا تایبەت یا VIP", "ئەڤ چانەلە تەنێ بۆ ئەندامێن Village VIP و ستافێ ڕێپێدراوە. ڕێزێ ل تایبەتمەندییا ئەندامان بگرە و ناوەڕۆکێ تایبەت دەرڤە مەبڵاو بکە."),
    "📸・vip-media": ("میدیا یا VIP", "وێنە و ڤیدیۆیێن گونجاو ل ناڤ کۆمەڵگا یا VIP پارڤە بکە. ئەڤ ناوەڕۆکە تایبەتە و نابێت بێ ڕەزامەندی بڵاو بکرێتەوە."),
    "💬・friends-chat": ("چاتا Private Friends Club", "ب خێر هاتی بۆ کۆمەڵگا یا تایبەت یا هەڤالان. ل ڤێرە ب ئازادی و ب ڕێز چات بکە و گفتوگۆیێن تایبەت ل ناڤ ئەڤ کۆمەڵگایێ بهێلە."),
    "😂・friends-memes": ("گاڤ و میمێن هەڤالان", "میم و گاڤێن خۆ ل گەل هەڤالێن تایبەت پارڤە بکە. با هەمی کەس پێبکەنن، نە کو کەسێک هەست ب خراپیێ بکەت."),
    "📸・friends-media": ("میدیایا هەڤالان", "وێنە و ڤیدیۆیێن تایبەت ل ڤێرە پارڤە بکە. ناوەڕۆکێ ئەڤ چانەلێ نهێنییە؛ بێ ڕەزامەندی ب دەرڤە مەبڵاو بکە."),
    "🎮・gaming-plans": ("پلانێن یارییا هەڤالان", "بۆ یارییا هاوبەش پلان دابنێ. ناڤێ یاریێ، ڕۆژ و کاتیێ یاریێ بنڤیسە و هەڤالێن خۆ بانگ بکە."),
    "📋・staff-logs": ("تۆمارێن ستافێ", "ئەڤ چانەلە بۆ تۆمار و ئاگەهدارییێن کارێ ستافێیە. تەنێ ئەندامێن ڕێپێدراو دکارن ناوەڕۆکێ ببینن؛ تۆماران مەسڕە و بێ پێویستی دەستکارییان مەکە."),
    "💬・staff-chat": ("چاتا ستافێ", "ئەڤ چانەلە بۆ هاوئاهەنگی و گفتوگۆیا ناڤخۆییا ستافێیە. زانیارییێن ئەندامان ب پارێزراوی بهێلە و ب ڕێز ل گەل یەکتر کار بکە."),
    "🎫・create-a-ticket": ("داخوازییا یارمەتیێ", "پێویستت ب یارمەتییە؟ ل دوگمێ دروستکرنا تیکەتێ کرتە بکە و کێشەیا خۆ ڕوون بکە. تەنێ تۆ و ستافێ ڕێپێدراو دێ بتوانن ئەڤ گفتوگۆیە ببینن."),
}

async def post_badini_channel_guides(guild: discord.Guild):
    """Post and pin one Badini guide in each managed text channel, without editing old content."""
    marker = "Friends Village • Rêberiya Badînî • FV42.1"
    for channel in guild.text_channels:
        guide = BADINI_CHANNEL_GUIDES.get(channel.name)
        if not guide:
            continue
        try:
            async for old_message in channel.history(limit=50):
                if old_message.author == bot.user and old_message.embeds:
                    footer = old_message.embeds[0].footer.text or ""
                    if marker in footer:
                        if not old_message.pinned:
                            await old_message.pin(reason="Rêbera Badînî ya kanalan")
                        break
            else:
                title, description = guide
                embed = discord.Embed(title=f"✦ {title}", description=description, color=GOLD)
                embed.set_footer(text=marker)
                message = await channel.send(embed=embed)
                await message.pin(reason="Rêbera Badînî ya kanalan")
        except discord.Forbidden:
            log.warning("Missing send/pin permissions for Badini guide in #%s", channel.name)
        except discord.HTTPException:
            log.exception("Could not publish Badini guide in #%s", channel.name)


# -------------------- Ticket UI --------------------
class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="دروستکرنا تیکەتێ",
        style=discord.ButtonStyle.primary,
        emoji="🎫",
        custom_id="friends_village:create_ticket",
    )
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("ئەڤ دوگمە تەنێ ل ناڤ سێرڤەرەکێ کار دکەت.", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)

        existing = discord.utils.find(
            lambda c: isinstance(c, discord.TextChannel)
            and c.topic == f"ticket-owner:{interaction.user.id}",
            guild.text_channels,
        )
        if existing:
            return await interaction.followup.send(
                f"تۆ پێشتر تیکەتەکا ڤەکری هەیە: {existing.mention}", ephemeral=True
            )

        category = discord.utils.get(guild.categories, name="🎫・SUPPORT TICKETS")
        if category is None:
            category = await guild.create_category("🎫 TICKETS", reason="Ticket system")

        bot_member = guild.me
        if bot_member is None and bot.user is not None:
            bot_member = guild.get_member(bot.user.id)
        if bot_member is None:
            return await interaction.followup.send("نەشێم دەسەڵاتێن بۆتێ پشتڕاست بکەمەوە. تکایە دووبارە هەوڵ بدە.", ephemeral=True)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                attach_files=True, embed_links=True,
            ),
            bot_member: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                manage_channels=True,
            ),
        }
        safe_name = f"ticket-{interaction.user.name}".lower().replace(" ", "-")[:65]
        safe_name = f"{safe_name}-{str(interaction.user.id)[-6:]}"
        channel = await guild.create_text_channel(
            safe_name,
            category=category,
            overwrites=overwrites,
            topic=f"ticket-owner:{interaction.user.id}",
            reason=f"Support ticket opened by {interaction.user}",
        )
        embed = discord.Embed(
            title="Support ticket",
            description=f"ب خێر هاتی {interaction.user.mention}! تکایە کێشە یان داخوازییا خۆ ڕوون بکە؛ ئەندامەکێ ستافێ دێ یارمەتییا تە بکەت.",
            color=GOLD,
        )
        await channel.send(
            content=interaction.user.mention,
            embed=embed,
            view=CloseTicketView(),
        )
        await interaction.followup.send(f"تیکەتەکا تە ئامادەیە: {channel.mention}", ephemeral=True)


class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="داخستنا تیکەتێ",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="friends_village:close_ticket",
    )
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not channel.topic:
            return await interaction.response.send_message("ئەڤ چانەلە تیکەت نینە.", ephemeral=True)

        if not channel.topic.startswith("ticket-owner:"):
            return await interaction.response.send_message("ئەڤ چانەلە تیکەت نینە.", ephemeral=True)

        owner_id = int(channel.topic.split(":", 1)[1])
        is_owner = interaction.user.id == owner_id
        is_staff = interaction.user.guild_permissions.manage_channels or interaction.user.guild_permissions.administrator
        if not (is_owner or is_staff):
            return await interaction.response.send_message(
                "تەنێ دروستکەرێ تیکەتێ یان بەرپرسێ سێرڤەری دکارن ئەڤ تیکەتە دابخەن.",
                ephemeral=True,
            )

        await interaction.response.send_message("تیکەت دێ نێزیکا داخستنێ بیت.", ephemeral=True)
        await channel.delete(reason=f"Ticket closed by {interaction.user}")


# -------------------- Community audio: music, Quran and soundpad --------------------
AUDIO_KINDS = {"music": "🎵 Music", "quran": "📖 Quran", "soundpad": "🔊 Soundpad"}

def audio_folder(guild_id: int, kind: str) -> Path:
    folder = AUDIO_ROOT / str(guild_id) / kind
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def clean_audio_name(value: str) -> str:
    value = Path(value).name
    value = re.sub(r"[^a-zA-Z0-9 _.-]", "", value).strip(" .")
    return value[:80] or "audio"


@bot.tree.command(name="audio-upload", description="Upload a music track, Quran recitation or Soundpad clip.")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(kind="Choose the audio library", attachment="MP3, WAV, OGG, M4A or FLAC file")
@app_commands.choices(kind=[
    app_commands.Choice(name="Music", value="music"),
    app_commands.Choice(name="Quran", value="quran"),
    app_commands.Choice(name="Soundpad", value="soundpad"),
])
async def audio_upload(interaction: discord.Interaction, kind: app_commands.Choice[str], attachment: discord.Attachment):
    ext = Path(attachment.filename).suffix.lower()
    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        return await interaction.response.send_message("Unsupported audio format. Use MP3, WAV, OGG, M4A or FLAC.", ephemeral=True)
    if attachment.size > MAX_AUDIO_BYTES:
        return await interaction.response.send_message("File is larger than 20 MB. Please upload a smaller audio file.", ephemeral=True)
    folder = audio_folder(interaction.guild_id, kind.value)
    base = clean_audio_name(Path(attachment.filename).stem)
    destination = folder / f"{base}{ext}"
    counter = 2
    while destination.exists():
        destination = folder / f"{base}-{counter}{ext}"
        counter += 1
    try:
        await attachment.save(destination)
    except (discord.HTTPException, OSError):
        log.exception("Audio upload failed")
        return await interaction.response.send_message("Could not save that audio file. Check the bot's storage and try again.", ephemeral=True)
    await interaction.response.send_message(f"Uploaded **{destination.name}** to **{AUDIO_KINDS[kind.value]}**. Use `/audio-list` to see files and `/audio-play` to play one in your current voice channel.", ephemeral=True)


@bot.tree.command(name="audio-list", description="List available music, Quran and Soundpad audio files.")
@app_commands.guild_only()
@app_commands.choices(kind=[
    app_commands.Choice(name="Music", value="music"),
    app_commands.Choice(name="Quran", value="quran"),
    app_commands.Choice(name="Soundpad", value="soundpad"),
])
async def audio_list(interaction: discord.Interaction, kind: app_commands.Choice[str]):
    files = sorted(f.name for f in audio_folder(interaction.guild_id, kind.value).iterdir() if f.is_file())
    description = "\n".join(f"• `{name}`" for name in files[:30]) or "No audio files uploaded yet. Ask a server manager to use `/audio-upload`."
    embed = discord.Embed(title=f"{AUDIO_KINDS[kind.value]} Library", description=description, color=GOLD)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="audio-play", description="Play an uploaded track or sound in your current voice channel.")
@app_commands.guild_only()
@app_commands.choices(kind=[
    app_commands.Choice(name="Music", value="music"),
    app_commands.Choice(name="Quran", value="quran"),
    app_commands.Choice(name="Soundpad", value="soundpad"),
])
@app_commands.describe(kind="Audio library", filename="Exact filename shown by /audio-list")
async def audio_play(interaction: discord.Interaction, kind: app_commands.Choice[str], filename: str):
    member = interaction.user
    voice_state = getattr(member, "voice", None)
    if not voice_state or not voice_state.channel:
        return await interaction.response.send_message("Join a voice channel first.", ephemeral=True)
    folder = audio_folder(interaction.guild_id, kind.value)
    safe_name = Path(filename).name
    source = folder / safe_name
    if not source.is_file() or source.suffix.lower() not in ALLOWED_AUDIO_EXTENSIONS:
        return await interaction.response.send_message("Audio file not found. Check `/audio-list` for the exact filename.", ephemeral=True)
    if shutil.which("ffmpeg") is None:
        return await interaction.response.send_message("The audio player is not ready: FFmpeg is missing from the host.", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    voice = interaction.guild.voice_client
    try:
        if voice and voice.is_connected():
            if voice.channel != voice_state.channel:
                await voice.move_to(voice_state.channel)
        else:
            voice = await voice_state.channel.connect()
        if voice.is_playing() or voice.is_paused():
            voice.stop()
        audio_source = discord.FFmpegPCMAudio(str(source))
        voice.play(audio_source, after=lambda error: log.error("Audio playback error: %s", error) if error else None)
    except (discord.ClientException, discord.Forbidden, discord.HTTPException, OSError):
        log.exception("Could not start voice playback")
        return await interaction.followup.send("Could not join or play audio. Check Connect/Speak permissions and FFmpeg.", ephemeral=True)
    await interaction.followup.send(f"▶️ Playing **{safe_name}** in {voice_state.channel.mention}.", ephemeral=True)


@bot.tree.command(name="audio-stop", description="Stop the current audio playback and leave the voice channel.")
@app_commands.guild_only()
async def audio_stop(interaction: discord.Interaction):
    voice = interaction.guild.voice_client
    if not voice or not voice.is_connected():
        return await interaction.response.send_message("The bot is not in a voice channel.", ephemeral=True)
    if voice.is_playing() or voice.is_paused():
        voice.stop()
    await voice.disconnect()
    await interaction.response.send_message("⏹️ Audio stopped; the bot left the voice channel.", ephemeral=True)


# -------------------- Events --------------------
@bot.event
async def on_ready():
    log.info("Logged in as %s (ID: %s)", bot.user, bot.user.id if bot.user else "unknown")
    log.info("Connected to %s server(s)", len(bot.guilds))
    await bot.sync_guild_commands()


# -------------------- Slash commands --------------------
@bot.tree.command(name="ping", description="Check the bot response time.")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"🏓 Pong! `{round(bot.latency * 1000)} ms`",
        ephemeral=True,
    )


@bot.tree.command(name="help", description="Show Friends Village bot commands.")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Friends Village — Help",
        description="Community tools and server management.",
        color=GOLD,
    )
    embed.add_field(name="ئەندام", value="`/ping` · `/serverinfo` · `/userinfo` · `/profile` · `/profile-bio` · `/rank` · `/leaderboard`", inline=False)
    embed.add_field(name="رولێن خۆ هەلبژێرە", value="`/self-role-list` · `/self-role`", inline=False)
    embed.add_field(name="Audio", value="`/audio-list` · `/audio-play` · `/audio-stop` · `/audio-upload` (manager)", inline=False)
    embed.add_field(name="Support", value="Use the **Create a ticket** button in #support-tickets.", inline=False)
    embed.add_field(
        name="Administrators",
        value="`/setup` · `/set-welcome` · `/set-log` · `/set-autorole` · `/automod` · `/leveling` · `/village-status` · `/backup-database` · `/purge` · `/timeout` · `/kick` · `/ban`",
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="profile-bio", description="بایۆی پرۆفایلا خۆ بنڤیسە و نوو بکە.")
@app_commands.guild_only()
@app_commands.describe(bio="بایۆیەکا کورت، تا ١٥٠ پیت")
async def profile_bio(interaction: discord.Interaction, bio: app_commands.Range[str, 1, 150]):
    clean_bio = re.sub(r"[\x00-\x1f\x7f]", " ", bio).strip()
    if not clean_bio:
        return await interaction.response.send_message("بایۆیا تە نابیت بەتاڵ بیت.", ephemeral=True)
    with db_connect() as conn:
        conn.execute(
            """INSERT INTO member_profiles (guild_id, user_id, bio) VALUES (?, ?, ?)
               ON CONFLICT(guild_id, user_id) DO UPDATE SET bio=excluded.bio,
               updated_at=CURRENT_TIMESTAMP""",
            (interaction.guild_id, interaction.user.id, clean_bio),
        )
        conn.commit()
    await interaction.response.send_message("✅ بایۆیا تە ب سەرکەفتی هاتە نووکرن.", ephemeral=True)


@bot.tree.command(name="profile", description="پرۆفایلا خۆ یان ئەندامەکێ دی ببینە.")
@app_commands.guild_only()
@app_commands.describe(member="ئەو ئەندامە یێ دڤێت پرۆفایلا وی ببینی")
async def profile(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    member = member or interaction.user
    with db_connect() as conn:
        bio_row = conn.execute(
            "SELECT bio FROM member_profiles WHERE guild_id=? AND user_id=?",
            (interaction.guild_id, member.id),
        ).fetchone()
        xp_row = conn.execute(
            "SELECT xp, level FROM member_xp WHERE guild_id=? AND user_id=?",
            (interaction.guild_id, member.id),
        ).fetchone()
    xp = int(xp_row["xp"]) if xp_row else 0
    level = int(xp_row["level"]) if xp_row else 0
    bio_text = bio_row["bio"] if bio_row and bio_row["bio"] else "ئەڤ ئەندامە هێشتا بایۆیەک نەنووسییە."
    embed = discord.Embed(title=f"👤 پرۆفایلا {member.display_name}", color=GOLD)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ئاست", value=str(level), inline=True)
    embed.add_field(name="کۆی XP", value=f"{xp:,}", inline=True)
    embed.add_field(name="دەربارەی", value=bio_text[:1000], inline=False)
    embed.set_footer(text="Friends Village • پرۆفایلا ئەندامان")
    await interaction.response.send_message(embed=embed)


def role_has_sensitive_permissions(role: discord.Role) -> bool:
    perms = role.permissions
    return any((
        perms.administrator, perms.manage_guild, perms.manage_roles,
        perms.manage_channels, perms.ban_members, perms.kick_members,
        perms.moderate_members, perms.manage_webhooks, perms.manage_messages,
        perms.manage_threads, perms.view_audit_log, perms.manage_nicknames,
        perms.manage_events, perms.manage_expressions, perms.mention_everyone,
    ))


@bot.tree.command(name="self-role-allow", description="ڕۆلەکێ بۆ هەلبژارتنا ئەندامان ڕێپێ بدە.")
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(role="ئەو رولە یێ ئەندام دکارن خۆ هەلبژێرن")
async def self_role_allow(interaction: discord.Interaction, role: discord.Role):
    me = interaction.guild.me
    if role.is_default() or role.managed or me is None or role >= me.top_role:
        return await interaction.response.send_message("ئەڤ رولە ناهێتە بەکارئینان؛ رولەکا ئاسایی و ژ رولێ بۆتێ نزمتر هەلبژێرە.", ephemeral=True)
    if not me.guild_permissions.manage_roles:
        return await interaction.response.send_message("بۆت پێویستی ب مۆڵەتا Manage Roles هەیە.", ephemeral=True)
    if role_has_sensitive_permissions(role):
        return await interaction.response.send_message("ئەڤ رولە دەسەڵاتێن هەستیار هەنە و نابیت ئەندام خۆ هەلبژێرن.", ephemeral=True)
    with db_connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO self_assignable_roles (guild_id, role_id, added_by) VALUES (?, ?, ?)",
            (interaction.guild_id, role.id, interaction.user.id),
        )
        conn.commit()
    await interaction.response.send_message(f"✅ رولێ {role.mention} بۆ هەلبژارتنا ئەندامان هاتە ڕێپێدان.", ephemeral=True)


@bot.tree.command(name="self-role-remove", description="ڕۆلەکێ ژ لیستا هەلبژارتنا ئەندامان لاببە.")
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(role="ئەو رولە یێ دڤێت ژ لیستێ لاببەی")
async def self_role_remove(interaction: discord.Interaction, role: discord.Role):
    with db_connect() as conn:
        cur = conn.execute(
            "DELETE FROM self_assignable_roles WHERE guild_id=? AND role_id=?",
            (interaction.guild_id, role.id),
        )
        conn.commit()
    if cur.rowcount:
        await interaction.response.send_message(f"✅ رولێ {role.mention} ژ لیستێ هاتە لابردن. ئەو رول ل ئەندامان ناهێتە ژێبرن.", ephemeral=True)
    else:
        await interaction.response.send_message("ئەڤ رولە ل لیستا هەلبژارتنێدا نەبوو.", ephemeral=True)


@bot.tree.command(name="self-role-list", description="لیستا رولێن بەردەست بۆ هەلبژارتنێ ببینە.")
@app_commands.guild_only()
async def self_role_list(interaction: discord.Interaction):
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT role_id FROM self_assignable_roles WHERE guild_id=? ORDER BY role_id",
            (interaction.guild_id,),
        ).fetchall()
    lines = []
    for row in rows:
        role = interaction.guild.get_role(int(row["role_id"]))
        if role is not None:
            lines.append(f"• {role.mention} — بۆ وەرگرتن `/self-role` بەکار بینە.")
    embed = discord.Embed(
        title="🎭 رولێن خۆ هەلبژێرە",
        description="\n".join(lines) if lines else "هێشتا رولەک بۆ هەلبژارتنێ نەهاتییە ڕێپێدان.",
        color=GOLD,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="self-role", description="رۆلەکا ڕێپێدراو وەرگرە یان ژ خۆ لاببە.")
@app_commands.guild_only()
@app_commands.describe(role="رولێ دڤێت وەرگری یان لاببەی")
async def self_role(interaction: discord.Interaction, role: discord.Role):
    guild = interaction.guild
    member = interaction.user
    me = guild.me
    if not isinstance(member, discord.Member) or me is None:
        return await interaction.response.send_message("نەشیا ئەندام و بۆت بناسین.", ephemeral=True)
    with db_connect() as conn:
        allowed = conn.execute(
            "SELECT 1 FROM self_assignable_roles WHERE guild_id=? AND role_id=?",
            (guild.id, role.id),
        ).fetchone()
    if not allowed:
        return await interaction.response.send_message("ئەڤ رولە بۆ هەلبژارتنا ئەندامان ڕێپێنەدراوە.", ephemeral=True)
    if role.is_default() or role.managed or role >= me.top_role or role_has_sensitive_permissions(role):
        return await interaction.response.send_message("ئەڤ رولە ناتوانرێت ب شێوەیێ خۆکار بهێتە گۆڕین.", ephemeral=True)
    if not me.guild_permissions.manage_roles:
        return await interaction.response.send_message("بۆت پێویستی ب مۆڵەتا Manage Roles هەیە.", ephemeral=True)
    if role in member.roles:
        await member.remove_roles(role, reason=f"Self-service role removed by {member}")
        await interaction.response.send_message(f"✅ رولێ {role.name} ژ تە هاتە لابردن.", ephemeral=True)
    else:
        await member.add_roles(role, reason=f"Self-service role added by {member}")
        await interaction.response.send_message(f"✅ رولێ {role.name} ب سەرکەفتی هاتە دانان.", ephemeral=True)


@bot.tree.command(name="serverinfo", description="Show information about this server.")
async def serverinfo(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("Use this command inside a server.", ephemeral=True)
    embed = discord.Embed(title=guild.name, color=GOLD)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name="Members", value=str(guild.member_count or "Unknown"))
    embed.add_field(name="Text channels", value=str(len(guild.text_channels)))
    embed.add_field(name="Roles", value=str(len(guild.roles)))
    embed.add_field(name="Created", value=discord.utils.format_dt(guild.created_at, style="D"), inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="userinfo", description="Show information about a server member.")
@app_commands.describe(member="The member to inspect (defaults to you).")
async def userinfo(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    member = member or interaction.user
    embed = discord.Embed(title=f"User: {member}", color=GOLD)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Account created", value=discord.utils.format_dt(member.created_at, style="D"))
    if isinstance(member, discord.Member):
        embed.add_field(name="Joined server", value=discord.utils.format_dt(member.joined_at, style="D") if member.joined_at else "Unknown")
        embed.add_field(name="Top role", value=member.top_role.mention)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="setup", description="Create the luxury Friends Village server layout safely.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.guild_only()
async def setup(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("This command only works in a server.", ephemeral=True)

    bot_member = guild.me or (guild.get_member(bot.user.id) if bot.user else None)
    if bot_member is None:
        return await interaction.response.send_message(
            "نەشێم دەسەڵاتێن بۆتێ پشتڕاست بکەمەوە. تکایە دووبارە هەوڵ بدە.",
            ephemeral=True,
        )
    if not bot_member.guild_permissions.manage_channels:
        return await interaction.response.send_message(
            "I need the **Manage Channels** permission to create the server layout.",
            ephemeral=True,
        )
    if not bot_member.guild_permissions.manage_roles:
        return await interaction.response.send_message(
            "I need the **Manage Roles** permission to create the Village VIP and Village Staff roles.",
            ephemeral=True,
        )

    await interaction.response.defer(ephemeral=True, thinking=True)

    # Create dedicated access roles. Assign members to these roles manually.
    vip_role = await ensure_role(guild, "Village VIP", discord.Color.gold())
    staff_role = await ensure_role(guild, "Village Staff", GOLD)
    friends_role = await ensure_role(guild, "Village Friends", discord.Color.from_rgb(90, 160, 220))

    everyone = guild.default_role

    public_overwrites = {
        everyone: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            send_messages_in_threads=True,
            create_public_threads=True,
            read_message_history=True,
            connect=True,
            speak=True,
        )
    }

    # 01 — Welcome & information
    welcome_cat = await ensure_category(guild, "🏡・WELCOME & INFORMATION", public_overwrites)
    welcome = await ensure_text_channel(guild, welcome_cat, "👋・welcome", "Welcome to Friends Village.")
    await ensure_text_channel(guild, welcome_cat, "📜・server-rules", "Read the community rules before chatting.")
    await ensure_text_channel(guild, welcome_cat, "📢・announcements", "Official community announcements.")
    await ensure_text_channel(guild, welcome_cat, "🎭・roles", "Choose your community roles.")

    # 02 — Community
    community_cat = await ensure_category(guild, "💬・COMMUNITY", public_overwrites)
    await ensure_text_channel(guild, community_cat, "💭・general-chat", "The main community conversation.")
    await ensure_text_channel(guild, community_cat, "🤝・introductions", "Meet the Friends Village community.")
    await ensure_text_channel(guild, community_cat, "📸・media-gallery", "Share photos, clips and creations.")
    await ensure_text_channel(guild, community_cat, "💡・suggestions", "Share ideas to improve the server.")
    await ensure_text_channel(guild, community_cat, "🤖・bot-commands", "Use bot commands here.")
    await ensure_text_channel_in_category(guild, community_cat, "🚀・boosts", "Celebrate and thank Friends Village server boosters.")
    await ensure_text_channel_in_category(guild, community_cat, "📱・social-media", "Share official Friends Village social media links and updates.")

    # 03 — Gaming
    gaming_cat = await ensure_category(guild, "🎮・GAMING", public_overwrites)
    await ensure_text_channel(guild, gaming_cat, "🎮・gaming-chat", "Talk about games and gaming news.")
    await ensure_text_channel(guild, gaming_cat, "👥・find-a-team", "Find people to play with.")
    await ensure_text_channel(guild, gaming_cat, "🏆・game-results", "Share your wins, scores and achievements.")
    await ensure_text_channel(guild, gaming_cat, "📰・gaming-news", "Gaming updates, releases and news.")
    await ensure_text_channel_in_category(guild, gaming_cat, "⚽・pes-efootball", "Discuss PES and eFootball, matches and squads.")
    await ensure_text_channel_in_category(guild, gaming_cat, "🪖・pubg", "Find teammates and discuss PUBG.")
    await ensure_text_channel_in_category(guild, gaming_cat, "🍃・among-us", "Discuss Among Us and find a crew.")
    await ensure_text_channel_in_category(guild, gaming_cat, "🧱・roblox", "Share Roblox games, experiences and creations.")
    await ensure_text_channel_in_category(guild, gaming_cat, "👻・horror-games", "Talk about horror games and share recommendations.")

    # 04 — Creative world
    creative_cat = await ensure_category(guild, "🎨・CREATIVE WORLD", public_overwrites)
    await ensure_text_channel(guild, creative_cat, "🎨・art-gallery", "Share your artwork and creative projects.")
    await ensure_text_channel(guild, creative_cat, "📷・photography", "Share photography and visual moments.")
    await ensure_text_channel(guild, creative_cat, "🎬・clips-and-videos", "Share your favorite clips and videos.")
    await ensure_text_channel(guild, creative_cat, "🎵・music-corner", "Talk about music and share recommendations.")
    await ensure_text_channel(guild, creative_cat, "😂・memes-and-fun", "Memes, jokes and good vibes.")

    # 04A — Gallery: new channels are added without moving existing media channels.
    gallery_cat = await ensure_category(guild, "🖼️・GALLERY", public_overwrites)
    await ensure_text_channel_in_category(guild, gallery_cat, "🏡・family-village", "Family Village moments and community memories.")
    await ensure_text_channel_in_category(guild, gallery_cat, "📸・photos", "Share photos with the Friends Village community.")
    await ensure_text_channel_in_category(guild, gallery_cat, "🎬・videos", "Share videos and clips with the community.")
    await ensure_text_channel_in_category(guild, gallery_cat, "💬・payv", "A space for Payv posts and community sharing.")

    # 05 — Events and activities
    events_cat = await ensure_category(guild, "🎉・EVENTS & ACTIVITIES", public_overwrites)
    await ensure_text_channel(guild, events_cat, "📅・events", "Official events and community activities.")
    await ensure_text_channel(guild, events_cat, "🎁・giveaways", "Community giveaways and prizes.")
    await ensure_text_channel(guild, events_cat, "🏅・competitions", "Challenges, tournaments and competitions.")
    await ensure_text_channel(guild, events_cat, "📊・polls", "Community polls and decisions.")

    # 06 — Voice lounge
    voice_cat = await ensure_category(guild, "🎙️・VOICE LOUNGE", public_overwrites)
    await ensure_voice_channel(guild, voice_cat, "🔊・General Lounge")
    await ensure_voice_channel(guild, voice_cat, "☕・Chill Room")
    await ensure_voice_channel(guild, voice_cat, "🎵・Music Room")
    await ensure_voice_channel(guild, voice_cat, "🎮・Gaming Room")
    await ensure_voice_channel(guild, voice_cat, "🌙・Late Night Talks")

    # 07 — Dedicated audio spaces. Audio is played from files uploaded by managers.
    music_cat = await ensure_category(guild, "🎵・MUSIC STUDIO", public_overwrites)
    await ensure_text_channel(guild, music_cat, "🎶・music-requests", "Request tracks and share music. Managers can upload tracks with /audio-upload.")
    await ensure_text_channel(guild, music_cat, "📚・music-library", "Use /audio-list and /audio-play to browse and play uploaded music.")
    await ensure_voice_channel(guild, music_cat, "🎧・Music Listening Room")

    quran_cat = await ensure_category(guild, "📖・QURAN & RECITATION", public_overwrites)
    await ensure_text_channel(guild, quran_cat, "🕌・quran-library", "Quran recitations uploaded by server managers. Use /audio-list kind:Quran.")
    await ensure_text_channel(guild, quran_cat, "🌙・quran-reflections", "A respectful space for Quran reflections and reminders.")
    await ensure_voice_channel(guild, quran_cat, "🤍・Quran Listening Room")

    soundpad_cat = await ensure_category(guild, "🔊・SOUNDPAD & EFFECTS", public_overwrites)
    await ensure_text_channel(guild, soundpad_cat, "🎛️・soundpad-library", "Browse uploaded sound effects with /audio-list kind:Soundpad.")
    await ensure_text_channel(guild, soundpad_cat, "📤・soundpad-uploads", "Managers can add custom sound clips with /audio-upload kind:Soundpad.")
    await ensure_voice_channel(guild, soundpad_cat, "🎙️・Soundpad Voice Room")

    # 08 — Forums
    forum_cat = await ensure_category(guild, "🧵・COMMUNITY FORUMS", public_overwrites)
    await ensure_forum_channel(guild, forum_cat, "💬・community-discussions", "Start a topic and discuss it with the community.", ["Discussion", "Question", "Poll", "Important"])
    await ensure_forum_channel(guild, forum_cat, "❓・questions-and-help", "Ask questions and help other members.", ["Unanswered", "Answered", "Technical", "Guidance"])
    await ensure_forum_channel(guild, forum_cat, "🎨・showcase", "Share projects, art, screenshots and achievements.", ["Artwork", "Photography", "Video", "Achievement"])
    await ensure_forum_channel(guild, forum_cat, "💡・community-ideas", "Suggest new activities and improvements for Friends Village.", ["Suggestion", "Under Review", "Approved", "Declined"])
    await ensure_forum_channel(guild, forum_cat, "🎮・game-discussions", "Game reviews, recommendations and gaming discussions.", ["PC", "Console", "Mobile", "Looking For Group"])

    # 07 — Private VIP area. Only VIP, staff and administrators can view it.
    private_overwrites = {
        everyone: discord.PermissionOverwrite(view_channel=False),
        vip_role: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            connect=True,
            speak=True,
        ),
        staff_role: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            connect=True,
            speak=True,
            manage_messages=True,
        ),
    }
    if bot_member:
        private_overwrites[bot_member] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
            connect=True,
            speak=True,
        )

    private_cat = await ensure_category(guild, "💎・VIP PRIVATE LOUNGE", private_overwrites)
    await ensure_text_channel(guild, private_cat, "💎・vip-chat", "Private chat for Village VIP members.")
    await ensure_text_channel(guild, private_cat, "📸・vip-media", "A private space for VIP media.")
    await ensure_voice_channel(guild, private_cat, "🔐・VIP Voice")

    # 08 — Private area for the owner and invited friends.
    # Assign the Village Friends role only to people who should see this category.
    friends_overwrites = {
        everyone: discord.PermissionOverwrite(view_channel=False),
        friends_role: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            send_messages_in_threads=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
            connect=True,
            speak=True,
        ),
        staff_role: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_messages=True,
            connect=True,
            speak=True,
        ),
    }
    if bot_member:
        friends_overwrites[bot_member] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
            connect=True,
            speak=True,
        )

    friends_cat = await ensure_category(
        guild, "🫂・PRIVATE FRIENDS CLUB", friends_overwrites
    )
    # This is the one intentional existing-category update requested: keep it private
    # and pin it at the top. All other existing categories retain their positions.
    await friends_cat.edit(
        overwrites=friends_overwrites,
        position=0,
        reason="Friends Village v4.2: pin Private Friends Club at the top",
    )
    await ensure_text_channel(guild, friends_cat, "💬・friends-chat", "Private chat for invited friends.")
    await ensure_text_channel(guild, friends_cat, "😂・friends-memes", "Memes and jokes for the friends group.")
    await ensure_text_channel(guild, friends_cat, "📸・friends-media", "Private photos, clips and media.")
    await ensure_text_channel(guild, friends_cat, "🎮・gaming-plans", "Plan games and play sessions together.")
    await ensure_voice_channel(guild, friends_cat, "🔊・friends-lounge")
    await ensure_voice_channel(guild, friends_cat, "🎮・private-gaming")
    await ensure_voice_channel(guild, friends_cat, "🌙・late-night-talks")

    # 08 — Staff area
    staff_overwrites = {
        everyone: discord.PermissionOverwrite(view_channel=False),
        staff_role: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_messages=True,
        ),
    }
    if bot_member:
        staff_overwrites[bot_member] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
        )
    staff_cat = await ensure_category(guild, "🔒・STAFF CONTROL", staff_overwrites)
    staff_logs = await ensure_text_channel(guild, staff_cat, "📋・staff-logs")
    await ensure_text_channel(guild, staff_cat, "💬・staff-chat")
    await ensure_voice_channel(guild, staff_cat, "🔊・Staff Voice")

    # 09 — Support tickets
    ticket_cat = await ensure_category(guild, "🎫・SUPPORT TICKETS", public_overwrites)
    support = await ensure_text_channel(guild, ticket_cat, "🎫・create-a-ticket")
    await send_ticket_panel(support)

    # Add Badini pinned guides to every managed text channel; old messages remain untouched.
    await post_badini_channel_guides(guild)

    set_welcome_channel(guild.id, welcome.id)

    embed = discord.Embed(
        title="✨ Friends Village — ڕێکخستن تەمام بوو",
        description=(
            "ڕێکخستنا سێرڤەری ئامادەیە. هیچ چانەل، کاتیگۆری یان پەیامەکا بەرێ نەهاتە ژێبرن. چانەلێن بەرێ نەهاتنە گوازتنەوە یان ناڤێ وان نەهاتە گۆڕین.\n\n"
            "**بەشێن گشتی:** بەخێرهاتن، کۆمەڵگا، یاری، گالەری، داهێنان، چالاکی و فۆرۆم.\n"
            "**بەشێن تایبەت:** VIP، Private Friends Club (ل سەرەوە) و بەشێ ستافێ.\n"
            "**دەستگەهشتنا هەڤالان:** رولێ `Village Friends` بدە ئەو هەڤالێن تە دڤێت دەستگەهشتنا وان هەبیت.\n"
            "**دەنگ و موزیک:** کتێبخانەیێن موزیک، قورئان و Soundpad ئامادەن. بەرپرس دکارن فایلان ب `/audio-upload` بار بکەن و ئەندام دکارن ب `/audio-play` گوهداریێ بکەن.\n"
            "**یارمەتی:** پانێلا تیکەتێ ئامادەیە.\n\n"
            f"چانەلێ بەخێرهاتن: {welcome.mention}\n"
            f"پانێلا تیکەتێ: {support.mention}\n"
            f"تۆمارێن ستافێ: {staff_logs.mention}\n\n"
            "**هەنگاڤا داهاتی:** رولێن `Village VIP` و `Village Staff` بدە ئەندامێن پەیوەندیدار."
        ),
        color=GOLD,
    )
    embed.set_footer(text="Friends Village • A place to belong")
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="set-welcome", description="Choose the channel used for new-member welcome messages.")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.guild_only()
@app_commands.describe(channel="The channel where welcome messages should be sent.")
async def set_welcome(interaction: discord.Interaction, channel: discord.TextChannel):
    if interaction.guild is None:
        return await interaction.response.send_message("Use this command inside a server.", ephemeral=True)
    set_welcome_channel(interaction.guild.id, channel.id)
    await interaction.response.send_message(f"Welcome messages will be sent in {channel.mention}.", ephemeral=True)


@bot.tree.command(name="purge", description="Delete a limited number of recent messages in this channel.")
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.guild_only()
@app_commands.describe(amount="Number of messages to remove (1–50).")
async def purge(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 50]):
    if not isinstance(interaction.channel, discord.TextChannel):
        return await interaction.response.send_message("Use this in a text channel.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"Removed {len(deleted)} message(s).", ephemeral=True)


@bot.tree.command(name="timeout", description="Temporarily timeout a member.")
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.guild_only()
@app_commands.describe(member="Member to timeout.", minutes="Duration in minutes (1–10080).", reason="Reason for the timeout.")
async def timeout_member(
    interaction: discord.Interaction,
    member: discord.Member,
    minutes: app_commands.Range[int, 1, 10080],
    reason: Optional[str] = None,
):
    if member.id == interaction.user.id or member.guild_permissions.administrator:
        return await interaction.response.send_message("That member cannot be timed out with this command.", ephemeral=True)
    await member.timeout(discord.utils.utcnow() + timedelta(minutes=minutes), reason=reason)
    await interaction.response.send_message(f"Timed out {member.mention} for {minutes} minute(s).", ephemeral=True)


@bot.tree.command(name="kick", description="Kick a member from the server.")
@app_commands.checks.has_permissions(kick_members=True)
@app_commands.guild_only()
@app_commands.describe(member="Member to kick.", reason="Reason for the kick.")
async def kick_member(interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = None):
    if member.id == interaction.user.id or member.guild_permissions.administrator:
        return await interaction.response.send_message("That member cannot be kicked with this command.", ephemeral=True)
    await member.kick(reason=reason)
    await interaction.response.send_message(f"Kicked {member.mention}.", ephemeral=True)


@bot.tree.command(name="ban", description="Ban a member from the server.")
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.guild_only()
@app_commands.describe(member="Member to ban.", reason="Reason for the ban.")
async def ban_member(interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = None):
    if member.id == interaction.user.id or member.guild_permissions.administrator:
        return await interaction.response.send_message("That member cannot be banned with this command.", ephemeral=True)
    await member.ban(reason=reason, delete_message_seconds=0)
    await interaction.response.send_message(f"Banned {member.mention}.", ephemeral=True)



# -------------------- V5 Royal Elite operations --------------------
@bot.tree.command(name="village-status", description="دۆخێ بۆت و بنکەیا داتایێ ببینە.")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def village_status(interaction: discord.Interaction):
    db_ok = False
    db_size = 0
    try:
        with db_connect() as conn:
            conn.execute("SELECT 1").fetchone()
        db_ok = DATABASE_PATH.exists()
        db_size = DATABASE_PATH.stat().st_size if db_ok else 0
    except (sqlite3.Error, OSError):
        log.exception("Database health check failed")
    volume_path = str(DATABASE_PATH.parent.resolve())
    embed = discord.Embed(title="✦ دۆخێ Friends Village", color=GOLD)
    embed.add_field(name="بۆت", value="چالاکە" if bot.is_ready() else "نەچالاکە", inline=True)
    embed.add_field(name="پەیوەندی", value=f"{round(bot.latency * 1000)} ms" if bot.is_ready() else "نەزانراو", inline=True)
    embed.add_field(name="بنکەیا داتایێ", value="باشە" if db_ok else "پشکنین پێویستە", inline=True)
    embed.add_field(name="قەبارەیا DB", value=f"{db_size:,} bytes", inline=True)
    embed.add_field(name="شوێنێ DB", value=f"`{volume_path}`", inline=False)
    embed.add_field(name="ئاگەهداری", value="ئەڤ پشکنینە تەنێ خوێندنە؛ بوونی Volume ـا هەمیشەیی ل Railway ژ داشبۆردێ Railway پشتڕاست بکە.", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="backup-database", description="وەرگرتنا کۆپییەکا پاراستنێ ژ بنکەیا داتایێ.")
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
async def backup_database(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    temp_name = None
    try:
        # SQLite's backup API safely includes committed WAL data.
        fd, temp_name = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        with db_connect() as current, sqlite3.connect(temp_name) as target:
            current.backup(target)
        with open(temp_name, "rb") as handle:
            backup_buffer = io.BytesIO(handle.read())
        backup_buffer.seek(0)
        filename = f"friends-village-backup-{discord.utils.utcnow():%Y%m%d-%H%M%S}.db"
        await interaction.followup.send(
            content="کۆپییا پاراستنێ یا بنکەیا داتایێ ئامادەیە. ئەڤ فایلە ب پارێزە و ب کەسێ دی مەدە.",
            file=discord.File(backup_buffer, filename=filename),
            ephemeral=True,
        )
    except (sqlite3.Error, OSError, discord.HTTPException):
        log.exception("Database backup failed")
        await interaction.followup.send(
            "نەشیا کۆپییا پاراستنێ ئامادە بکەت. لۆگێن Railway بپشکنە.",
            ephemeral=True,
        )
    finally:
        if temp_name:
            try:
                os.remove(temp_name)
            except OSError:
                log.warning("Could not remove temporary database backup file")


# -------------------- Error handling --------------------



# -------------------- Bilingual community and security systems --------------------
@bot.event
async def on_message(message: discord.Message):
    if message.guild is None or message.author.bot:
        return
    guild, member = message.guild, message.author
    config = settings_for(guild.id)
    now = int(discord.utils.utcnow().timestamp())
    key = (guild.id, member.id)

    # Simple anti-flood and link filter. Exempt moderators.
    if config["automod_enabled"] and not member.guild_permissions.manage_messages:
        recent = [t for t in _spam_times.get(key, []) if now - t <= 8]
        recent.append(now)
        _spam_times[key] = recent[-8:]
        if re.search(r"(https?://|www\.|discord\.gg/)", message.content, re.I):
            try:
                await message.delete()
                await message.channel.send(
                    f"{member.mention} Links are restricted here. | لینک ل ڤێرێ قەدەغەیە.",
                    delete_after=5
                )
                await audit(guild, "AutoMod • Link blocked", f"Member: {member.mention}\nChannel: {message.channel.mention}")
            except discord.Forbidden:
                pass
            return
        if len(recent) >= 6:
            try:
                await message.delete()
                await member.timeout(discord.utils.utcnow() + timedelta(seconds=30), reason="AutoMod flood protection")
                await audit(guild, "AutoMod • Flood detected", f"Member: {member.mention}\nAction: 30-second timeout")
            except discord.Forbidden:
                pass
            _spam_times[key] = []

    # One XP award per user per 60 seconds.
    if config["leveling_enabled"] and now - _xp_cooldowns.get(key, 0) >= 60:
        import random
        gained = random.randint(12, 22)
        with db_connect() as conn:
            conn.execute("INSERT OR IGNORE INTO member_xp (guild_id,user_id) VALUES (?,?)", key)
            row = conn.execute("SELECT xp,level FROM member_xp WHERE guild_id=? AND user_id=?", key).fetchone()
            total, old_level = int(row["xp"]) + gained, int(row["level"])
            new_level = old_level
            while total >= xp_threshold(new_level + 1):
                new_level += 1
            conn.execute("UPDATE member_xp SET xp=?,level=? WHERE guild_id=? AND user_id=?",
                         (total, new_level, guild.id, member.id))
            conn.commit()
        _xp_cooldowns[key] = now
        if new_level > old_level:
            await message.channel.send(
                f"✨ {member.mention} reached **Level {new_level}**! | پیرۆزە، گەهشتی ئاستی **{new_level}**."
            )
    await bot.process_commands(message)


@bot.tree.command(name="warn", description="Issue a formal warning to a member.")
@app_commands.guild_only()
@app_commands.checks.has_permissions(moderate_members=True)
async def warn_member(interaction: discord.Interaction, member: discord.Member, reason: str):
    if member.id == interaction.user.id or member.bot:
        return await interaction.response.send_message("You cannot warn this member.", ephemeral=True)
    with db_connect() as conn:
        conn.execute("INSERT INTO warnings (guild_id,user_id,moderator_id,reason) VALUES (?,?,?,?)",
                     (interaction.guild_id, member.id, interaction.user.id, reason[:500]))
        count = conn.execute("SELECT COUNT(*) FROM warnings WHERE guild_id=? AND user_id=?",
                             (interaction.guild_id, member.id)).fetchone()[0]
        conn.commit()
    await audit(interaction.guild, "Member warned", f"Member: {member.mention}\nModerator: {interaction.user.mention}\nReason: {reason}")
    await interaction.response.send_message(f"⚠️ {member.mention} warned. Total warnings: **{count}**.", ephemeral=True)


@bot.tree.command(name="warnings", description="View a member's warning history.")
@app_commands.guild_only()
@app_commands.checks.has_permissions(moderate_members=True)
async def show_warnings(interaction: discord.Interaction, member: discord.Member):
    with db_connect() as conn:
        rows = conn.execute("SELECT moderator_id,reason,created_at FROM warnings WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 10",
                            (interaction.guild_id, member.id)).fetchall()
    embed = discord.Embed(title=f"Warnings • {member}", color=GOLD)
    embed.description = "\n".join(f"• <@{r['moderator_id']}> — {r['created_at']}\n{r['reason']}" for r in rows) or "No warnings. | هیچ ئاگەهدارییەک نییە."
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="clear-warnings", description="Clear a member's warning history.")
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
async def clear_warnings(interaction: discord.Interaction, member: discord.Member):
    with db_connect() as conn:
        cur = conn.execute("DELETE FROM warnings WHERE guild_id=? AND user_id=?", (interaction.guild_id, member.id))
        conn.commit()
    await interaction.response.send_message(f"Removed **{cur.rowcount}** warning(s) for {member.mention}.", ephemeral=True)


@bot.tree.command(name="rank", description="Show a member's XP and level.")
@app_commands.guild_only()
async def rank(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    member = member or interaction.user
    with db_connect() as conn:
        row = conn.execute("SELECT xp,level FROM member_xp WHERE guild_id=? AND user_id=?",
                           (interaction.guild_id, member.id)).fetchone()
    xp, level = (int(row["xp"]), int(row["level"])) if row else (0, 0)
    start, end = xp_threshold(level), xp_threshold(level + 1)
    progress, needed = max(0, xp - start), max(1, end - start)
    blocks = min(10, int(progress / needed * 10))
    embed = discord.Embed(title=f"✦ Village Rank • {member.display_name}", color=GOLD)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Level / ئاست", value=str(level))
    embed.add_field(name="Total XP", value=f"{xp:,}")
    embed.add_field(name="Progress", value=f"`{'▰'*blocks}{'▱'*(10-blocks)}` {progress:,}/{needed:,}", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="leaderboard", description="Show the top 10 members by XP.")
@app_commands.guild_only()
async def leaderboard(interaction: discord.Interaction):
    with db_connect() as conn:
        rows = conn.execute("SELECT user_id,xp,level FROM member_xp WHERE guild_id=? ORDER BY xp DESC LIMIT 10",
                            (interaction.guild_id,)).fetchall()
    embed = discord.Embed(title="🏆 Friends Village • Leaderboard", color=GOLD)
    embed.description = "\n".join(f"**{i}.** <@{r['user_id']}> — Level **{r['level']}** · {r['xp']:,} XP"
                                    for i, r in enumerate(rows, 1)) or "No XP yet — start chatting!"
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="set-log", description="Set the moderation and audit log channel.")
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
async def set_log(interaction: discord.Interaction, channel: discord.TextChannel):
    set_setting(interaction.guild_id, "log_channel_id", channel.id)
    await interaction.response.send_message(f"Audit logs will be sent to {channel.mention}.", ephemeral=True)


@bot.tree.command(name="set-autorole", description="Set the role automatically assigned to new members.")
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
async def set_autorole(interaction: discord.Interaction, role: discord.Role):
    if role.is_default() or role.managed or role >= interaction.guild.me.top_role:
        return await interaction.response.send_message("Choose a normal role below the bot's highest role.", ephemeral=True)
    set_setting(interaction.guild_id, "autorole_id", role.id)
    await interaction.response.send_message(f"New members will receive {role.mention}.", ephemeral=True)


@bot.tree.command(name="automod", description="Enable or disable AutoMod.")
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
async def automod(interaction: discord.Interaction, enabled: bool):
    set_setting(interaction.guild_id, "automod_enabled", int(enabled))
    await interaction.response.send_message(f"AutoMod {'enabled' if enabled else 'disabled'}.", ephemeral=True)


@bot.tree.command(name="leveling", description="Enable or disable XP leveling.")
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
async def leveling(interaction: discord.Interaction, enabled: bool):
    set_setting(interaction.guild_id, "leveling_enabled", int(enabled))
    await interaction.response.send_message(f"Leveling {'enabled' if enabled else 'disabled'}.", ephemeral=True)


@bot.tree.command(name="lock", description="Lock the current text channel.")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_channels=True)
async def lock_channel(interaction: discord.Interaction):
    if not isinstance(interaction.channel, discord.TextChannel):
        return await interaction.response.send_message("Text channels only.", ephemeral=True)
    await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=False,
                                               reason=f"Locked by {interaction.user}")
    await interaction.response.send_message("🔒 Channel locked. | کەناڵ داخرا.")


@bot.tree.command(name="unlock", description="Unlock the current text channel.")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_channels=True)
async def unlock_channel(interaction: discord.Interaction):
    if not isinstance(interaction.channel, discord.TextChannel):
        return await interaction.response.send_message("Text channels only.", ephemeral=True)
    await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=None,
                                               reason=f"Unlocked by {interaction.user}")
    await interaction.response.send_message("🔓 Channel unlocked. | کەناڵ ڤەبوو.")


@bot.event
async def on_member_join(member: discord.Member):
    config = settings_for(member.guild.id)
    role = member.guild.get_role(config.get("autorole_id") or 0)
    if role and member.guild.me and role < member.guild.me.top_role:
        try:
            await member.add_roles(role, reason="Friends Village auto-role")
        except discord.HTTPException:
            log.exception("Auto-role assignment failed")
    channel_id = get_welcome_channel(member.guild.id)
    channel = member.guild.get_channel(channel_id) if channel_id else None
    if isinstance(channel, discord.TextChannel):
        embed = discord.Embed(
            title="✦ ب خێر هاتی بۆ Friends Village ✦",
            description=(f"ب خێر هاتی {member.mention}! ئەڤ شووە یا تەیە؛ هیڤیدارین کاتەکە تە خۆش بیت.\n"
                         "یاسایێن سێرڤەری بخوینە و ل گەل هەمی ئەندامان ب ڕێز ڕەفتار بکە."),
            color=GOLD
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"ئەندامێ ژمارە {member.guild.member_count or '?'} • Friends Village")
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            log.warning("Cannot send welcome message in channel %s", channel_id)


@bot.event
async def on_member_remove(member: discord.Member):
    await audit(member.guild, "Member left", f"{member} (`{member.id}`) left the server.")


@bot.event
async def on_message_delete(message: discord.Message):
    if message.guild and not message.author.bot:
        content = (message.content or "[No text content / attachment]")[:800]
        await audit(message.guild, "Message deleted",
                    f"Author: {message.author.mention}\nChannel: {message.channel.mention}\nContent: {content}")


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if before.guild and not before.author.bot and before.content != after.content:
        await audit(before.guild, "Message edited",
                    f"Author: {before.author.mention}\nChannel: {before.channel.mention}\nBefore: {before.content[:350]}\nAfter: {after.content[:350]}")


@bot.tree.error
async def luxury_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        msg = "You do not have permission. | مۆڵەتا تە نینە."
    elif isinstance(error, app_commands.BotMissingPermissions):
        msg = "The bot is missing a required permission. | مۆڵەتێن بۆت نەینە."
    else:
        log.error("Slash command error: %s", error, exc_info=error)
        msg = "Something went wrong. Check bot permissions and hosting logs."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except discord.HTTPException:
        pass


# -------------------- Startup --------------------
async def main():
    if not TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN is missing. Add it in Railway → Service → Variables."
        )

    init_db()
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
