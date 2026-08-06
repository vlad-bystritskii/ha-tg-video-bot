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
   You can skip this and use *first-owner* mode instead (see below). To share the
   bot with someone, you don't need their id up front — they can just message it
   and you approve them with a button.

## Configuration

| Option             | Default                              | Description                                                        |
|--------------------|--------------------------------------|--------------------------------------------------------------------|
| `bot_token`        | —                                    | Bot token from @BotFather (required)                               |
| `api_id`           | —                                    | api_id from my.telegram.org (required)                             |
| `api_hash`         | —                                    | api_hash from my.telegram.org (required)                           |
| `allowed_user_ids` | `[]`                                 | Numeric ids of the bot's **owners**. Empty → first-owner mode       |
| `allow_access_requests` | `true`                          | Let strangers ask an owner for access (see below)                   |
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

### Access control (important — this is a *private* bot)

There are two tiers:

- **Owners** — `allowed_user_ids`, or the first-owner if that list is empty (the
  *first* person to message the bot claims it, stored in `/data/owner.json`; send
  `/start` right after first launch). Owners can download, approve other people
  and install a `cookies.txt`.
- **Approved users** — people an owner let in. They can only download. They are
  stored in `/data/access.json`, so they survive restarts and add-on updates.

Everyone else is refused. Leaving the bot open to strangers would let anyone make
your server download arbitrary URLs, so it always denies by default.

**Letting someone in.** Two ways:

- They message the bot → they're told the owner was asked, and you get a card
  with **✅ allow / ⛔ deny** buttons. One tap and they're in.
- Or add them yourself with `/allow <id>` (their id comes from
  [@userinfobot](https://t.me/userinfobot)).

Set `allow_access_requests: false` if you don't want strangers to be able to ask
at all — then only `/allow` works.

**Not getting spammed.** Even with requests on, the bot is deliberately hard to
flood:

- one open request per person — repeat messages never re-ping you;
- **⛔ deny** is permanent: that person is silently refused from then on and can
  never ping you again (`/revoke <id>` undoes it if you change your mind);
- at most **5 notifications an hour**, and at most **20** queued requests;
- `/requests off` mutes them entirely while you're away.

Muting never rejects anyone — people keep queueing quietly and are told the owner
isn't taking requests right now. When you're back, `/requests on` unmutes and
hands you the whole queue as cards to decide on; `/pending` shows it any time
without changing the setting.

## Usage

- Send any link → best MP4-compatible quality.
- `/fast <link>` → best available up to 720p (smaller / faster).
- `/audio <link>` → audio only (m4a).
- `/subs <link>` → subtitles only (srt): the video's original language, else English.

Owner-only commands:

- `/users` → owners, approved users, who's waiting.
- `/pending` → re-send the allow/deny cards for everyone in the queue.
- `/requests on|off` → unmute / mute access requests.
- `/allow <id>`, `/revoke <id>` → grant or take away access.

Videos are sent as in-app **playable videos**; if Telegram can't render one
(bad metadata / edge cases) it falls back to sending it as a plain file and tells
you so.

## Cookies (age-gated / private / subscriptions)

The add-on has no browser, so cookies are exported elsewhere. Two ways to install them:

**Easiest — send the file to the bot.** Export a `cookies.txt` (see below) and just
send it to the bot as a document in Telegram. It saves it to `cookies_path` and uses
it from then on. (Owners only — the cookies are your own session, so an approved
user can't replace them.)

**Or drop the file in manually.** On a machine that has the browser logged in
(e.g. your Mac with Chrome):

```bash
yt-dlp --cookies-from-browser chrome \
       --cookies /path/to/ha/config/tg_video_bot/cookies.txt \
       --skip-download --playlist-items 0 https://www.youtube.com/
```

Put the resulting `cookies.txt` at `/config/tg_video_bot/cookies.txt` (the add-on's
`/config` maps to Home Assistant's config folder).

⚠️ `--cookies-from-browser` dumps **every** cookie in the browser — your mail,
drive and everything else you're signed into. Keep only what the download needs:

```bash
awk -F'\t' '/^#/ || $1 ~ /(^|\.)youtube\.com$/ || $1 == ".google.com" \
            || $1 == "accounts.google.com"' all-cookies.txt > cookies.txt
```

### When cookies go stale

They will: YouTube rotates its tokens while the browser keeps using them, and the
exported set stops working within days. The symptom is a download that fails with
`HTTP Error 403: Forbidden` — extraction succeeds, only the media URL is refused.

When that happens the bot replies to an **owner** with a ready-made refresh
command (already scoped to the failing site) — copy it, run it, and send the
`cookies.txt` it leaves on your Desktop back to the bot.

To make them last, export from a throwaway account in a **private window**: log
in, open `https://www.youtube.com/robots.txt` (not a video, so the player doesn't
rotate anything), export, then close the window *without* logging out.

## Notes

- Nothing is exposed on the network; the bot only makes outbound connections
  (long polling), so no port forwarding is needed.
- Temp downloads live under `/data/tmp` and are deleted after each send.
- Approved users and the request queue live in `/data/access.json`; owners come
  from the add-on config (or `/data/owner.json` in first-owner mode).
