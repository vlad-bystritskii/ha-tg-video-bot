# TG Video Downloader

A private Telegram bot: send it a link, it downloads the video with
[`yt-dlp`](https://github.com/yt-dlp/yt-dlp) and sends the file back to you.

It bundles a **local [telegram-bot-api](https://github.com/tdlib/telegram-bot-api)
server**, which raises Telegram's upload limit from **50 MB to 2 GB** — enough for
real videos, not just short clips.

## What you need first

1. **A bot token** — talk to [@BotFather](https://t.me/BotFather), `/newbot`, copy the token.
2. **api_id + api_hash** — the local Bot API server needs these. Go to
   <https://my.telegram.org> → *API development tools* → create an app → copy
   `api_id` and `api_hash`. (These belong to *your Telegram account*, not the bot.)
3. **Your numeric user id** *(optional)* — from [@userinfobot](https://t.me/userinfobot).
   You can skip this and use *first-owner* mode instead (see below).

## Configuration

| Option             | Default                              | Description                                                        |
|--------------------|--------------------------------------|--------------------------------------------------------------------|
| `bot_token`        | —                                    | Bot token from @BotFather (required)                               |
| `api_id`           | —                                    | api_id from my.telegram.org (required)                             |
| `api_hash`         | —                                    | api_hash from my.telegram.org (required)                           |
| `allowed_user_ids` | `[]`                                 | Numeric ids allowed to use the bot. Empty → first-owner mode       |
| `default_format`   | `best`                               | Quality for a bare link: `best` or `fast` (best available ≤720p)   |
| `cookies_path`     | `/config/tg_video_bot/cookies.txt`   | Optional Netscape cookies.txt for age-gated / private videos       |

Example:

```yaml
bot_token: "123456:ABC-DEF..."
api_id: 1234567
api_hash: "0123456789abcdef0123456789abcdef"
allowed_user_ids:
  - 111111111
default_format: best
```

### Access control (important — this is a *personal* bot)

- If `allowed_user_ids` is **set**, only those users are answered.
- If it's **empty**, the bot runs in **first-owner** mode: the *first* person who
  messages it becomes the sole owner (stored in `/data/owner.json`) and everyone
  else is refused. Send `/start` right after first launch to claim it.

Leaving the bot open to strangers would let anyone make your server download
arbitrary URLs, so it always denies by default.

## Usage

- Send any link → best MP4-compatible quality.
- `/fast <link>` → best available up to 720p (smaller / faster).
- `/audio <link>` → audio only (m4a).
- `/subs <link>` → subtitles only (srt): the video's original language, else English.

Videos are sent as in-app **playable videos**; if Telegram can't render one
(bad metadata / edge cases) it falls back to sending it as a plain file and tells
you so.

## Cookies (age-gated / private / subscriptions)

The add-on has no browser, so cookies are exported elsewhere. Two ways to install them:

**Easiest — send the file to the bot.** Export a `cookies.txt` (see below) and just
send it to the bot as a document in Telegram. It saves it to `cookies_path` and uses
it from then on. (Only the owner can do this — the bot is private.)

**Or drop the file in manually.** On a machine that has the browser logged in
(e.g. your Mac with Chrome):

```bash
yt-dlp --cookies-from-browser chrome \
       --cookies /path/to/ha/config/tg_video_bot/cookies.txt \
       --skip-download --playlist-items 0 https://www.youtube.com/
```

Put the resulting `cookies.txt` at `/config/tg_video_bot/cookies.txt` (the add-on's
`/config` maps to Home Assistant's config folder). Cookies expire, so refresh it
periodically (a scheduled job works well). Tip: use a throwaway account/profile —
`yt-dlp` can invalidate cookies of an actively used browser session.

## Notes

- Nothing is exposed on the network; the bot only makes outbound connections
  (long polling), so no port forwarding is needed.
- Temp downloads live under `/data/tmp` and are deleted after each send.
