"""Telegram Bot — serve original media albums via deep links."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from telegram import InputMediaDocument, InputMediaPhoto, InputMediaVideo, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from bot import media_db
from shared.config import load_settings
from shared.logger import log_buffer

logging.getLogger("httpx").setLevel(logging.WARNING)

_app: Optional[Application] = None
_bot_task: Optional[asyncio.Task] = None
_pending_harvest: Optional[str] = None
_harvest_group_id: Optional[str] = None


def _build_media_group(file_ids: list[str], caption: str = ""):
    items = []
    for i, fid in enumerate(file_ids):
        cap = caption if i == 0 else None
        # Telegram file_id prefix hints type; fallback photo
        if fid.startswith("AgAC") or fid.startswith("BQAC"):
            items.append(InputMediaPhoto(media=fid, caption=cap))
        elif fid.startswith("BAAC") or fid.startswith("CgAC"):
            items.append(InputMediaVideo(media=fid, caption=cap))
        else:
            items.append(InputMediaDocument(media=fid, caption=cap))
    return items


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    cfg = load_settings().get("bot", {})
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "👋 Chào bạn!\n\n"
            "Dùng link dạng: /start alb_xxxx để xem media.\n"
            "Hoặc bấm link từ bài viết trong forum."
        )
        return

    token = args[0]
    if not token.startswith("alb_"):
        await update.message.reply_text("❌ Link không hợp lệ.")
        return

    await update.message.reply_text(cfg.get("start_message", "🎬 Đang tải..."))

    album = media_db.get_album(token)
    if not album:
        await update.message.reply_text("❌ Không tìm thấy media hoặc link đã hết hạn.")
        log_buffer.warn("BOT", f"Token không tồn tại: {token}")
        return

    file_ids = album["file_ids"]
    caption = album["caption"] if cfg.get("keep_caption", True) else ""

    if not file_ids:
        await update.message.reply_text("⏳ Media đang được xử lý, vui lòng thử lại sau ít phút.")
        log_buffer.warn("BOT", f"Token {token} chưa có file_id")
        return

    try:
        if cfg.get("keep_album", True) and len(file_ids) > 1:
            media = _build_media_group(file_ids, caption)
            await update.message.reply_media_group(media=media)
        else:
            fid = file_ids[0]
            if fid.startswith("AgAC") or fid.startswith("BQAC"):
                await update.message.reply_photo(photo=fid, caption=caption or None)
            elif fid.startswith("BAAC") or fid.startswith("CgAC"):
                await update.message.reply_video(video=fid, caption=caption or None)
            else:
                await update.message.reply_document(document=fid, caption=caption or None)

        if cfg.get("track_views", True):
            media_db.increment_views(token)
        user = update.effective_user
        uname = f"@{user.username}" if user and user.username else str(user.id if user else "?")
        log_buffer.ok("BOT", f"User {uname} → {token} → gửi {len(file_ids)} file")
    except Exception as e:
        log_buffer.err("BOT", f"Lỗi gửi {token}: {e}")
        await update.message.reply_text(f"❌ Không gửi được media: {e}")


async def harvest_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Capture file_ids when userbot forwards media for harvesting."""
    global _pending_harvest, _harvest_group_id
    if not update.message:
        return

    text = update.message.text or update.message.caption or ""
    if text.startswith("HARVEST:"):
        _pending_harvest = text.split(":", 1)[1].strip()
        _harvest_group_id = None
        return

    if not update.message.media and not update.message.media_group_id:
        return

    fid = _extract_file_id(update.message)
    if not fid:
        return

    token = _pending_harvest
    if not token:
        return

    album = media_db.get_album(token)
    if album:
        existing = album.get("file_ids") or []
        if fid not in existing:
            merged = existing + [fid]
            media_db.update_file_ids(token, merged)
            log_buffer.ok("BOT", f"Harvest {token}: +1 file_id (total {len(merged)})")

    # Only clear the pending token when NOT part of an ongoing media group
    group_id = update.message.media_group_id
    if group_id:
        _harvest_group_id = group_id
    else:
        # Single media message — harvest complete
        _pending_harvest = None
        _harvest_group_id = None


def _extract_file_id(message) -> Optional[str]:
    if message.photo:
        return message.photo[-1].file_id
    if message.video:
        return message.video.file_id
    if message.document:
        return message.document.file_id
    if message.audio:
        return message.audio.file_id
    return None


async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    stats = media_db.get_stats()
    await update.message.reply_text(
        f"📊 Bot stats\n"
        f"Albums: {stats.get('total_albums', 0)}\n"
        f"Files: {stats.get('total_files', 0)}\n"
        f"Views: {stats.get('total_views', 0)}"
    )


def is_running() -> bool:
    return _bot_task is not None and not _bot_task.done()


async def start_bot() -> bool:
    global _app, _bot_task
    if is_running():
        return True

    cfg = load_settings()
    token = cfg.get("bot", {}).get("token", "")
    if not token:
        log_buffer.warn("BOT", "Chưa cấu hình bot token")
        return False

    media_db.init_db()

    _app = Application.builder().token(token).build()
    _app.add_handler(CommandHandler("start", start_handler))
    _app.add_handler(CommandHandler("status", status_handler))
    _app.add_handler(
        MessageHandler(
            filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.TEXT,
            harvest_handler,
        )
    )

    await _app.initialize()
    await _app.start()
    await _app.updater.start_polling(drop_pending_updates=True)

    # Keep a sentinel task so is_running() returns True while polling is active
    async def _sentinel():
        while _app and _app.updater and _app.updater.running:
            await asyncio.sleep(5)

    _bot_task = asyncio.create_task(_sentinel())
    log_buffer.ok("BOT", f"Bot polling started (@{cfg.get('bot', {}).get('username', '?')})")
    return True


async def stop_bot() -> None:
    global _app, _bot_task
    if _app:
        try:
            if _app.updater and _app.updater.running:
                await _app.updater.stop()
            await _app.stop()
            await _app.shutdown()
        except Exception as e:
            log_buffer.warn("BOT", f"Stop error: {e}")
        _app = None
    if _bot_task and not _bot_task.done():
        _bot_task.cancel()
    _bot_task = None
    log_buffer.info("BOT", "Bot stopped")
