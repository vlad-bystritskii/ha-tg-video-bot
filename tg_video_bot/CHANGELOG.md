# Changelog

## 0.3.1

- Bundle the Deno JS runtime and `yt-dlp[default]` so YouTube works (fixes
  "Requested format is not available" caused by unsolved JS challenges).

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
