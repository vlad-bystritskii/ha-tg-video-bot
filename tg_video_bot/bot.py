#!/usr/bin/env python3
"""Personal Telegram video-downloader bot.

Send it a link -> it downloads with yt-dlp and sends the video back. Paired with
a local telegram-bot-api server (see run.sh / docker-compose.yml) it can return
files up to 2 GB instead of the 50 MB cloud Bot API limit.

Configuration comes from environment variables (set by run.sh on Home Assistant,
or directly in docker-compose.yml):

  BOT_TOKEN             Telegram bot token
  BOT_API_BASE_URL      base URL of the local Bot API server (default http://127.0.0.1:8081)
  ALLOWED_USER_IDS      comma-separated numeric admin ids; empty = first-owner mode
  ALLOW_ACCESS_REQUESTS true (default) = strangers can ask an admin for access
  DEFAULT_FORMAT        best | fast  (used for a bare link)
  COOKIES_PATH          optional path to a Netscape cookies.txt
  DATA_DIR              writable dir for owner.json, access.json and temp downloads
                        (default /data)
"""
from __future__ import annotations

import asyncio
import glob
import html
import json
import logging
import os
import re
import shlex
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, User
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
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
ACCESS_FILE = DATA_DIR / "access.json"
ALLOW_ACCESS_REQUESTS = os.environ.get(
    "ALLOW_ACCESS_REQUESTS", "true"
).strip().lower() not in ("0", "false", "no", "off")

DENIED_MSG = "⛔ this bot is private"

URL_RE = re.compile(r"https?://\S+")

# -S makes yt-dlp prefer a widely-compatible h264/aac MP4 (streams nicely in TG),
# ported from the user's ~/.local/bin/y script.
SORT = "vcodec:h264,lang,quality,res,fps,hdr:12,acodec:aac"
FORMAT = {
    "best": "bv*+ba/b",
    # Best available up to 720p. `height<=720` already steps down 720 -> 480 ->
    # lower on its own; the trailing /b covers the rare case where every format
    # is above 720p.
    "fast": "bv*[height<=720]+ba/b[height<=720]/b[height<=720]/b",
}


# --- access control ----------------------------------------------------------
# Two tiers: *admins* (the configured whitelist, or the first-owner) can use the
# bot, install cookies and approve others; *approved* users, stored in
# access.json, can only download. Everyone else is refused — but may ask an admin
# for access, which the admin grants with an inline button.
#
# Spam control, so a leaked bot link can't turn into a flood of notifications:
# one open request per person, a hard cap on the queue, and at most RATE pings to
# the admins per hour. Anything over that — or anything at all while the admin is
# muted via /requests off — is queued *quietly* and delivered on /pending or
# /requests on. Nobody is auto-approved; the queue only decides when you're told.
PING_RATE = 5             # admin notifications per PING_WINDOW
PING_WINDOW = 3600.0      # seconds
MAX_PENDING = 20          # queued requests before we stop accepting new ones

_pings: list[float] = []  # monotonic timestamps of recent admin notifications


def ping_allowed() -> bool:
    now = time.monotonic()
    _pings[:] = [t for t in _pings if now - t < PING_WINDOW]
    return len(_pings) < PING_RATE


def allowed_ids() -> set[int]:
    raw = os.environ.get("ALLOWED_USER_IDS", "")
    return {int(x) for x in raw.replace(" ", "").split(",") if x}


def owner_id() -> int | None:
    try:
        return int(json.loads(OWNER_FILE.read_text())["owner"])
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return None


def claim_owner(user_id: int) -> None:
    """First-owner mode: with no whitelist configured, the first person to talk
    to the bot becomes its admin."""
    if allowed_ids() or owner_id() is not None:
        return
    OWNER_FILE.parent.mkdir(parents=True, exist_ok=True)
    OWNER_FILE.write_text(json.dumps({"owner": user_id}))
    log.info("first-owner mode: locked bot to user %s", user_id)


def admin_ids() -> set[int]:
    ids = allowed_ids()
    if ids:
        return ids
    owner = owner_id()
    return {owner} if owner is not None else set()


def is_admin(user_id: int) -> bool:
    return user_id in admin_ids()


def load_access() -> dict:
    try:
        data = json.loads(ACCESS_FILE.read_text())
    except (FileNotFoundError, ValueError):
        data = {}
    data.setdefault("approved", {})  # str(user_id) -> {"who": ..., "at": ...}
    data.setdefault("pending", {})   # same shape: requests awaiting a decision
    data.setdefault("denied", [])
    data.setdefault("muted", False)  # /requests off -> queue quietly
    return data


