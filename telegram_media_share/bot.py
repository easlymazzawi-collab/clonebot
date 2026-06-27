"""
bot.py — aiogram 3 Share-Link Bot

Features:
  · /start TOKEN  — serve any stored media to user
  · /start        — welcome message with instructions
  · Admin panel   — stats, broadcast, ban/unban, add/remove admin
  · Forward modes — copy (hide author) / forward (show author)
  · Push media    — admin can push stored media to any chat
"""
import asyncio
import datetime
import logging
import html

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    BotCommand,
)
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

import config
from database import db

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# FSM STATES
# ══════════════════════════════════════════════════════════════════════════════

class BroadcastStates(StatesGroup):
    waiting_message = State()
    confirming      = State()

class PushStates(StatesGroup):
    waiting_token   = State()
    waiting_chat_id = State()
    waiting_mode    = State()   # "copy" | "forward"


# ══════════════════════════════════════════════════════════════════════════════
# ROUTERS
# ══════════════════════════════════════════════════════════════════════════════

user_router  = Router()
admin_router = Router()


# ══════════════════════════════════════════════════════════════════════════════
# MIDDLEWARE — track users automatically
# ══════════════════════════════════════════════════════════════════════════════

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from typing import Callable, Awaitable, Any


class UserTrackingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user:
            await db.upsert_user(
                user_id    = user.id,
                username   = user.username or "",
                first_name = user.first_name or "",
                last_name  = user.last_name or "",
            )
        return await handler(event, data)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Thống kê", callback_data="admin:stats"),
            InlineKeyboardButton(text="👥 Users",    callback_data="admin:users"),
        ],
        [
            InlineKeyboardButton(text="📢 Broadcast",    callback_data="admin:broadcast"),
            InlineKeyboardButton(text="📤 Push media",   callback_data="admin:push"),
        ],
        [
            InlineKeyboardButton(text="📜 Lịch sử BC",  callback_data="admin:bc_history"),
        ],
    ])


def _back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Quay lại", callback_data="admin:back")],
    ])


async def _is_admin(user_id: int) -> bool:
    return await db.is_admin(user_id)


def _fmt_user(u: dict) -> str:
    name = html.escape(
        " ".join(filter(None, [u.get("first_name"), u.get("last_name")])) or "?"
    )
    uname = f"@{html.escape(u['username'])}" if u.get("username") else ""
    ban   = " 🚫" if u.get("is_banned") else ""
    return f"<b>{name}</b> {uname} (<code>{u['user_id']}</code>){ban}"


# ══════════════════════════════════════════════════════════════════════════════
# USER HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

@user_router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
    args = message.text.split(maxsplit=1)
    token = args[1].strip() if len(args) > 1 else None

    if not token:
        await message.answer(
            "👋 Chào mừng bạn!\n\n"
            "Bot này phục vụ media từ các link chia sẻ.\n"
            "Hãy bấm vào một link để xem nội dung:\n"
            f"<code>https://t.me/{config.BOT_USERNAME}?start=TOKEN</code>",
            parse_mode="HTML",
        )
        return

    if await db.is_banned(message.from_user.id):
        await message.answer("🚫 Bạn đã bị cấm sử dụng bot.")
        return

    media = await db.get_media(token)
    if not media:
        await message.answer("❌ Link không hợp lệ hoặc đã bị xóa.", parse_mode="HTML")
        return

    try:
        await bot.copy_message(
            chat_id      = message.chat.id,
            from_chat_id = media["storage_chat_id"],
            message_id   = media["storage_msg_id"],
        )
        await db.increment_access(token)
    except TelegramBadRequest as e:
        if "message to copy not found" in str(e).lower():
            await message.answer("⚠️ Media này đã bị xóa khỏi kho lưu trữ.")
        else:
            logger.exception("copy_message failed token=%s", token)
            await message.answer("⚠️ Không thể tải media. Vui lòng thử lại sau.")
    except Exception:
        logger.exception("Unexpected error serving token=%s", token)
        await message.answer("⚠️ Lỗi không xác định. Vui lòng thử lại.")


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN HANDLERS — Commands
# ══════════════════════════════════════════════════════════════════════════════

@admin_router.message(Command("admin"))
async def cmd_admin_panel(message: Message):
    if not await _is_admin(message.from_user.id):
        return
    await message.answer("🛠 <b>Admin Panel</b>", parse_mode="HTML", reply_markup=_admin_kb())


