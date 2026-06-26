"""Shared Telethon client singleton."""

from __future__ import annotations

import asyncio
from typing import Optional

from telethon import TelegramClient

from shared.config import ROOT, env_override, load_settings
from shared.logger import log_buffer

_client: Optional[TelegramClient] = None
_lock = asyncio.Lock()


async def get_client() -> TelegramClient:
    global _client
    async with _lock:
        if _client and _client.is_connected():
            return _client

        cfg = env_override(load_settings())
        tg = cfg.get("telegram", {})
        api_id = int(tg.get("api_id") or 0)
        api_hash = tg.get("api_hash", "")
        session = tg.get("session_name", "session_v24")
        phone = tg.get("phone", "")

        if not api_id or not api_hash:
            raise RuntimeError("Chưa cấu hình API_ID / API_HASH")

        session_path = str(ROOT / session)
        _client = TelegramClient(session_path, api_id, api_hash)
        _client.flood_sleep_threshold = 60
        await _client.connect()

        if not await _client.is_user_authorized():
            if not phone:
                raise RuntimeError("Chưa đăng nhập — cần phone để xác thực lần đầu")
            await _client.start(phone=phone)

        me = await _client.get_me()
        log_buffer.ok("USERBOT", f"Đã kết nối: {me.first_name} (@{me.username or me.id})")
        return _client


async def disconnect_client() -> None:
    global _client
    if _client and _client.is_connected():
        await _client.disconnect()
    _client = None


def is_connected() -> bool:
    return _client is not None and _client.is_connected()


async def get_me_info() -> dict:
    client = await get_client()
    me = await client.get_me()
    return {
        "id": me.id,
        "first_name": me.first_name,
        "username": me.username,
        "phone": me.phone,
    }
