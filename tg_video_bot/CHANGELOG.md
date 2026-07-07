# Changelog

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