@admin_router.message(Command("stats"))
async def cmd_stats(message: Message):
    if not await _is_admin(message.from_user.id):
        return
    stats = await db.get_stats()
    text = (
        f"📊 <b>Thống kê hệ thống</b>\n\n"
        f"👥 Người dùng    : <b>{stats['total_users']}</b>\n"
        f"🗂  Media lưu trữ : <b>{stats['total_media']}</b>\n"
        f"📥 Lượt truy cập : <b>{stats['total_accesses']}</b>\n"
        f"🛡  Admins        : <b>{stats['total_admins']}</b>"
    )
    await message.answer(text, parse_mode="HTML")


@admin_router.message(Command("ban"))
async def cmd_ban(message: Message):
    if not await _is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("❌ Dùng: <code>/ban USER_ID [lý do]</code>", parse_mode="HTML")
        return
    uid    = int(parts[1])
    reason = parts[2] if len(parts) > 2 else ""
    await db.ban_user(uid, reason)
    await message.answer(f"🚫 Đã ban user <code>{uid}</code>.", parse_mode="HTML")


@admin_router.message(Command("unban"))
async def cmd_unban(message: Message):
    if not await _is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("❌ Dùng: <code>/unban USER_ID</code>", parse_mode="HTML")
        return
    uid = int(parts[1])
    await db.unban_user(uid)
    await message.answer(f"✅ Đã unban user <code>{uid}</code>.", parse_mode="HTML")


@admin_router.message(Command("addadmin"))
async def cmd_addadmin(message: Message):
    if not await _is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("❌ Dùng: <code>/addadmin USER_ID</code>", parse_mode="HTML")
        return
    uid = int(parts[1])
    ok  = await db.add_admin(uid, added_by=message.from_user.id)
    if ok:
        await message.answer(f"✅ Đã thêm admin <code>{uid}</code>.", parse_mode="HTML")
    else:
        await message.answer(f"ℹ️ <code>{uid}</code> đã là admin.", parse_mode="HTML")


@admin_router.message(Command("removeadmin"))
async def cmd_removeadmin(message: Message):
    if not await _is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("❌ Dùng: <code>/removeadmin USER_ID</code>", parse_mode="HTML")
        return
    uid = int(parts[1])
    ok  = await db.remove_admin(uid)
    if ok:
        await message.answer(f"✅ Đã xóa admin <code>{uid}</code>.", parse_mode="HTML")
    else:
        await message.answer(f"❌ Không tìm thấy admin <code>{uid}</code>.", parse_mode="HTML")


@admin_router.message(Command("users"))
async def cmd_users(message: Message):
    if not await _is_admin(message.from_user.id):
        return
    users = await db.get_recent_users(20)
    if not users:
        await message.answer("Chưa có người dùng nào.")
        return
    lines = [f"👥 <b>20 người dùng gần nhất:</b>\n"]
    for u in users:
        last = datetime.datetime.fromtimestamp(u["last_active"]).strftime("%d/%m %H:%M") if u.get("last_active") else "?"
        lines.append(f"• {_fmt_user(u)} — {last}")
    await message.answer("\n".join(lines), parse_mode="HTML")


@admin_router.message(Command("delete"))
async def cmd_delete_media(message: Message):
    if not await _is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("❌ Dùng: <code>/delete TOKEN</code>", parse_mode="HTML")
        return
    token = parts[1]
    ok = await db.delete_media(token)
    if ok:
        await message.answer(f"✅ Đã xóa media <code>{token}</code>.", parse_mode="HTML")
    else:
        await message.answer(f"❌ Token <code>{token}</code> không tồn tại.", parse_mode="HTML")


# ── Broadcast FSM ──────────────────────────────────────────────────────────────

@admin_router.message(Command("broadcast"))
async def cmd_broadcast_start(message: Message, state: FSMContext):
    if not await _is_admin(message.from_user.id):
        return
    await state.set_state(BroadcastStates.waiting_message)
    await message.answer(
        "📢 <b>Broadcast</b>\n\nGửi tin nhắn muốn phát đến TẤT CẢ người dùng.\n"
        "Hỗ trợ text, ảnh, video, file...\n\n<i>/cancel để hủy</i>",
        parse_mode="HTML",
    )


@admin_router.message(Command("cancel"), BroadcastStates.waiting_message)
@admin_router.message(Command("cancel"), BroadcastStates.confirming)
@admin_router.message(Command("cancel"), PushStates.waiting_token)
@admin_router.message(Command("cancel"), PushStates.waiting_chat_id)
@admin_router.message(Command("cancel"), PushStates.waiting_mode)
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Đã hủy.")


