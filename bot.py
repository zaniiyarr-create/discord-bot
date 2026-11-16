import os
import logging
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

# Slash commands are used, so Message Content Intent is not required.
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
        color=discord.Color.blurple(),
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
            color=discord.Color.green(),
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
            title="Welcome to Friends Village! ✨",
            description=f"Welcome {member.mention}! Make yourself at home and enjoy the community.",
            color=discord.Color.green(),
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
        color=discord.Color.blurple(),
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
    embed = discord.Embed(title=guild.name, color=discord.Color.blurple())
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
    embed = discord.Embed(title=f"User: {member}", color=discord.Color.blurple())
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
    staff_role = await ensure_role(guild, "Village Staff", discord.Color.from_rgb(126, 87, 194))

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
        color=discord.Color.from_rgb(126, 87, 194),
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
