<div align="center">

# 🎬 TG Video Downloader

**Send a link to your own Telegram bot — get the video back.**

A private, self-hosted bot around [`yt-dlp`](https://github.com/yt-dlp/yt-dlp).
Ships a local [telegram-bot-api](https://github.com/tdlib/telegram-bot-api) server
so it can return files up to **2 GB** (not the usual 50 MB cloud limit).

![home assistant](https://img.shields.io/badge/Home%20Assistant-add--on-41BDF5?logo=home-assistant&logoColor=white)
![docker](https://img.shields.io/badge/also-docker--compose-2496ED?logo=docker&logoColor=white)
![license](https://img.shields.io/badge/license-MIT-blue)

</div>

---

## What it does

- 📥 Send any supported link → best MP4-compatible (h264/aac) video back in chat.
- 🧰 `/fast` (≤720p) · `/audio` (m4a) · `/subs` (srt, original language → English).
- 📦 Up to **2 GB** files via a bundled local Bot API server.
- 🔒 Private by default — whitelist, or *first-owner* mode.
- 👥 Share it: a stranger who messages the bot is queued, and you let them in with
  an **allow / deny** button. `/requests off` mutes the pings while you're away.
- 🍪 Optional `cookies.txt` for age-gated / private videos — just send the file to the bot.
- 🔁 Cookies expire and YouTube starts answering `403`; when that happens the bot
  hands the owner a copy-paste command that re-exports them, trimmed to the site
  that failed. Run it, send the file back, done.

Two ways to run it, **one repo**:

## Option A — Home Assistant add-on

1. **Settings → Add-ons → Add-on Store → ⋮ → Repositories** and add:
   ```
   https://github.com/vlad-bystritskii/ha-tg-video-bot
   ```
2. Install **TG Video Downloader**, open **Configuration**, set `bot_token`,
   `api_id`, `api_hash` (see [DOCS](tg_video_bot/DOCS.md) for where to get them).
3. **Start**, enable *Start on boot* + *Watchdog*, message your bot.

## Option B — Docker Compose (any Linux / NAS, no Home Assistant)

```bash
git clone https://github.com/vlad-bystritskii/ha-tg-video-bot
cd ha-tg-video-bot
cp .env.example .env          # fill in BOT_TOKEN / API_ID / API_HASH
touch cookies.txt             # optional; leave empty if you don't need cookies
docker compose up -d --build
```

## Getting `api_id` / `api_hash`

The local Bot API server needs Telegram *application* credentials (separate from
the bot token): <https://my.telegram.org> → *API development tools* → create an app.
Full walkthrough in **[tg_video_bot/DOCS.md](tg_video_bot/DOCS.md)**.

## Security

Private bot on purpose: it denies everyone until you either list your
`allowed_user_ids` (the bot's *owners*) or claim it in first-owner mode. Other
people only get in when an owner approves them, and approved users can download
but can't approve anyone or touch your cookies. Access requests are rate-limited
and mutable, so the bot can't be used to spam you. It only makes outbound
connections (long polling) — no ports are exposed. Never commit your `.env` or
`cookies.txt` (both are git-ignored).

## License

MIT — see [LICENSE](LICENSE).
