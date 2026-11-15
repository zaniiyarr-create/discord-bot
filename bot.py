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
bot = commands.Bot(command_prefix="!", intents=intents)


# -------------------- SQLite configuration --------------------
def db_connect():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_connect() as conn:
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
        return existing
    return await guild.create_category(name, overwrites=overwrites, reason="Friends Village setup")


async def ensure_text_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    name: str,
    topic: Optional[str] = None,
) -> discord.TextChannel:
    existing = discord.utils.get(category.text_channels, name=name)
    if existing:
        return existing
    return await guild.create_text_channel(
        name,
        category=category,
        topic=topic,
        reason="Friends Village setup",
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

        existing = discord.utils.find(
            lambda c: isinstance(c, discord.TextChannel)
            and c.topic == f"ticket-owner:{interaction.user.id}",
            guild.text_channels,
        )
        if existing:
            return await interaction.response.send_message(
                f"You already have an open ticket: {existing.mention}",
                ephemeral=True,
            )

        category = discord.utils.get(guild.categories, name="🎫 TICKETS")
        if category is None:
            category = await guild.create_category("🎫 TICKETS", reason="Ticket system")

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                attach_files=True, embed_links=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                manage_channels=True,
            ),
        }
        safe_name = f"ticket-{interaction.user.name}".lower().replace(" ", "-")[:80]
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
        await interaction.response.send_message(f"Your ticket is ready: {channel.mention}", ephemeral=True)


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


@bot.event
async def on_member_join(member: discord.Member):
    channel_id = get_welcome_channel(member.guild.id)
    if not channel_id:
        return
    channel = member.guild.get_channel(channel_id)
    if isinstance(channel, discord.TextChannel):
        embed = discord.Embed(
            title="Welcome to Friends Village! 🎉",
            description=f"Welcome {member.mention}! We are happy to have you here.",
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


@bot.tree.command(name="setup", description="Create missing Friends Village channels without deleting anything.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.guild_only()
async def setup(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("This command only works in a server.", ephemeral=True)

    await interaction.response.defer(ephemeral=True, thinking=True)

    public_overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    }
    category = await ensure_category(guild, "🏡 FRIENDS VILLAGE", public_overwrites)

    welcome = await ensure_text_channel(guild, category, "👋・welcome")
    await ensure_text_channel(guild, category, "💬・general-chat")
    await ensure_text_channel(guild, category, "🤖・bot-commands")
    support = await ensure_text_channel(guild, category, "🎫・support-tickets")
    await ensure_text_channel(guild, category, "📢・announcements")

    # Create a private staff log category. Access is not granted to @everyone.
    staff_overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False)
    }
    staff_category = await ensure_category(guild, "🔒 STAFF AREA", staff_overwrites)
    await ensure_text_channel(guild, staff_category, "📋・staff-logs")

    set_welcome_channel(guild.id, welcome.id)
    await send_ticket_panel(support)

    embed = discord.Embed(
        title="Friends Village setup complete",
        description=(
            "Created any missing channels and categories. Existing channels and messages were left untouched.\n\n"
            f"Welcome channel: {welcome.mention}\n"
            f"Ticket panel: {support.mention}\n"
            f"Staff logs: {staff_category.text_channels[0].mention if staff_category.text_channels else 'Created'}"
        ),
        color=discord.Color.green(),
    )
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
    await member.ban(reason=reason, delete_message_days=0)
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
        bot.add_view(TicketPanelView())
        bot.add_view(CloseTicketView())
        await bot.start(TOKEN)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