def save_access(data: dict) -> None:
    ACCESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACCESS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def describe(user: User) -> str:
    handle = f" (@{user.username})" if user.username else ""
    return f"{user.full_name or '?'}{handle} — id {user.id}"


def pending_name(user_id: int) -> str:
    """How a queued requester described themselves, if we still know."""
    entry = load_access()["pending"].get(str(user_id))
    return entry["who"] if entry else f"id {user_id}"


def grant(user_id: int, who: str) -> None:
    access = load_access()
    access["approved"][str(user_id)] = {
        "who": who,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    access["pending"].pop(str(user_id), None)
    access["denied"] = [i for i in access["denied"] if i != user_id]
    save_access(access)


def revoke(user_id: int, deny: bool) -> None:
    access = load_access()
    access["approved"].pop(str(user_id), None)
    access["pending"].pop(str(user_id), None)
    if deny:
        if user_id not in access["denied"]:
            access["denied"].append(user_id)
    else:
        access["denied"] = [i for i in access["denied"] if i != user_id]
    save_access(access)


def is_authorized(user_id: int) -> bool:
    return is_admin(user_id) or str(user_id) in load_access()["approved"]


async def send_request_card(
    context: ContextTypes.DEFAULT_TYPE, user_id: int, who: str, to: set[int]
) -> int:
    """Send the allow/deny card for one requester. Returns how many admins got it."""
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ allow", callback_data=f"grant:{user_id}"),
        InlineKeyboardButton("⛔ deny", callback_data=f"deny:{user_id}"),
    ]])
    reached = 0
    for admin in to:
        try:
            await context.bot.send_message(
                admin, f"👤 {who}\nwants to use the bot.", reply_markup=keyboard
            )
            reached += 1
        except Exception as exc:  # noqa: BLE001 — an admin who never /start-ed us
            log.warning("couldn't notify admin %s: %s", admin, exc)
    return reached