@admin_router.message(BroadcastStates.waiting_message)
async def broadcast_got_message(message: Message, state: FSMContext, bot: Bot):
    await state.update_data(msg_id=message.message_id, chat_id=message.chat.id)
    count = await db.count_users()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Xác nhận gửi", callback_data="bc:confirm"),
            InlineKeyboardButton(text="❌ Hủy",          callback_data="bc:cancel"),
        ],
    ])
    await message.answer(
        f"⚠️ Sẽ gửi tin nhắn này tới <b>{count}</b> người dùng.\nXác nhận?",
        parse_mode="HTML", reply_markup=kb,
    )
    await state.set_state(BroadcastStates.confirming)


@admin_router.callback_query(F.data == "bc:cancel", BroadcastStates.confirming)
async def broadcast_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Đã hủy broadcast.")
    await callback.answer()


@admin_router.callback_query(F.data == "bc:confirm", BroadcastStates.confirming)
async def broadcast_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.message.edit_text("📤 Đang gửi…")
    await callback.answer()

    data = await state.get_data()
    await state.clear()

    src_chat = data["chat_id"]
    src_msg  = data["msg_id"]
    user_ids = await db.get_all_user_ids()

    sent = failed = 0
    for uid in user_ids:
        try:
            await bot.copy_message(chat_id=uid, from_chat_id=src_chat, message_id=src_msg)
            sent += 1
        except TelegramForbiddenError:
            failed += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)  # ~20 msg/s to stay within rate limits

    await db.log_broadcast(
        sent_by=callback.from_user.id,
        message_text="(media)" if True else "",
        total_sent=sent,
        total_failed=failed,
    )

    await callback.message.answer(
        f"✅ Broadcast hoàn thành!\n"
        f"   Đã gửi  : <b>{sent}</b>\n"
        f"   Thất bại: <b>{failed}</b>",
        parse_mode="HTML",
    )


# ── Push Media FSM ─────────────────────────────────────────────────────────────

@admin_router.message(Command("push"))
async def cmd_push_start(message: Message, state: FSMContext):
    if not await _is_admin(message.from_user.id):
        return
    # Quick form: /push TOKEN CHAT_ID [copy|forward]
    parts = message.text.split()
    if len(parts) >= 3:
        token   = parts[1]
        chat_id = parts[2]
        mode    = parts[3] if len(parts) > 3 else "copy"
        await _do_push(message, token, chat_id, mode)
        return

    await state.set_state(PushStates.waiting_token)
    await message.answer(
        "📤 <b>Push Media</b>\n\nNhập TOKEN cần đẩy:\n<i>/cancel để hủy</i>",
        parse_mode="HTML",
    )


@admin_router.message(PushStates.waiting_token)
async def push_got_token(message: Message, state: FSMContext):
    await state.update_data(token=message.text.strip())
    await state.set_state(PushStates.waiting_chat_id)
    await message.answer("📨 Nhập <b>chat_id / username</b> đích:", parse_mode="HTML")


@admin_router.message(PushStates.waiting_chat_id)
async def push_got_chat(message: Message, state: FSMContext):
    await state.update_data(chat_id=message.text.strip())
    await state.set_state(PushStates.waiting_mode)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📋 Copy (ẩn tên)",      callback_data="push:copy"),
            InlineKeyboardButton(text="📤 Forward (hiện tên)", callback_data="push:forward"),
        ],
    ])
    await message.answer("Chọn chế độ gửi:", reply_markup=kb)


@admin_router.callback_query(F.data.startswith("push:"), PushStates.waiting_mode)
async def push_got_mode(callback: CallbackQuery, state: FSMContext, bot: Bot):
    mode = callback.data.split(":")[1]
    data = await state.get_data()
    await state.clear()
    await callback.message.edit_reply_markup()
    await _do_push(callback.message, data["token"], data["chat_id"], mode, bot=bot)
    await callback.answer()


async def _do_push(message: Message, token: str, chat_id: str, mode: str, bot: Bot = None):
    _bot = bot or message.bot
    media = await db.get_media(token)
    if not media:
        await message.answer(f"❌ Token <code>{token}</code> không tồn tại.", parse_mode="HTML")
        return
    try:
        target = int(chat_id) if chat_id.lstrip("-").isdigit() else chat_id
        if mode == "forward":
            await _bot.forward_message(
                chat_id      = target,
                from_chat_id = media["storage_chat_id"],
                message_id   = media["storage_msg_id"],
            )
        else:
            await _bot.copy_message(
                chat_id      = target,
                from_chat_id = media["storage_chat_id"],
                message_id   = media["storage_msg_id"],
            )
        await message.answer(
            f"✅ Đã push <code>{token}</code> → <code>{target}</code> ({mode}).",
            parse_mode="HTML",
        )
    except Exception as e:
        await message.answer(f"❌ Push thất bại: <code>{html.escape(str(e))}</code>", parse_mode="HTML")


