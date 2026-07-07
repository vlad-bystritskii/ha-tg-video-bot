#!/usr/bin/env python3
"""Personal Telegram video-downloader bot.

Send it a link -> it downloads with yt-dlp and sends the video back. Paired with
a local telegram-bot-api server (see run.sh / docker-compose.yml) it can return
files up to 2 GB instead of the 50 MB cloud Bot API limit.

Configuration comes from environment variables (set by run.sh on Home Assistant,
or directly in docker-compose.yml):

  BOT_TOKEN          Telegram bot token
  BOT_API_BASE_URL   base URL of the local Bot API server (default http://127.0.0.1:8081)
  ALLOWED_USER_IDS   comma-separated numeric user ids; empty = first-owner mode
  DEFAULT_FORMAT     best | hd480  (used for a bare link)
  COOKIES_PATH       optional path to a Netscape cookies.txt
  DATA_DIR           writable dir for owner.json and temp downloads (default /data)
"""
from __future__ import annotations

import asyncio
import glob
import json
import logging
import os
import re
import shutil
import uuid
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
log = logging.getLogger("tg-video-bot")

# --- config ------------------------------------------------------------------
BOT_TOKEN = os.environ["BOT_TOKEN"]
BOT_API_BASE_URL = os.environ.get("BOT_API_BASE_URL", "http://127.0.0.1:8081").rstrip("/")
DEFAULT_FORMAT = os.environ.get("DEFAULT_FORMAT", "best").strip() or "best"
COOKIES_PATH = os.environ.get("COOKIES_PATH", "").strip()
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
TMP_ROOT = DATA_DIR / "tmp"
OWNER_FILE = DATA_DIR / "owner.json"

URL_RE = re.compile(r"https?://\S+")

# -S makes yt-dlp prefer a widely-compatible h264/aac MP4 (streams nicely in TG),
# ported from the user's ~/.local/bin/y script.
SORT = "vcodec:h264,lang,quality,res,fps,hdr:12,acodec:aac"
FORMAT = {
    "best": "bv*+ba/b",
    "hd480": "bv*[height<=480]+ba/b[height<=480]/b[height<=480]",
}


def allowed_ids() -> set[int]:
    raw = os.environ.get("ALLOWED_USER_IDS", "")
    return {int(x) for x in raw.replace(" ", "").split(",") if x}


def is_authorized(user_id: int) -> bool:
    """Whitelist if configured; otherwise first-owner mode."""
    ids = allowed_ids()
    if ids:
        return user_id in ids
    try:
        owner = json.loads(OWNER_FILE.read_text())["owner"]
    except (FileNotFoundError, ValueError, KeyError):
        owner = None
    if owner is None:
        OWNER_FILE.parent.mkdir(parents=True, exist_ok=True)
        OWNER_FILE.write_text(json.dumps({"owner": user_id}))
        log.info("first-owner mode: locked bot to user %s", user_id)
        return True
    return user_id == owner


def cookies_args() -> list[str]:
    if COOKIES_PATH and Path(COOKIES_PATH).is_file():
        return ["--cookies", COOKIES_PATH]
    return []