async def ask_admins(update: Update, context: ContextTypes.DEFAULT_TYPE, user: User) -> None:
    """Refuse the user and, if requests are open, queue them for an admin."""
    message = update.effective_message
    access = load_access()
    admins = admin_ids()
    uid = str(user.id)

    # Hard no: requests disabled, nobody to ask, already turned down, or the
    # queue is full — none of these ping anyone.
    if not ALLOW_ACCESS_REQUESTS or not admins or user.id in access["denied"]:
        await message.reply_text(DENIED_MSG)
        return
    if uid in access["pending"]:
        await message.reply_text("⏳ your request is already in the queue")
        return
    if len(access["pending"]) >= MAX_PENDING:
        await message.reply_text(DENIED_MSG)
        return

    who = describe(user)
    access["pending"][uid] = {
        "who": who,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    save_access(access)
    log.info("access requested by %s", who)

    quiet = access["muted"] or not ping_allowed()
    if not quiet and await send_request_card(context, user.id, who, admins):
        _pings.append(time.monotonic())
        await message.reply_text(
            "⏳ asked the owner for access — I'll message you once they decide"
        )
        return
    # Muted, rate-limited, or no admin reachable: they stay queued silently.
    await message.reply_text(
        "⏳ the owner isn't taking requests right now — you're in the queue"
    )


async def require_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True if this user may use the bot; otherwise refuse them and return False."""
    user = update.effective_user
    if not user:
        return False
    claim_owner(user.id)
    if is_authorized(user.id):
        return True
    await ask_admins(update, context, user)
    return False


async def require_admin(update: Update) -> bool:
    user = update.effective_user
    if not user:
        return False
    claim_owner(user.id)
    if is_admin(user.id):
        return True
    await update.effective_message.reply_text(
        "⛔ only the bot's owner can do that" if is_authorized(user.id) else DENIED_MSG
    )
    return False


def cookies_args() -> list[str]:
    if COOKIES_PATH and Path(COOKIES_PATH).is_file():
        return ["--cookies", COOKIES_PATH]
    return []


# --- cookie-expiry hint ------------------------------------------------------
# An exported browser session dies fast: YouTube rotates its tokens while the
# browser keeps using them, and once the saved set is stale extraction still
# succeeds but the media URL answers 403. That looks like a broken bot, so when
# a download fails this way we hand the admin the exact refresh command.
COOKIE_ERROR_RE = re.compile(
    r"HTTP Error 40[13]"
    r"|Sign in to confirm"
    r"|confirm your age"
    r"|not a bot"
    r"|login required"
    r"|private video"
    r"|members-only"
    r"|cookie",
    re.IGNORECASE,
)

# A bare --cookies-from-browser dump carries every site you are logged into, so
# the command pipes it through awk and keeps only what this download needs.
COOKIE_REFRESH_CMD = (
    "yt-dlp --cookies-from-browser chrome --cookies /tmp/all-cookies.txt "
    "--skip-download {url} && "
    "awk -F'\\t' '/^#/ || {filter}' /tmp/all-cookies.txt "
    "> ~/Desktop/cookies.txt && rm /tmp/all-cookies.txt"
)

# YouTube logins live on google.com too, but only these two entries — not every
# mail/drive/cloud subdomain sitting in the same jar.
YOUTUBE_COOKIE_FILTER = (
    '$1 ~ /(^|\\.)youtube\\.com$/ || $1 == ".google.com" '
    '|| $1 == "accounts.google.com"'
)


def cookie_filter_expr(url: str) -> str:
    """awk condition keeping only the cookies the given site needs."""
    host = re.sub(r"[^a-z0-9.-]", "", (urlsplit(url).hostname or "").lower())
    if "youtube" in host or "youtu.be" in host:
        return YOUTUBE_COOKIE_FILTER
    base = ".".join(host.split(".")[-2:])
    if not base:
        return "1"
    return "$1 ~ /(^|\\.)%s$/" % base.replace(".", "\\.")


def cookie_hint(url: str, for_admin: bool) -> str:
    """HTML fragment telling an admin how to refresh the cookies ('' if N/A)."""
    if not COOKIES_PATH:
        return ""
    if not for_admin:
        return "\n\n🍪 the cookies may have expired — ask an owner to refresh them."
    cmd = COOKIE_REFRESH_CMD.format(
        url=shlex.quote(url), filter=cookie_filter_expr(url)
    )
    lead = (
        "looks like the cookies went stale"
        if Path(COOKIES_PATH).is_file()
        else "this one probably needs cookies, and I have none"
    )
    return (
        f"\n\n🍪 <b>{lead}.</b> Run this on your "
        "machine (swap <code>chrome</code> for firefox/safari/edge if needed):\n"
        # quote=False: inside <pre> only & < > need escaping, and turning the
        # command's own quotes into entities would only make it harder to read.
        f"<pre>{html.escape(cmd, quote=False)}</pre>\n"
        "…then send me the <code>cookies.txt</code> it leaves on your Desktop.\n"
        "Export from a private window and close it <i>without</i> logging out — "
        "YouTube keeps invalidating the cookies of a session that stays open."
    )


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
        "--js-runtimes", "node",  # solve YouTube JS challenges via Node
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
        else:  # best / fast
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
           "--js-runtimes", "node",
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


def pick_sub(files: list[Path]) -> Path:
    """One file per attempt.

    A source often exposes several tracks for the same language — YouTube serves
    both `en` and `en-orig` — and our `en.*` glob (which is there to catch `en-US`
    and friends) matches them all. They're near-identical, so send just one, and
    prefer the original-language track that /subs promises.
    """
    def rank(path: Path) -> tuple[int, str]:
        tag = path.stem.rsplit(".", 1)[-1].lower()  # "… [id].en-orig" -> "en-orig"
        return (0 if tag.endswith("-orig") else 1, path.name)

    return min(files, key=rank)


async def download_subs(url: str, workdir: Path) -> list[Path]:
    """Original-language subtitles first, fall back to English."""
    lang = await probe_language(url)
    attempts = [f"{lang}.*"] if lang and lang != "en" else []
    attempts.append("en.*")  # fallback (and the only try when original is en/unknown)
    for idx, langs in enumerate(attempts):
        files = await run_ytdlp(url, "subs", workdir / f"try{idx}", sub_langs=langs)
        if files:
            return [pick_sub(files)]
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


async def send_result(update: Update, mode: str, files: list[Path]) -> list[str]:
    """Send files back. Returns any warnings to surface to the user."""
    chat = update.effective_chat
    notes: list[str] = []
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
            # Preferred path: a real, in-app playable video (not a file).
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
            reason = str(exc).splitlines()[0][:120] if str(exc) else "unknown"
            log.warning("send_video failed (%s); retrying as document", exc)
            with path.open("rb") as fh:
                await chat.send_document(document=fh, filename=path.name)
            notes.append(f"⚠️ sent as file (couldn't send as video: {reason})")
    return notes


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
        notes = await send_result(update, mode, files)
        if notes:
            await status.edit_text("\n".join(notes))
        else:
            await status.delete()
    except Exception as exc:  # noqa: BLE001 — report back to the user
        log.exception("download failed")
        msg = str(exc)
        hint = (
            cookie_hint(url, is_admin(update.effective_user.id))
            if COOKIE_ERROR_RE.search(msg)
            else ""
        )
        text = f"❌ failed:\n{msg[:1500]}"
        if hint:
            try:
                await status.edit_text(
                    f"❌ failed:\n<pre>{html.escape(msg[:1500], quote=False)}</pre>{hint}",
                    parse_mode=ParseMode.HTML,
                )
                return
            except Exception:  # noqa: BLE001 — bad markup shouldn't eat the error
                log.exception("cookie hint failed to render; sending plain text")
        await status.edit_text(text)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --- handlers ----------------------------------------------------------------
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_access(update, context):
        return
    match = URL_RE.search(update.message.text or "")
    if not match:
        await update.message.reply_text("send me a video link 🙂")
        return
    await process(update, match.group(0), DEFAULT_FORMAT)


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A sent document is treated as a cookies.txt to install."""
    # Cookies are the owner's own Telegram/YouTube session — admins only.
    if not await require_admin(update):
        return
    if not COOKIES_PATH:
        await update.message.reply_text("cookies path is not configured")
        return
    status = await update.message.reply_text("🍪 saving cookies…")
    try:
        tg_file = await update.message.document.get_file()
        dest = Path(COOKIES_PATH)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # With the local Bot API server, get_file() usually yields an on-disk
        # path we can copy directly; otherwise fall back to an HTTP download.
        src = tg_file.file_path
        if src and os.path.isfile(src):
            shutil.copyfile(src, dest)
        else:
            await tg_file.download_to_drive(custom_path=str(dest))
        text = dest.read_text(errors="ignore")
        first = text.splitlines()[0] if text else ""
        if "Cookie File" not in first and "\t" not in text[:4000]:
            await status.edit_text(
                "⚠️ that doesn't look like a Netscape cookies.txt — saved anyway, "
                "double-check it was exported with `yt-dlp --cookies`"
            )
        else:
            entries = sum(1 for ln in text.splitlines() if ln and not ln.startswith("#"))
            await status.edit_text(
                f"🍪 cookies saved ({entries} entries) — I'll use them for downloads."
            )
    except Exception as exc:  # noqa: BLE001 — report back to the user
        log.exception("cookies upload failed")
        await status.edit_text(f"❌ couldn't save cookies:\n{str(exc)[:500]}")


def _command(command: str, mode: str):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await require_access(update, context):
            return
        text = " ".join(context.args) if context.args else ""
        match = URL_RE.search(text)
        if not match:
            await update.message.reply_text(f"usage: /{command} <link>")
            return
        await process(update, match.group(0), mode)

    return handler


async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # In first-owner mode this claims ownership; a stranger gets the access
    # request flow instead of the help text.
    if not await require_access(update, context):
        return
    text = (
        "Hi! Send me a link and I'll send the video back.\n\n"
        "Commands:\n"
        "• just a link — best quality (mp4)\n"
        "• /fast <link> — up to 720p (smaller / faster)\n"
        "• /audio <link> — audio only (m4a)\n"
        "• /subs <link> — subtitles only (srt): original language, else English"
    )
    if is_admin(update.effective_user.id):
        text += (
            "\n\nOwner only:\n"
            "• /users — who can use the bot\n"
            "• /pending — review people waiting for access\n"
            "• /requests on|off — mute access pings while you're away\n"
            "• /allow <id> · /revoke <id> — grant or take away access\n"
            "• send a cookies.txt file — used for age-gated / private videos"
        )
    await update.message.reply_text(text)


async def on_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """An admin pressed allow/deny on an access request."""
    query = update.callback_query
    admin = update.effective_user
    if not admin or not is_admin(admin.id):
        await query.answer("not your call", show_alert=True)
        return

    action, _, raw = (query.data or "").partition(":")
    try:
        target = int(raw)
    except ValueError:
        await query.answer("bad request")
        return

    who = pending_name(target)
    if action == "grant":
        grant(target, who)
        verdict, told = "✅ allowed", "✅ access granted — send me a link 🙂"
    else:
        revoke(target, deny=True)
        verdict, told = "⛔ denied", "⛔ the owner declined your request"
    log.info("%s %s by admin %s", who, verdict, admin.id)

    await query.answer(verdict)
    await query.edit_message_text(
        f"👤 {who}\n{verdict} by {admin.full_name or admin.id}", reply_markup=None
    )
    try:
        await context.bot.send_message(target, told)
    except Exception as exc:  # noqa: BLE001 — they may have blocked the bot since
        log.warning("couldn't notify user %s: %s", target, exc)


async def on_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update):
        return
    access = load_access()
    lines = ["👑 owners: " + ", ".join(str(i) for i in sorted(admin_ids()))]
    if access["approved"]:
        lines.append("\n✅ approved:")
        lines += [f"• {v.get('who', uid)}" for uid, v in access["approved"].items()]
    else:
        lines.append("\n✅ approved: nobody yet")
    if access["denied"]:
        lines.append("\n⛔ denied: " + ", ".join(str(i) for i in access["denied"]))
    if not ALLOW_ACCESS_REQUESTS:
        lines.append("\n🔕 access requests are turned off in the add-on config")
    else:
        lines.append(
            f"\n{'🔕 requests muted' if access['muted'] else '🔔 requests on'}"
            f" · ⏳ {len(access['pending'])} waiting"
        )
    lines.append("\n/pending · /requests on|off · /allow <id> · /revoke <id>")
    await update.message.reply_text("\n".join(lines))


