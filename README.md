# Friends Village — Luxury Edition

A Discord community bot built with `discord.py 2.6.4` and SQLite.

## Features

- English-first luxury server layout.
- Public categories for Welcome, Community, Gaming, Voice Lounge and Community Forums.
- Private VIP Lounge and Staff Control categories.
- Text channels, voice channels and forum channels.
- Persistent welcome-channel setting.
- Private support tickets with a close button.
- Moderation slash commands: `/purge`, `/timeout`, `/kick`, `/ban`.
- Utility commands: `/ping`, `/help`, `/serverinfo`, `/userinfo`.
- Safe, repeatable `/setup`: creates missing resources and does not delete existing channels or messages.

## Setup

1. Create a Discord application and bot in the Discord Developer Portal.
2. Enable **Server Members Intent** for welcome events.
3. Invite the bot with **Manage Channels** and **Manage Roles**, plus send messages, embed links, read message history, moderate members, kick and ban members as required.
4. Add `DISCORD_TOKEN` to your environment variables.
5. Run `python bot.py`.
6. In your Discord server, run `/setup` as an administrator.

## Public and private access

`/setup` creates two roles with no elevated permissions:

- `Village VIP` — assign this role to members who should access the VIP Private Lounge.
- `Village Staff` — assign this role to trusted moderators who should access Staff Control.

Administrators retain access through Discord's administrator permission. The bot must be above `Village VIP` and `Village Staff` in the server role hierarchy if it needs to manage those roles later; this version does not automatically assign either role.

## Hosting

For Railway, use:

- `DISCORD_TOKEN` — your bot token
- `DATABASE_PATH=/data/friends_village.db`
- `LOG_LEVEL=INFO`

Attach a Railway Volume mounted at `/data` to persist the SQLite database.

## Important

- `/setup` is non-destructive and can be run more than once.
- Existing channels and messages are not deleted or renamed.
- Private categories are hidden from `@everyone`; access is granted to their matching role and administrators.
- Forum channel creation requires the bot's **Manage Channels** permission and a server where Discord forum channels are available.
- Do not share your bot token. If it has been exposed, regenerate it in the Discord Developer Portal.
