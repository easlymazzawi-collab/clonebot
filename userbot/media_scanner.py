"""Scan source forum and harvest Bot API file_ids."""

from __future__ import annotations

import asyncio
import os
from typing import Any, Callable, Optional

from telethon.tl.functions.messages import ForwardMessagesRequest
from telethon.tl.types import Message

from bot import media_db
from shared.link_parser import msg_in_topic
from shared.logger import log_buffer
from userbot.telegram_client import get_client


async def _resolve_bot_peer(client, bot_username: str):
    username = bot_username.lstrip("@")
    return await client.get_input_entity(username)


async def harvest_album_to_bot(
    client,
    src_peer,
    bot_peer,
    msgs: list[Message],
    *,
    src_chat_id: int,
    topic_name: str = "",
    caption: str = "",
) -> str:
    """Forward album to bot with harvest tag; return token."""
    best = min(msgs, key=lambda m: m.id)
    token = media_db.new_token()
    media_db.save_album(
        token=token,
        src_chat_id=src_chat_id,
        src_msg_id=best.id,
        file_ids=[],
        topic_name=topic_name,
        media_type="album" if len(msgs) > 1 else "single",
        caption=caption,
    )

    # Tag message so bot handler can match (sent as separate tiny forward trick)
    await client.send_message(bot_peer, f"HARVEST:{token}")

    await client(
        ForwardMessagesRequest(
            from_peer=src_peer,
            to_peer=bot_peer,
            id=[m.id for m in msgs],
            random_id=[
                int.from_bytes(os.urandom(8), "big") & 0x7FFFFFFFFFFFFFFF for _ in msgs
            ],
            drop_author=True,
        )
    )
    return token


def build_bot_link(username: str, token: str) -> str:
    u = username.lstrip("@")
    return f"https://t.me/{u}?start={token}"


async def scan_topic_media(
    src_entity,
    src_topic_id: int,
    *,
    bot_username: str,
    topic_name: str = "",
    min_id: int = 0,
    on_progress: Optional[Callable[[dict], None]] = None,
) -> dict[str, Any]:
    """Iterate topic media, harvest file_ids via bot forward."""
    client = await get_client()
    src_peer = await client.get_input_entity(src_entity)
    bot_peer = await _resolve_bot_peer(client, bot_username)
    src_chat_id = src_entity.id

    stats = {"scanned": 0, "albums": 0, "tokens": []}
    pending_gid = None
    pending_msgs: list[Message] = []

    async def flush():
        nonlocal pending_gid, pending_msgs
        if not pending_msgs:
            return
        msgs = list(pending_msgs)
        pending_gid = None
        pending_msgs.clear()
        cap = next((m.message for m in msgs if m.message), "")
        token = await harvest_album_to_bot(
            client,
            src_peer,
            bot_peer,
            msgs,
            src_chat_id=src_chat_id,
            topic_name=topic_name,
            caption=cap,
        )
        stats["albums"] += 1
        stats["tokens"].append(token)
        log_buffer.ok("SCAN", f"Harvest {token} ({len(msgs)} file) topic={topic_name}")
        if on_progress:
            on_progress(stats)
        await asyncio.sleep(1.5)

    kwargs: dict = {"entity": src_entity, "min_id": min_id, "reverse": True}
    # For non-General topics, use reply_to filter to narrow results server-side
    if src_topic_id and src_topic_id != 1:
        kwargs["reply_to"] = src_topic_id

    async for msg in client.iter_messages(**kwargs):
        if not isinstance(msg, Message) or not msg.media:
            continue
        # Always pass the actual topic_id so General (id=1) is filtered correctly
        if not msg_in_topic(msg, src_topic_id):
            continue
        stats["scanned"] += 1

        if msg.grouped_id:
            if pending_gid == msg.grouped_id:
                pending_msgs.append(msg)
            else:
                await flush()
                pending_gid = msg.grouped_id
                pending_msgs = [msg]
        else:
            await flush()
            cap = msg.message or ""
            token = await harvest_album_to_bot(
                client,
                src_peer,
                bot_peer,
                [msg],
                src_chat_id=src_chat_id,
                topic_name=topic_name,
                caption=cap,
            )
            stats["albums"] += 1
            stats["tokens"].append(token)
            await asyncio.sleep(1.2)

    await flush()
    return stats
