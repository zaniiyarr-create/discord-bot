# Friends Village v5.1 — Community Profile / Self Roles / Royal Elite

A bilingual English/Kurdish Discord community bot using discord.py 2.7.1, SQLite and gold-themed embeds.

## Included server layout

`/setup` adds the requested missing categories/channels. Existing categories and channels are not deleted, moved, renamed, or edited. The one intentional exception is `📌・PRIVATE FRIENDS CLUB`: its private access permissions are enforced and the category is moved to the top, as requested.

- Welcome & Information — public welcome, rules, announcements and roles.
- Community — general chat, introductions, media, suggestions, bot commands, boosts and social media.
- Gaming — gaming chat, find-a-team, results, gaming news, PES/eFootball, PUBG, Among Us, Roblox and horror games.
- Gallery — Family Village, photos, videos and Payv.
- Creative World — art, photography, clips, music chat and memes.
- Events & Activities — events, giveaways, competitions and polls.
- Voice Lounge — general, chill, music, gaming and late-night voice rooms.
- Music Studio — music requests, library and listening room.
- Quran & Recitation — Quran library, reflections and listening room.
- Soundpad & Effects — soundpad library, uploads and voice room.
- Community Forums — discussions, help, showcase, ideas and game discussions.
- VIP Private Lounge — private to Village VIP, Village Staff and admins.
- Private Friends Club — private to Village Friends, Village Staff and admins; pinned at the top of the category list.
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

- `/setup` is repeatable and additive: it does not delete, move, rename, or edit existing channels/categories. It intentionally sets Private Friends Club to private and places it first. Review its role access after setup; the server owner/admins retain administrative access.
- The welcome message is public in the configured welcome channel; it mentions the new member but does not @everyone-ping the whole server.
- Audio playback is uploaded-file based. No internet music search, YouTube, Spotify, queue or autoplay is included.
- Soundpad clips are played by command in the caller's current voice channel; it is not Discord's native Soundboard UI.
- The bot cannot be considered deployed or live-tested until it is started with a valid token in your server.
- Never share your bot token. Regenerate it immediately if exposed.


## V4.2.1 — Rêberên kanalan bi kurdîya Badînî

Di vê guhertinê de `/setup` ji bo her kanala nivîsarî ya ku bot rêve dike rêbernameyeke taybet bi zaravaya kurdîya Badînî dişîne û pin dike. Rêbername tenê carekê tê şandin. Peyamên berê nayên jêbirin; nav, cih, topic û destûrên kanalên heyî nayên guhertin. Bot divê destûra `Send Messages`, `Read Message History` û `Manage Messages` hebe da ku rêbernameyan pin bike.


## V5.0 — Royal Elite Foundation (first implementation phase)

Di vê qonaxê de:
- Komanda `/village-status` rewşa bot, ping û SQLite nîşan dide. Ev komend tenê kontrola xwendinê ye; mount-a Railway divê ji dashboard-ê were piştrastkirin.
- Komanda `/backup-database` ji bo adminan backup-a SQLite diafirîne û wek pelê taybet di Discord-ê de dişîne.
- Dûbarebûna event-a `on_member_join` û error-handler-a slash command hatin yekxistin da ku autorole/welcome û rêkxistina xeletiyan bi awayekî yekgirtî bixebitin.
- Lêgerîna kanalên nû li tevahiya serverê tê kirin da ku `/setup` kanalên heman navî li kategoriyeke din dubare çêneke.
- Rêya xwerû ya SQLite û audio ji bo Railway `/data` ye. Volume li Railway bi mount path `/data` girêde û `DATABASE_PATH=/data/friends_village.db`, `AUDIO_ROOT=/data/audio` saz bike.

### Qonaxên paşerojê yên Complete V5
1. Community: role menus, welcome customization, member profile.
2. Gaming: tournament registration, match reporting and leaderboard.
3. Support: ticket transcript/export and staff workflow.
4. Security: configurable AutoMod, audit coverage and scheduled backups.
5. Voice: reliable playback queue, soundpad controls and reconnect handling.
6. Badini localization: translate all user-facing command descriptions and bot responses, then test every command.

**Test status:** Python syntax compilation succeeded for `bot.py`. No Discord login, Railway deployment, command sync, voice playback, or live server test has been performed.


## V5.1 — Community Profile and Self Roles

New Badini commands:
- `/profile` — show a member's community profile, bio, XP and level.
- `/profile-bio` — set or update your own short bio (up to 150 characters).
- `/self-role-list` — show roles that administrators have approved for self-selection.
- `/self-role` — add or remove one approved role from your own account.
- `/self-role-allow` — administrator-only: approve a safe role for self-selection.
- `/self-role-remove` — administrator-only: remove a role from the self-selection list. This does not remove it from members who already have it.

Self-role safety: the bot refuses managed, @everyone, higher-than-bot, or privileged roles. Do not approve roles that grant moderation or server-management powers. The bot needs Manage Roles, and its highest role must be above every approved role.

V5.1 database migration is additive: `member_profiles` and `self_assignable_roles` are created with `CREATE TABLE IF NOT EXISTS`. Existing tables and records are not deleted or rewritten. Existing V5.0 databases are retained when `DATABASE_PATH` points to the same persistent file.

The V5.1 source also fixes a startup-order bug: the logger is initialized before the `/data` volume warning is written.

**Validation:** Python syntax compilation and ZIP integrity checks were performed locally. Discord login, slash-command synchronization, Railway deployment, role assignment permissions, and live server behavior have not been tested.