async def deliver_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-send the queued allow/deny cards to the admin who asked for them."""
    admin = update.effective_user.id
    pending = load_access()["pending"]
    if not pending:
        await update.message.reply_text("⏳ no one is waiting")
        return
    await update.message.reply_text(f"⏳ {len(pending)} waiting for a decision:")
    for uid, info in list(pending.items()):
        await send_request_card(context, int(uid), info["who"], {admin})


async def on_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update):
        return
    await deliver_pending(update, context)


async def on_requests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/requests on|off — mute or unmute access-request notifications.

    Muting never rejects anyone: requests keep queueing quietly, and unmuting
    hands you the whole queue at once.
    """
    if not await require_admin(update):
        return
    if not ALLOW_ACCESS_REQUESTS:
        await update.message.reply_text(
            "🔕 access requests are turned off in the add-on config "
            "(`allow_access_requests`)"
        )
        return

    arg = (context.args or [""])[0].lower()
    if arg and arg not in ("on", "off"):
        await update.message.reply_text("usage: /requests on|off")
        return

    access = load_access()
    if arg:
        access["muted"] = arg == "off"
        save_access(access)
        _pings.clear()  # a fresh hourly budget when you come back
        log.info("access requests %s by admin %s", arg, update.effective_user.id)

    waiting = len(access["pending"])
    if access["muted"]:
        await update.message.reply_text(
            f"🔕 requests muted — people can still queue up ({waiting} waiting), "
            "you just won't be pinged. /requests on when you're back."
        )
        return
    await update.message.reply_text(f"🔔 requests on — {waiting} waiting")
    if arg == "on" and waiting:
        await deliver_pending(update, context)