# ── Forward forwarded message to storage ──────────────────────────────────────

@admin_router.message(F.forward_from | F.forward_from_chat | F.forward_sender_name)
async def admin_forward_handler(message: Message, bot: Bot):
    """
    When admin forwards any media message to the bot, store it and return a share link.
    This is the 'quick-store' feature: forward any content → get a share link.
    """
    if not await _is_admin(message.from_user.id):
        return
    if not message.content_type or message.content_type == "text":
        return

    # Copy this message to storage channel
    try:
        stored = await bot.copy_message(
            chat_id    = config.STORAGE_CHANNEL,
            from_chat_id = message.chat.id,
            message_id = message.message_id,
        )
        storage_msg_id = stored.message_id
    except Exception as e:
        await message.answer(f"❌ Không thể lưu vào storage: <code>{html.escape(str(e))}</code>", parse_mode="HTML")
        return

    media_type = message.content_type
    caption    = message.caption or message.text or ""
    file_name  = ""
    if message.document and message.document.file_name:
        file_name = message.document.file_name

    token = await db.save_media(
        storage_chat_id = config.STORAGE_CHANNEL,
        storage_msg_id  = storage_msg_id,
        media_type      = media_type,
        original_caption = caption,
        file_name        = file_name,
    )

    link = f"https://t.me/{config.BOT_USERNAME}?start={token}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Mở link", url=link)],
        [
            InlineKeyboardButton(text="📤 Push (copy)",    callback_data=f"qpush:copy:{token}"),
            InlineKeyboardButton(text="📨 Push (forward)", callback_data=f"qpush:fwd:{token}"),
        ],
    ])
    await message.answer(
        f"✅ Đã lưu! Token: <code>{token}</code>\n\n"
        f"🔗 <b>Link chia sẻ:</b>\n<code>{link}</code>",
        parse_mode="HTML",
        reply_markup=kb,
    )


@admin_router.callback_query(F.data.startswith("qpush:"))
async def quick_push_callback(callback: CallbackQuery, bot: Bot):
    if not await _is_admin(callback.from_user.id):
        await callback.answer("⛔ Không có quyền.", show_alert=True)
        return
    _, mode_short, token = callback.data.split(":", 2)
    mode = "forward" if mode_short == "fwd" else "copy"
    await callback.answer(f"Nhập chat_id vào chat với bot để push.", show_alert=True)
    # Store pending push in state — simple approach: ask user via separate message
    await callback.message.answer(
        f"📤 Nhập <b>chat_id / username</b> đích để push token <code>{token}</code> ({mode}):\n"
        f"<i>Dạng: /push {token} CHAT_ID {mode}</i>",
        parse_mode="HTML",
    )


# ── Admin panel callbacks ──────────────────────────────────────────────────────

@admin_router.callback_query(F.data == "admin:back")
async def cb_admin_back(callback: CallbackQuery):
    if not await _is_admin(callback.from_user.id):
        await callback.answer()
        return
    await callback.message.edit_text("🛠 <b>Admin Panel</b>", parse_mode="HTML", reply_markup=_admin_kb())
    await callback.answer()


@admin_router.callback_query(F.data == "admin:stats")
async def cb_admin_stats(callback: CallbackQuery):
    if not await _is_admin(callback.from_user.id):
        await callback.answer()
        return
    stats = await db.get_stats()
    text = (
        f"📊 <b>Thống kê hệ thống</b>\n\n"
        f"👥 Người dùng    : <b>{stats['total_users']}</b>\n"
        f"🗂  Media lưu trữ : <b>{stats['total_media']}</b>\n"
        f"📥 Lượt truy cập : <b>{stats['total_accesses']}</b>\n"
        f"🛡  Admins        : <b>{stats['total_admins']}</b>"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=_back_kb())
    await callback.answer()


