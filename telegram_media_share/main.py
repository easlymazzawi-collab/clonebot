"""
main.py — Entry point for the Telegram Media Share project.

Usage:
  python main.py          # interactive menu
  python main.py bot      # run only the share-link bot
  python main.py clone    # run only the forum cloner
  python main.py both     # run bot in background + cloner in foreground
"""
import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# Suppress noisy libraries
logging.getLogger("telethon").setLevel(logging.WARNING)
logging.getLogger("aiogram").setLevel(logging.WARNING)


def _print_banner():
    print("╔══════════════════════════════════════════════════════════╗")
    print("║   Telegram Forum → Share-Link System  v1.0              ║")
    print("║   📦 Cloner (Telethon) + 🤖 Share Bot (aiogram 3)       ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()


def _menu() -> str:
    print("  Chọn chế độ chạy:")
    print("    1. 🤖 Bot share link (nhận link → trả media)")
    print("    2. 🗂  Clone forum   (sao chép forum sang định dạng link)")
    print("    3. 🚀 Cả hai         (bot chạy nền + clone tương tác)")
    print("    4. ❌ Thoát")
    return input("\n  Lựa chọn [1-4, Enter=1]: ").strip() or "1"


async def _run_bot():
    from bot import run_bot
    await run_bot()


async def _run_clone():
    from cloner import run_cloner
    await run_cloner()


async def _run_both():
    """
    Start the bot as a background asyncio task, then run the cloner interactively.
    Both share the same event loop.
    """
    from bot import run_bot
    from cloner import run_cloner
    from database import db

    await db.connect()

    # Bot runs as a background task
    bot_task = asyncio.create_task(_run_bot_inner())
    print("  🤖 Bot đang chạy nền…\n")

    try:
        await run_cloner()
    finally:
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass
        await db.close()


async def _run_bot_inner():
    """Inner bot runner (without db connect/close since _run_both manages it)."""
    import config
    from aiogram import Bot, Dispatcher
    from aiogram.fsm.storage.memory import MemoryStorage
    from bot import admin_router, user_router, UserTrackingMiddleware, set_bot_commands

    if not config.BOT_TOKEN:
        print("  ⚠️  BOT_TOKEN chưa cấu hình — bot không chạy.")
        return

    bot = Bot(token=config.BOT_TOKEN, parse_mode=None)
    dp  = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(UserTrackingMiddleware())
    dp.callback_query.middleware(UserTrackingMiddleware())
    dp.include_router(admin_router)
    dp.include_router(user_router)
    await set_bot_commands(bot)
    try:
        await dp.start_polling(bot, skip_updates=True)
    finally:
        await bot.session.close()


async def main():
    _print_banner()

    mode = sys.argv[1].lower() if len(sys.argv) > 1 else None

    if mode == "bot":
        from database import db
        await db.connect()
        try:
            await _run_bot()
        finally:
            await db.close()
        return

    if mode == "clone":
        from database import db
        await db.connect()
        try:
            await _run_clone()
        finally:
            await db.close()
        return

    if mode == "both":
        await _run_both()
        return

    # Interactive menu
    choice = _menu()
    print()

    if choice == "1":
        from database import db
        await db.connect()
        try:
            await _run_bot()
        finally:
            await db.close()

    elif choice == "2":
        from database import db
        await db.connect()
        try:
            await _run_clone()
        finally:
            await db.close()

    elif choice == "3":
        await _run_both()

    elif choice == "4":
        print("👋 Tạm biệt!")
    else:
        print("❌ Lựa chọn không hợp lệ.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Tạm biệt!")