async def run_ytdlp(url: str, mode: str, workdir: Path, sub_langs: str = "en.*") -> list[Path]:
    """Download `url` in the given mode. Returns produced file paths."""
    workdir.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(workdir / "%(title).150B [%(id)s].%(ext)s")
    printed = workdir / "downloaded.txt"

    base = [
        "yt-dlp",
        "--no-playlist",
        "--no-progress",
        "--no-warnings",
        "-o", out_tmpl,
        *cookies_args(),
    ]

    if mode == "subs":
        cmd = base + [
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs", f"{sub_langs},-live_chat",
            "--sub-format", "srt/vtt/best",
            "--convert-subs", "srt",
            url,
        ]
    else:
        cmd = base + ["--print-to-file", "after_move:%(filepath)s", str(printed)]
        if mode == "audio":
            cmd += ["-f", "ba/b", "-x", "--audio-format", "m4a"]
        else:  # best / hd480
            cmd += [
                "--merge-output-format", "mp4",
                "--remux-video", "mp4",
                "--write-info-json",
                "-S", SORT,
                "-f", FORMAT.get(mode, FORMAT["best"]),
            ]
        cmd += [url]

    log.info("yt-dlp: %s", " ".join(cmd[-4:]))
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        tail = (stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError("\n".join(tail[-4:]) or "yt-dlp failed")

    if mode == "subs":
        return sorted(workdir.glob("*.srt"))
    files = [Path(p) for p in printed.read_text().splitlines() if p.strip()] if printed.exists() else []
    return [f for f in files if f.is_file()]


async def probe_language(url: str) -> str | None:
    """Ask yt-dlp for the video's original language (e.g. 'en', 'ru')."""
    cmd = ["yt-dlp", "--no-warnings", "--skip-download",
           "--print", "%(language)s", *cookies_args(), url]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        return None
    lines = out.decode("utf-8", "replace").strip().splitlines()
    val = (lines[0].strip() if lines else "").split("-")[0].lower()
    return val if val and val not in ("na", "none", "null") else None


async def download_subs(url: str, workdir: Path) -> list[Path]:
    """Original-language subtitles first, fall back to English."""
    lang = await probe_language(url)
    attempts = [f"{lang}.*"] if lang and lang != "en" else []
    attempts.append("en.*")  # fallback (and the only try when original is en/unknown)
    for idx, langs in enumerate(attempts):
        files = await run_ytdlp(url, "subs", workdir / f"try{idx}", sub_langs=langs)
        if files:
            return files
    return []


def video_meta(path: Path) -> dict:
    """width/height/duration from the sibling *.info.json, if any."""
    infos = glob.glob(str(path.parent / "*.info.json"))
    if not infos:
        return {}
    try:
        data = json.loads(Path(infos[0]).read_text())
    except (ValueError, OSError):
        return {}
    meta = {}
    for key in ("width", "height", "duration"):
        val = data.get(key)
        if isinstance(val, (int, float)):
            meta[key] = int(val)
    return meta


async def send_result(update: Update, mode: str, files: list[Path]) -> None:
    chat = update.effective_chat
    for path in files:
        if mode == "subs":
            with path.open("rb") as fh:
                await chat.send_document(document=fh, filename=path.name)
            continue
        if mode == "audio":
            with path.open("rb") as fh:
                await chat.send_audio(audio=fh, filename=path.name)
            continue
        meta = video_meta(path)
        try:
            with path.open("rb") as fh:
                await chat.send_video(
                    video=fh,
                    filename=path.name,
                    supports_streaming=True,
                    width=meta.get("width"),
                    height=meta.get("height"),
                    duration=meta.get("duration"),
                    caption=path.stem[:1000],
                )
        except Exception as exc:  # noqa: BLE001 — fall back to a plain file
            log.warning("send_video failed (%s); retrying as document", exc)
            with path.open("rb") as fh:
                await chat.send_document(document=fh, filename=path.name)


async def process(update: Update, url: str, mode: str) -> None:
    status = await update.message.reply_text("⏳ downloading…")
    workdir = TMP_ROOT / uuid.uuid4().hex
    try:
        await update.effective_chat.send_action(ChatAction.RECORD_VIDEO)
        if mode == "subs":
            files = await download_subs(url, workdir)
        else:
            files = await run_ytdlp(url, mode, workdir)
        if not files:
            await status.edit_text("⚠️ nothing was downloaded")
            return
        await status.edit_text("⬆️ uploading…")
        await update.effective_chat.send_action(ChatAction.UPLOAD_VIDEO)
        await send_result(update, mode, files)
        await status.delete()
    except Exception as exc:  # noqa: BLE001 — report back to the user
        log.exception("download failed")
        msg = str(exc)
        await status.edit_text(f"❌ failed:\n{msg[:1500]}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --- handlers ----------------------------------------------------------------
def guard(update: Update) -> bool:
    user = update.effective_user
    return bool(user) and is_authorized(user.id)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not guard(update):
        await update.message.reply_text("⛔ this bot is private")
        return
    match = URL_RE.search(update.message.text or "")
    if not match:
        await update.message.reply_text("send me a video link 🙂")
        return
    await process(update, match.group(0), DEFAULT_FORMAT)


def _command(command: str, mode: str):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not guard(update):
            await update.message.reply_text("⛔ this bot is private")
            return
        text = " ".join(context.args) if context.args else ""
        match = URL_RE.search(text)
        if not match:
            await update.message.reply_text(f"usage: /{command} <link>")
            return
        await process(update, match.group(0), mode)

    return handler


async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    guard(update)  # in first-owner mode this claims ownership
    await update.message.reply_text(
        "Hi! Send me a link and I'll send the video back.\n\n"
        "Commands:\n"
        "• just a link — best quality (mp4)\n"
        "• /hd <link> — capped at 480p (smaller file)\n"
        "• /audio <link> — audio only (m4a)\n"
        "• /subs <link> — subtitles only (srt): original language, else English"
    )


def main() -> None:
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .base_url(f"{BOT_API_BASE_URL}/bot")
        .base_file_url(f"{BOT_API_BASE_URL}/file/bot")
        .concurrent_updates(True)
        .read_timeout(1800)
        .write_timeout(1800)
        .connect_timeout(60)
        .pool_timeout(60)
        .build()
    )
    app.add_handler(CommandHandler(["start", "help"], on_start))
    app.add_handler(CommandHandler("hd", _command("hd", "hd480")))
    app.add_handler(CommandHandler("audio", _command("audio", "audio")))
    app.add_handler(CommandHandler("subs", _command("subs", "subs")))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    log.info("bot starting; Bot API base = %s", BOT_API_BASE_URL)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
