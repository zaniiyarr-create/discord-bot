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

        category = discord.utils.get(guild.categories, name="🎫 TICKETS")
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
            title="Welcome to Friends Village! 🎉",
            description=f"Welcome {member.mention}! We are happy to have you here.",
            color=discord.Color.green(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            log.warning("Cannot send welcome message in channel %s", channel_id)


# -------------------- Destructive reset confirmation --------------------
class DeleteCategoriesConfirmView(discord.ui.View):
    """Confirmation for deleting every server channel and category."""
    def __init__(self, requester_id: int):
        super().__init__(timeout=60)
        self.requester_id = requester_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Only the administrator who started this reset can confirm it.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Yes, delete ALL channels", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("This command only works in a server.", ephemeral=True)

        bot_member = guild.me or (guild.get_member(bot.user.id) if bot.user else None)
        if bot_member is None or not bot_member.guild_permissions.manage_channels:
            return await interaction.response.send_message(
                "I need the **Manage Channels** permission to perform this reset.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True, thinking=True)
        self.stop()
        deleted_channels = 0
        deleted_categories = 0
        failures = []
        reason = f"Friends Village full server reset requested by {interaction.user} ({interaction.user.id})"

        # IMPORTANT: Deleting a Discord category only uncategorizes its children.
        # Delete every channel in the guild first, including channels already
        # uncategorized by an earlier reset, then delete all empty categories.
        channels = [ch for ch in list(guild.channels) if not isinstance(ch, discord.CategoryChannel)]
        for channel in channels:
            try:
                await channel.delete(reason=reason)
                deleted_channels += 1
            except (discord.Forbidden, discord.HTTPException) as exc:
                failures.append(f"{channel.name}: {type(exc).__name__}")
                log.exception("Could not delete channel %s (%s)", channel.name, channel.id)

        for category in list(guild.categories):
            try:
                # If any child channel could not be deleted, Discord will reject
                # deleting the non-empty category and the failure is reported.
                await category.delete(reason=reason)
                deleted_categories += 1
            except (discord.Forbidden, discord.HTTPException) as exc:
                failures.append(f"Category {category.name}: {type(exc).__name__}")
                log.exception("Could not delete category %s (%s)", category.name, category.id)

        result = discord.Embed(
            title="Full server channel reset finished",
            description=(
                f"Deleted **{deleted_channels} channel(s)** and **{deleted_categories} categor(ies)**.\n"
                "This includes channels that were not inside a category.\n\n"
                "When you are ready, run `/setup` to create the Friends Village layout again."
            ),
            color=discord.Color.orange() if failures else discord.Color.green(),
        )
        if failures:
            result.add_field(
                name=f"Items not deleted ({len(failures)})",
                value="\n".join(failures[:10])[:1024],
                inline=False,
            )
        await interaction.edit_original_response(content=None, embed=result, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(
            content="Reset cancelled. Nothing was deleted.", embed=None, view=None
        )


# -------------------- Slash commands --------------------
@bot.tree.command(name="delete-categories", description="Delete ALL server channels and categories (irreversible).")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.guild_only()
async def delete_categories(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("This command only works in a server.", ephemeral=True)

    bot_member = guild.me or (guild.get_member(bot.user.id) if bot.user else None)
    if bot_member is None or not bot_member.guild_permissions.manage_channels:
        return await interaction.response.send_message(
            "I need the **Manage Channels** permission before I can delete categories and channels.",
            ephemeral=True,
        )

    channel_count = sum(1 for channel in guild.channels if not isinstance(channel, discord.CategoryChannel))
    category_count = len(guild.categories)
    embed = discord.Embed(
        title="⚠️ Confirm COMPLETE server channel reset",
        description=(
            f"This will permanently delete **ALL {channel_count} channels** and "
            f"**ALL {category_count} categories**.\n\n"
            "This includes channels inside categories AND channels outside categories. "
            "All messages in deleted channels will be lost. This cannot be undone.\n\n"
            "After it finishes, run `/setup` to create the new Friends Village layout."
        ),
        color=discord.Color.red(),
    )
    log.info(
        "Full channel reset requested by %s (%s) in guild %s (%s): %s categories, %s total channels",
        interaction.user, interaction.user.id, guild.name, guild.id,
        category_count, channel_count,
    )
    await interaction.response.send_message(
        embed=embed,
        view=DeleteCategoriesConfirmView(interaction.user.id),
        ephemeral=True,
    )


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
    staff_logs = await ensure_text_channel(guild, staff_category, "📋・staff-logs")

    set_welcome_channel(guild.id, welcome.id)
    await send_ticket_panel(support)

    embed = discord.Embed(
        title="Friends Village setup complete",
        description=(
            "Created any missing channels and categories. Existing channels and messages were left untouched.\n\n"
            f"Welcome channel: {welcome.mention}\n"
            f"Ticket panel: {support.mention}\n"
            f"Staff logs: {staff_logs.mention}"
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
