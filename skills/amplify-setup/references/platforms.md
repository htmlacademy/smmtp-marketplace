# Platform guides

How to walk the operator through connecting each kind of channel. Read only the section for the channel at hand. Give every link and menu path as written; if the operator does not find an item, ask what they see and guide from there. Values in `CAPS` are what `plan` lists in `asks`: collect each through Writing values in SKILL.md, never by naming the variable to the operator. The last line of each guide is what only the first post proves.

## Telegram channel or group

Publishing goes through a bot the operator creates; it posts in the channel's name.

1. Create the bot: open https://t.me/BotFather in Telegram, press Start, send `/newbot`, choose a display name, then a username ending in `bot`. BotFather answers with a long line like `123456789:AAH…`: the bot's key, `TELEGRAM_BOT_TOKEN`.
2. Make it an administrator of the channel: open the channel → its name at the top → Edit (pencil) → Administrators → Add Admin → search the bot's username → keep "Post Messages" on → Save. In a group, adding the bot as a member allowed to send messages is enough.
3. `connect --to telegram --link <their channel link>`. A private channel's invite link works once step 2 is done.

On `chat_not_found`, a private channel needs its invite link.

First post proves: media rights in a group.

## Telegram stories

Stories go out as the operator's Telegram Business account through the same bot. Telegram Business needs Telegram Premium.

1. The bot from the Telegram guide, connected first.
2. In @BotFather: `/mybots` → the bot → Bot Settings → Business Mode → Turn on.
3. In Telegram: Settings → Telegram Business → Chatbots → enter the bot's username → allow it to manage stories.
4. The bot's key again, `TELEGRAM_BOT_TOKEN`: @BotFather → `/mybots` → the bot → API Token.
5. `connect --to telegram --placement story`.

First post proves: nothing.

## VK community or personal page

1. The operator must be the community's owner or administrator (the top admin level).
2. `connect --to vk --link <their page link>`; they open the link it gives, sign in to VK with that account and allow access.

First post proves: rights to upload video.

## VK stories

1. In the community: Manage → API usage → Access tokens → Create token → tick Stories → Create, confirm, and copy the key: `VK_ACCESS_TOKEN`.
2. `connect --to vk --placement story --link <community link>`. The link has to name the community by number, `vk.com/club123`; if theirs is a custom name, ask for the number shown in the community's settings.

First post proves: nothing.

## Discord channel

1. They need the Manage Webhooks permission on the server.
2. Channel → Edit Channel (gear) → Integrations → Webhooks → New Webhook → choose the channel → Copy Webhook URL. The whole link is `DISCORD_WEBHOOK_URL`; it is a secret.
3. `connect --to discord`.

First post proves: nothing.

## Mastodon account

1. Their profile link, such as `https://mastodon.social/@name`.
2. Open `https://<server>/settings/applications/new`, name the application, tick `read:accounts`, `write:statuses` and `write:media`, Submit, open the application and copy "Your access token": `MASTODON_ACCESS_TOKEN`.
3. `connect --to mastodon --link <profile link>`.

First post proves: that media upload works.

## Bluesky account

1. Their profile link, such as `https://bsky.app/profile/name.bsky.social`.
2. In Bluesky: Settings → Privacy and security → App passwords → Add App Password → name it → copy the password of the form `xxxx-xxxx-xxxx-xxxx`: `BLUESKY_PASSWORD`. Never their account password.
3. `connect --to bluesky --link <profile link>`. An account hosted on another server than bsky.social also needs that server in `BLUESKY_HOST`.

First post proves: nothing.

## MAX channel or chat

1. Create the bot in MAX for business, https://business.max.ru → Chatbots → the bot → settings, and copy its token: `MAX_BOT_TOKEN`.
2. Add the bot to the channel; in a channel it needs the owner's or an administrator's right to write.
3. `connect --to max --link <chat id>`: MAX takes the chat's numeric id.

First post proves: nothing. A refusal from MAX's side goes to mail@smmtp.ru.

## Sign-in platforms

X, LinkedIn, Facebook pages, Instagram, Threads, YouTube and Slack connect by signing in; nothing is typed. `connect --to <platform> --link <their link>`; they open the link it gives, sign in with the account that publishes and allow access.

- **LinkedIn**: a company page link (`/company/…`) connects the page and needs them to be its administrator; a profile link (`/in/…`) connects them personally.
- **Instagram**: a Business or Creator account. Every post needs an image or video.
- **Facebook**: a page they manage with the right to create content.
- **Threads**: a public account.
- **YouTube**: the Google account that owns the channel. Every post needs a video; visibility and the daily upload quota show only on the first upload.
- **Slack**: after signing in, they invite the app's bot to the channel with `/invite`.
