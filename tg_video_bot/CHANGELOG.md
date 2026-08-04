# Changelog

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
