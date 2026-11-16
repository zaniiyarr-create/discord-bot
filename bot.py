import os
import logging
import re
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

# -------------------- Configuration --------------------
TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "data/friends_village.db"))
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("friends-village")

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
    existing = discord.utils.get(guild.categories, name=name)
    if existing:
        if overwrites is not None:
            await existing.edit(overwrites=overwrites, reason="Friends Village setup: enforce category permissions")
        return existing
    return await guild.create_category(name, overwrites=overwrites, reason="Friends Village setup")


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
        if existing.category_id != category.id:
            await existing.edit(category=category, reason="Friends Village setup: keep layout in sync")
        if topic and existing.topic != topic:
            await existing.edit(topic=topic, reason="Friends Village setup: update channel topic")
        return existing
    return await guild.create_text_channel(
        name,
        category=category,
        topic=topic,
        reason="Friends Village setup",
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
        if existing.category_id != category.id:
            await existing.edit(category=category, reason="Friends Village setup: keep layout in sync")
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
) -> discord.ForumChannel:
    # CategoryChannel has .channels, not .forum_channels, in discord.py.
    # Search guild.channels so forum channels are found regardless of category.
    existing = discord.utils.find(
        lambda ch: isinstance(ch, discord.ForumChannel) and ch.name == name,
        guild.channels,
    )
    if existing:
        if existing.category_id != category.id:
            await existing.edit(category=category, reason="Friends Village setup: keep layout in sync")
        if topic and existing.topic != topic:
            await existing.edit(topic=topic, reason="Friends Village setup: update forum topic")
        return existing
    return await guild.create_forum(
        name,
        category=category,
        topic=topic,
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
        title="Friends Village Support",
        description="Need help? Press the button below to create a private support ticket.",
        color=GOLD,
    )
    await channel.send(embed=embed, view=TicketPanelView())


# -------------------- Ticket UI --------------------
class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Create a ticket",
        style=discord.ButtonStyle.primary,
        emoji="🎫",
        custom_id="friends_village:create_ticket",
    )
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("This button only works in a server.", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)

        existing = discord.utils.find(
            lambda c: isinstance(c, discord.TextChannel)
            and c.topic == f"ticket-owner:{interaction.user.id}",
            guild.text_channels,
        )
        if existing:
            return await interaction.followup.send(
                f"You already have an open ticket: {existing.mention}", ephemeral=True
            )

        category = discord.utils.get(guild.categories, name="🎫・SUPPORT TICKETS")
        if category is None:
            category = await guild.create_category("🎫 TICKETS", reason="Ticket system")

        bot_member = guild.me
        if bot_member is None and bot.user is not None:
            bot_member = guild.get_member(bot.user.id)
        if bot_member is None:
            return await interaction.followup.send("I could not verify my server permissions. Please try again shortly.", ephemeral=True)

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
            description=f"Hello {interaction.user.mention}! Describe what you need help with. A moderator will assist you.",
            color=GOLD,
        )
        await channel.send(
            content=interaction.user.mention,
            embed=embed,
            view=CloseTicketView(),
        )
        await interaction.followup.send(f"Your ticket is ready: {channel.mention}", ephemeral=True)


class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close ticket",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="friends_village:close_ticket",
    )
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel) or not channel.topic:
            return await interaction.response.send_message("This is not a ticket channel.", ephemeral=True)

        if not channel.topic.startswith("ticket-owner:"):
            return await interaction.response.send_message("This is not a ticket channel.", ephemeral=True)

        owner_id = int(channel.topic.split(":", 1)[1])
        is_owner = interaction.user.id == owner_id
        is_staff = interaction.user.guild_permissions.manage_channels or interaction.user.guild_permissions.administrator
        if not (is_owner or is_staff):
            return await interaction.response.send_message(
                "Only the ticket creator or a server moderator can close this ticket.",
                ephemeral=True,
            )

        await interaction.response.send_message("Ticket will be closed in a moment.", ephemeral=True)
        await channel.delete(reason=f"Ticket closed by {interaction.user}")


# -------------------- Events --------------------
@bot.event
async def on_ready():
    log.info("Logged in as %s (ID: %s)", bot.user, bot.user.id if bot.user else "unknown")
    log.info("Connected to %s server(s)", len(bot.guilds))
    await bot.sync_guild_commands()