@admin_router.callback_query(F.data == "admin:users")
async def cb_admin_users(callback: CallbackQuery):
    if not await _is_admin(callback.from_user.id):
        await callback.answer()
        return
    users = await db.get_recent_users(10)
    if not users:
        text = "Chưa có người dùng nào."
    else:
        lines = ["👥 <b>10 người dùng gần nhất:</b>\n"]
        for u in users:
            last = datetime.datetime.fromtimestamp(u["last_active"]).strftime("%d/%m %H:%M") if u.get("last_active") else "?"
            lines.append(f"• {_fmt_user(u)} — {last}")
        text = "\n".join(lines)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=_back_kb())
    await callback.answer()


@admin_router.callback_query(F.data == "admin:broadcast")
async def cb_admin_broadcast(callback: CallbackQuery, state: FSMContext):
    if not await _is_admin(callback.from_user.id):
        await callback.answer()
        return
    await state.set_state(BroadcastStates.waiting_message)
    count = await db.count_users()
    await callback.message.edit_text(
        f"📢 <b>Broadcast</b> — {count} người dùng\n\nGửi tin nhắn muốn phát:",
        parse_mode="HTML",
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admin:push")
async def cb_admin_push(callback: CallbackQuery, state: FSMContext):
    if not await _is_admin(callback.from_user.id):
        await callback.answer()
        return
    await state.set_state(PushStates.waiting_token)
    await callback.message.edit_text(
        "📤 <b>Push Media</b>\n\nNhập TOKEN:",
        parse_mode="HTML",
    )
    await callback.answer()


@admin_router.callback_query(F.data == "admin:bc_history")
async def cb_bc_history(callback: CallbackQuery):
    if not await _is_admin(callback.from_user.id):
        await callback.answer()
        return
    history = await db.get_broadcast_history(5)
    if not history:
        text = "Chưa có lịch sử broadcast."
    else:
        lines = ["📜 <b>Lịch sử broadcast (5 gần nhất):</b>\n"]
        for h in history:
            ts  = datetime.datetime.fromtimestamp(h["sent_at"]).strftime("%d/%m %H:%M") if h.get("sent_at") else "?"
            lines.append(
                f"• {ts} — ✅{h['total_sent']} ❌{h['total_failed']}"
                f" (by <code>{h['sent_by']}</code>)"
            )
        text = "\n".join(lines)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=_back_kb())
    await callback.answer()


# ══════════════════════════════════════════════════════════════════════════════
# BOT COMMANDS MENU
# ══════════════════════════════════════════════════════════════════════════════

async def set_bot_commands(bot: Bot):
    await bot.set_my_commands([
        BotCommand(command="start",        description="Xem media từ link chia sẻ"),
        BotCommand(command="admin",        description="Mở admin panel"),
        BotCommand(command="stats",        description="Thống kê hệ thống"),
        BotCommand(command="broadcast",    description="Gửi tin tới tất cả users"),
        BotCommand(command="push",         description="Push media: /push TOKEN CHAT_ID [copy|forward]"),
        BotCommand(command="ban",          description="Cấm user: /ban USER_ID [lý do]"),
        BotCommand(command="unban",        description="Bỏ cấm: /unban USER_ID"),
        BotCommand(command="addadmin",     description="Thêm admin: /addadmin USER_ID"),
        BotCommand(command="removeadmin",  description="Xóa admin: /removeadmin USER_ID"),
        BotCommand(command="users",        description="Xem danh sách users gần đây"),
        BotCommand(command="delete",       description="Xóa media: /delete TOKEN"),
        BotCommand(command="cancel",       description="Hủy thao tác hiện tại"),
    ])


# ══════════════════════════════════════════════════════════════════════════════
# STARTUP
# ══════════════════════════════════════════════════════════════════════════════

async def run_bot():
    """Entry point — run the aiogram bot."""
    if not config.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set. Please configure .env")
    if not config.BOT_USERNAME:
        raise RuntimeError("BOT_USERNAME is not set. Please configure .env")
    if not config.STORAGE_CHANNEL:
        raise RuntimeError("STORAGE_CHANNEL is not set. Please configure .env")

    await db.connect()

    bot = Bot(token=config.BOT_TOKEN, parse_mode=None)
    dp  = Dispatcher(storage=MemoryStorage())

    # Register middleware
    dp.message.middleware(UserTrackingMiddleware())
    dp.callback_query.middleware(UserTrackingMiddleware())

    # Register routers (admin first so its handlers take priority)
    dp.include_router(admin_router)
    dp.include_router(user_router)

    await set_bot_commands(bot)

    me = await bot.get_me()
    print(f"✅ Bot @{me.username} started  (storage={config.STORAGE_CHANNEL})")

    try:
        await dp.start_polling(bot, skip_updates=True)
    finally:
        await db.close()
        await bot.session.close()
