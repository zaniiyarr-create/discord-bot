# Friends Village Bot V2.0

A Discord.py bot with safe server setup, welcome messages, persistent support-ticket buttons,
basic moderation, slash commands, and SQLite settings. On startup, the bot syncs application commands directly to each connected server so newly added commands appear without waiting for global-command propagation.

## Security first

- If a bot token has ever appeared in GitHub, screenshots, logs, or a ZIP shared publicly, reset it in the Discord Developer Portal immediately.
- Add the replacement token only as the Railway variable `DISCORD_TOKEN`.
- Never put tokens in `bot.py`, `.env.example`, GitHub commits, or chat messages.
- Do not reuse a token that has been exposed.

## Railway deployment

1. Push this project to your GitHub repository.
2. In Railway, open the bot service connected to that repository.
3. Open **Variables** and add:
   - `DISCORD_TOKEN` = your newly reset bot token
   - `DATABASE_PATH` = `/data/friends_village.db`
4. Add a Railway Volume and mount it at `/data` so SQLite settings persist across redeployments.
5. Deploy. The included Nixpacks configuration starts `python bot.py`.
6. On startup, the bot registers persistent ticket buttons, then syncs slash commands to each connected server.
7. Open the deployment logs. Look for `Successfully synced ... application command(s) to guild ...` and `Logged in as ...`.

## Discord Developer Portal

In your application's **Bot** settings:
- Reset the bot token if it was exposed.
- Enable **Server Members Intent** because the bot welcomes new members.
- Message Content Intent is not required; commands use Discord slash commands.

In **OAuth2 → URL Generator**, select scopes:
- `bot`
- `applications.commands`

Grant only the permissions needed:
- View Channels
- Send Messages
- Embed Links
- Read Message History
- Manage Channels (ticket creation/closing and `/delete-categories` reset)
- Manage Messages (purge command)
- Moderate Members (timeout)
- Kick Members
- Ban Members

Invite the bot to your server using the generated URL. Keep the bot role below staff roles and do not grant Administrator unless genuinely required.

## Commands

### Everyone
- `/ping`
- `/help`
- `/serverinfo`
- `/userinfo`

### Administrators / moderators
- `/setup` — creates missing Friends Village channels and categories. It DOES NOT delete existing channels or messages.
- `/delete-categories` — administrator-only and confirmation-protected. Permanently deletes ALL server channels (including uncategorized text/voice channels and their messages) and then deletes all categories. This is irreversible. Run `/setup` afterward to recreate the Friends Village layout.
- `/set-welcome channel:` — changes the welcome channel.
- `/purge amount:` — removes 1–50 recent messages.
- `/timeout member: minutes: reason:` 
- `/kick member: reason:`
- `/ban member: reason:`

### Tickets
`/setup` creates a support panel in `🎫・support-tickets`. Members can create private tickets with the button. The ticket creator or a moderator can close their ticket.

## Important behavior

- `/setup` is non-destructive and can be run more than once.
- Existing channels and messages are not removed.
- SQLite stores the welcome-channel setting using WAL mode and a 30-second busy timeout. Use a Railway Volume mounted at `/data` and set `DATABASE_PATH=/data/friends_village.db` to persist the database. Without a persistent volume, settings may be lost when the container is replaced.
- The bot uses slash commands, so it does not need Message Content Intent.

## Full server layout reset

1. Run `/delete-categories` as a server administrator.
2. Read the warning and press **Yes, delete all categories** only if you accept that all channels and messages inside those categories will be permanently deleted.
3. Channels not assigned to a category are not deleted.
4. After the reset completes, run `/setup` to create the Friends Village categories and channels again.

The reset requires the bot's **Manage Channels** permission. It deletes every channel in the server, including channels that are not inside a category, before deleting categories. Discord may prevent deletion of channels/categories the bot cannot manage due to permissions or server restrictions; any such failures are reported in the result.
