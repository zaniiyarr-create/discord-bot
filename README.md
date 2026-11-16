# Friends Village — Luxury Black & Gold Edition

Bilingual English + Kurdish Discord community bot built with discord.py 2.6.4 and SQLite.

## Features
- Luxury gold-themed embeds and bilingual welcome messages.
- Safe, repeatable `/setup` for community, gaming, voice, forum, VIP, staff and ticket channels.
- Persistent support tickets with close controls.
- Moderation: `/purge`, `/timeout`, `/kick`, `/ban`, `/warn`, `/warnings`, `/clear-warnings`.
- Audit log channel: `/set-log`, message edit/delete and member departure logs.
- Basic AutoMod: link blocking and burst/flood detection; configure with `/automod`.
- XP leveling: `/rank`, `/leaderboard`, `/leveling`.
- Onboarding: `/set-autorole`, `/set-welcome`.
- Channel controls: `/lock`, `/unlock`.

## Discord Developer Portal
Enable **Server Members Intent** and **Message Content Intent** under Bot > Privileged Gateway Intents. Invite with `bot` and `applications.commands` scopes and permissions for Manage Channels, Manage Roles, Manage Messages, Moderate Members, Kick Members, Ban Members, View Channels, Send Messages, Embed Links and Read Message History.

## Run
```bash
pip install -r requirements.txt
python bot.py
```
Set `DISCORD_TOKEN`. Optional: `DATABASE_PATH=data/friends_village.db`, `LOG_LEVEL=INFO`.

## First setup
1. Run `/setup` as an administrator.
2. Run `/set-log` and choose the private `#staff-logs` channel.
3. Optionally run `/set-autorole`.
4. Use `/automod enabled:true` and `/leveling enabled:true` as desired.
5. Assign trusted people to `Village Staff` and VIPs to `Village VIP`.

## Railway
Start command: `python bot.py`. Set `DISCORD_TOKEN`, `DATABASE_PATH=/data/friends_village.db`, `LOG_LEVEL=INFO`, and attach a persistent Volume mounted at `/data`.

## Important limitations
- AutoMod is a basic link/flood filter, not a replacement for Discord's native AutoMod. It blocks links from non-moderators; test it before enabling.
- Message Content Intent is required for the filter and XP system.
- Role assignment requires the bot's highest role to be above the target role.
- SQLite persists only when the configured database path is on persistent storage.
- Never share the bot token. Regenerate it immediately if exposed.