@bot.event
async def on_member_join(member: discord.Member):
    channel_id = get_welcome_channel(member.guild.id)
    if not channel_id:
        return
    channel = member.guild.get_channel(channel_id)
    if isinstance(channel, discord.TextChannel):
        embed = discord.Embed(
            title="✦ Welcome to Friends Village | بەخێربێیت ✦",
            description=(f"Welcome {member.mention}! Make yourself at home.\\n"
                         f"بەخێربێیت {member.mention}! هیڤیدارین کاتەکە تە خۆش بیت."),
            color=GOLD,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            log.warning("Cannot send welcome message in channel %s", channel_id)


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
    embed.add_field(name="Everyone", value="`/ping` · `/serverinfo` · `/userinfo`", inline=False)
    embed.add_field(name="Support", value="Use the **Create a ticket** button in #support-tickets.", inline=False)
    embed.add_field(
        name="Administrators",
        value="`/setup` · `/set-welcome` · `/purge` · `/timeout` · `/kick` · `/ban`",
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


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
            "I could not verify my server permissions. Please try again shortly.",
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

    everyone = guild.default_role

    public_overwrites = {
        everyone: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
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

    # 03 — Gaming
    gaming_cat = await ensure_category(guild, "🎮・GAMING", public_overwrites)
    await ensure_text_channel(guild, gaming_cat, "🎮・gaming-chat", "Talk about games and gaming news.")
    await ensure_text_channel(guild, gaming_cat, "👥・find-a-team", "Find people to play with.")

    # 04 — Voice lounge
    voice_cat = await ensure_category(guild, "🎙️・VOICE LOUNGE", public_overwrites)
    await ensure_voice_channel(guild, voice_cat, "🔊・General Lounge")
    await ensure_voice_channel(guild, voice_cat, "☕・Chill Room")
    await ensure_voice_channel(guild, voice_cat, "🎵・Music Room")
    await ensure_voice_channel(guild, voice_cat, "🎮・Gaming Room")
    await ensure_voice_channel(guild, voice_cat, "🌙・Late Night Talks")

    # 05 — Forums
    forum_cat = await ensure_category(guild, "🧵・COMMUNITY FORUMS", public_overwrites)
    await ensure_forum_channel(guild, forum_cat, "💬・community-discussions", "Start a topic and discuss it with the community.")
    await ensure_forum_channel(guild, forum_cat, "❓・questions-and-help", "Ask questions and help other members.")
    await ensure_forum_channel(guild, forum_cat, "🎨・showcase", "Share projects, art, screenshots and achievements.")

    # 06 — Private VIP area. Only VIP, staff and administrators can view it.
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

    # 07 — Staff area
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

    # 08 — Support tickets
    ticket_cat = await ensure_category(guild, "🎫・SUPPORT TICKETS", public_overwrites)
    support = await ensure_text_channel(guild, ticket_cat, "🎫・create-a-ticket")
    await send_ticket_panel(support)

    set_welcome_channel(guild.id, welcome.id)

    embed = discord.Embed(
        title="✨ Friends Village — Luxury Setup Complete",
        description=(
            "The server layout is ready. Existing channels and messages were not deleted.\n\n"
            "**Public areas:** Welcome, Community, Gaming, Voice Lounge and Forums.\n"
            "**Private areas:** VIP Private Lounge and Staff Control.\n"
            "**Support:** Ticket panel is ready.\n\n"
            f"Welcome channel: {welcome.mention}\n"
            f"Ticket panel: {support.mention}\n"
            f"Staff logs: {staff_logs.mention}\n\n"
            "**Next step:** Assign members to `Village VIP` and trusted moderators to `Village Staff`."
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


# -------------------- Error handling --------------------
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        message = "You do not have permission to use this command."
    elif isinstance(error, app_commands.CommandOnCooldown):
        message = "Please wait before using this command again."
    else:
        log.exception("Application command error", exc_info=error)
        message = "Something went wrong. Check the bot logs or contact an administrator."

    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass



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
        if re.search(r"(https?://|www\\.|discord\\.gg/)", message.content, re.I):
            try:
                await message.delete()
                await message.channel.send(
                    f"{member.mention} Links are restricted here. | لینک ل ڤێرێ قەدەغەیە.",
                    delete_after=5
                )
                await audit(guild, "AutoMod • Link blocked", f"Member: {member.mention}\\nChannel: {message.channel.mention}")
            except discord.Forbidden:
                pass
            return
        if len(recent) >= 6:
            try:
                await message.delete()
                await member.timeout(discord.utils.utcnow() + timedelta(seconds=30), reason="AutoMod flood protection")
                await audit(guild, "AutoMod • Flood detected", f"Member: {member.mention}\\nAction: 30-second timeout")
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
    await audit(interaction.guild, "Member warned", f"Member: {member.mention}\\nModerator: {interaction.user.mention}\\nReason: {reason}")
    await interaction.response.send_message(f"⚠️ {member.mention} warned. Total warnings: **{count}**.", ephemeral=True)


@bot.tree.command(name="warnings", description="View a member's warning history.")
@app_commands.guild_only()
@app_commands.checks.has_permissions(moderate_members=True)
async def show_warnings(interaction: discord.Interaction, member: discord.Member):
    with db_connect() as conn:
        rows = conn.execute("SELECT moderator_id,reason,created_at FROM warnings WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 10",
                            (interaction.guild_id, member.id)).fetchall()
    embed = discord.Embed(title=f"Warnings • {member}", color=GOLD)
    embed.description = "\\n".join(f"• <@{r['moderator_id']}> — {r['created_at']}\\n{r['reason']}" for r in rows) or "No warnings. | هیچ ئاگەهدارییەک نییە."
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
    embed.description = "\\n".join(f"**{i}.** <@{r['user_id']}> — Level **{r['level']}** · {r['xp']:,} XP"
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
            title="✦ Welcome to Friends Village | بەخێربێیت ✦",
            description=(f"Welcome {member.mention}! Make yourself at home.\\n"
                         f"بەخێربێیت {member.mention}! هیڤیدارین کاتەکە تە خۆش بیت."),
            color=GOLD
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Member #{member.guild.member_count or '?'} • Friends Village")
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
                    f"Author: {message.author.mention}\\nChannel: {message.channel.mention}\\nContent: {content}")


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if before.guild and not before.author.bot and before.content != after.content:
        await audit(before.guild, "Message edited",
                    f"Author: {before.author.mention}\\nChannel: {before.channel.mention}\\nBefore: {before.content[:350]}\\nAfter: {after.content[:350]}")


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
