# Changelog

## 0.4.3

- Logs now survive a rebuild. They are written to `/data/logs/tg-video-bot.log`
  (20 MB × 11) as well as to stdout, because recreating the container takes its
  journal with it — and the journal is where the answer to "why did that
  download fail yesterday" lives.
- Every download leaves a trail worth reading: the full `yt-dlp` command line
  (pasteable into a shell as-is), how long it took, its exit code, and its
  warnings even when it succeeded — a silently substituted format explains an
  odd result hours later.
- Each update gets a short id, printed on every line it causes, so one download
  can be pulled out of a busy log with a single `grep`.
- Logs default to `DEBUG` (set `LOG_LEVEL` to change it) and bot tokens are
  masked on the way out, so the HTTP layer is finally readable without leaking
  the token that used to hide in every Telegram URL.

## 0.4.2

- A download that fails on `403 Forbidden` (or another sign-in/cookie error) now
  tells owners **how to fix it**: the bot replies with a copy-paste command that
  re-exports the browser cookies, trims them to the failing site, and drops a
  `cookies.txt` on the Desktop — send that file back and the bot is working
  again. Non-owners just get "ask an owner to refresh the cookies".
- The suggested export no longer ships your whole cookie jar to the add-on: it
  keeps only the domains the download needs (for YouTube: `youtube.com`,
  `.google.com`, `accounts.google.com`), not your mail/drive/cloud sessions.

## 0.4.1

- `/subs` no longer sends the same subtitles twice when a source exposes several
  tracks for one language (YouTube serves both `en` and `en-orig`). It now sends
  a single file, preferring the original-language track.

## 0.4.0

- Other people can now use the bot **with the owner's approval**: a stranger who
  messages it gets an "asked the owner" reply, the owner gets an *allow / deny*
  button, and approved users are remembered in `/data/access.json`.
- Spam control: one open request per person, at most 5 owner pings an hour, and a
  20-request queue cap. `/requests off` mutes the pings while you're away —
  people keep queueing quietly and `/requests on` hands you the whole queue.
- New owner commands: `/users`, `/pending`, `/requests on|off`, `/allow <id>`,
  `/revoke <id>`.
- New option `allow_access_requests` (default on) to turn the request flow off.
- `allowed_user_ids` are now the bot's *owners*: only they can approve people and
  install a `cookies.txt`. Approved users can only download.

## 0.3.2

- Use Node.js (native musl, via apk) as the yt-dlp JS runtime instead of a
  glibc-shimmed Deno that failed to execute on Alpine. Fixes YouTube downloads.

## 0.3.1

- Add `yt-dlp[default]` + a JS runtime so YouTube works (fixes "Requested format
  is not available" caused by unsolved JS challenges).

## 0.3.0

- Send a `cookies.txt` file to the bot and it installs it (no manual file copy needed).

## 0.2.1

- Added add-on icon / logo.

## 0.2.0

- Renamed `/hd` → `/fast`, now best available up to 720p (steps down automatically).
- Videos always try to send as a playable video first; on fallback to a file the
  bot says why.
- Temp download dir is wiped on start (in addition to per-request cleanup).

## 0.1.1

- Bot messages are now in English.
- `/subs` downloads the video's original-language subtitles, falling back to English.

## 0.1.0

- Initial release.
- Send a link → get the video back (yt-dlp, best MP4-compatible h264/aac).
- Local `telegram-bot-api` server bundled → uploads up to 2 GB.
- Commands: `/hd` (≤480p), `/audio` (m4a), `/subs` (srt).
- Access control: whitelist via `allowed_user_ids`, or first-owner mode when empty.
- Optional `cookies.txt` for age-gated / private videos.