def _access_command(action: str):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await require_admin(update):
            return
        try:
            target = int((context.args or [""])[0])
        except ValueError:
            await update.message.reply_text(f"usage: /{action} <numeric user id>")
            return
        if is_admin(target):
            await update.message.reply_text("that's an owner — set in the add-on config")
            return
        if action == "allow":
            grant(target, pending_name(target))
            note = f"✅ {target} can use the bot now"
            told = "✅ access granted — send me a link 🙂"
        else:
            # A plain /revoke lets them ask again later; deny-listing is the
            # inline button's job.
            revoke(target, deny=False)
            note = f"⛔ {target} no longer has access"
            told = "⛔ your access to this bot was removed"
        await update.message.reply_text(note)
        try:
            await context.bot.send_message(target, told)
        except Exception as exc:  # noqa: BLE001 — never messaged us / blocked us
            log.warning("couldn't notify user %s: %s", target, exc)

    return handler


def main() -> None:
    # Wipe any leftovers from a previous run (e.g. a crash mid-download), then
    # start clean. Per-request temp dirs are also removed after each send.
    shutil.rmtree(TMP_ROOT, ignore_errors=True)
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
    app.add_handler(CommandHandler("fast", _command("fast", "fast")))
    app.add_handler(CommandHandler("audio", _command("audio", "audio")))
    app.add_handler(CommandHandler("subs", _command("subs", "subs")))
    app.add_handler(CommandHandler("users", on_users))
    app.add_handler(CommandHandler("pending", on_pending))
    app.add_handler(CommandHandler("requests", on_requests))
    app.add_handler(CommandHandler("allow", _access_command("allow")))
    app.add_handler(CommandHandler("revoke", _access_command("revoke")))
    app.add_handler(CallbackQueryHandler(on_decision, pattern=r"^(grant|deny):-?\d+$"))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    log.info(
        "bot starting; Bot API base = %s; access requests = %s",
        BOT_API_BASE_URL,
        "on" if ALLOW_ACCESS_REQUESTS else "off",
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
