# Friends Village v4.0 — Royal Elite Paradise

A bilingual English/Kurdish Discord community bot using discord.py 2.6.4, SQLite and gold-themed embeds.

## Included server layout

`/setup` safely creates missing categories/channels and moves matching channels into their intended categories. It does not delete existing channels or messages.

- Welcome & Information — public welcome, rules, announcements and roles.
- Community — general chat, introductions, media, suggestions and bot commands.
- Gaming — gaming chat, find-a-team, results and gaming news.
- Creative World — art, photography, clips, music chat and memes.
- Events & Activities — events, giveaways, competitions and polls.
- Voice Lounge — general, chill, music, gaming and late-night voice rooms.
- Music Studio — music requests, library and listening room.
- Quran & Recitation — Quran library, reflections and listening room.
- Soundpad & Effects — soundpad library, uploads and voice room.
- Community Forums — discussions, help, showcase, ideas and game discussions.
- VIP Private Lounge — private to Village VIP, Village Staff and admins.
- Private Friends Club — private to Village Friends, Village Staff and admins.
- Staff Control — private logs, staff chat and voice.
- Support Tickets — public panel creating private ticket channels.

## Welcome messages

New members receive a bilingual gold embed in `👋・welcome`. The channel is public and visible to everyone. The bot needs View Channel, Send Messages, Embed Links and the Server Members Intent.

## Audio playback (music, Quran and Soundpad)

The bot plays audio files uploaded by a server manager. Supported formats: MP3, WAV, OGG, M4A and FLAC. Maximum upload size enforced by the bot: 20 MB.

- `/audio-upload kind:<Music|Quran|Soundpad> attachment:<file>` — managers with Manage Server upload a file.
- `/audio-list kind:<...>` — list files in a library.
- `/audio-play kind:<...> filename:<exact filename>` — join your current voice channel and play the file.
- `/audio-stop` — stop playback and disconnect.

The host must have FFmpeg and PyNaCl installed. Audio files are stored under `data/audio/<guild_id>/<kind>/`. Use persistent storage for this directory or uploaded files will be lost when the host redeploys. The bot needs Connect and Speak permissions in the voice channels. This is a file-based player, not a YouTube/Spotify streaming integration.

For a Quran library, upload recitations you have permission to use. The bot does not download or select Quran recitations automatically.

## Existing features

- Persistent ticket panels and close controls.
- Moderation: purge, timeout, kick, ban, warnings and warning history.
- Audit logs, member departure, message edit/delete logs.
- Basic link/flood AutoMod and XP leveling/rank/leaderboard.
- Autorole and configurable welcome/log channels.
- Channel lock/unlock controls.
- Private VIP and invited-friends categories.

## Setup

1. Install dependencies: `pip install -r requirements.txt`.
2. Ensure FFmpeg is installed and available as `ffmpeg` on PATH.
3. Set `DISCORD_TOKEN` in the hosting environment.
4. Enable **Server Members Intent** and **Message Content Intent** in Discord Developer Portal → Bot → Privileged Gateway Intents.
5. Invite with `bot` and `applications.commands` scopes. Grant Manage Channels, Manage Roles, View Channels, Send Messages, Embed Links, Read Message History, Connect, Speak, Attach Files, Manage Messages, Moderate Members, Kick Members and Ban Members as needed.
6. Run `/setup` as an administrator.
7. Assign trusted friends the `Village Friends` role, VIPs `Village VIP`, and moderators `Village Staff`. Keep the bot's highest role above the roles it must manage.
8. Run `/set-log` and select `📋・staff-logs`.
9. Optionally run `/set-autorole`.
10. Managers upload audio using `/audio-upload`. Members use `/audio-list` and `/audio-play`.

## Railway / persistent storage

Set:
- `DISCORD_TOKEN` — bot token
- `DATABASE_PATH=/data/friends_village.db`
- `AUDIO_ROOT=/data/audio`
- `LOG_LEVEL=INFO`

Attach a persistent Volume mounted at `/data`. Start command: `python bot.py`.

## Important notes

- `/setup` is repeatable and does not delete existing channels/messages. Review permissions after setup; the server owner/admins retain administrative access.
- The welcome message is public in the configured welcome channel; it mentions the new member but does not @everyone-ping the whole server.
- Audio playback is uploaded-file based. No internet music search, YouTube, Spotify, queue or autoplay is included.
- Soundpad clips are played by command in the caller's current voice channel; it is not Discord's native Soundboard UI.
- The bot cannot be considered deployed or live-tested until it is started with a valid token in your server.
- Never share your bot token. Regenerate it immediately if exposed.
