"""Forum backup / clone engine (Userbot)."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.functions.messages import (
    CreateForumTopicRequest,
    ForwardMessagesRequest,
    GetForumTopicsRequest,
)
from telethon.tl.types import Channel, Message

from bot import media_db
from shared.logger import log_buffer
from shared.progress import load_last_id, load_topic_map, make_key, save_last_id, save_session_meta, save_topic_map
from userbot.telegram_client import get_client

PERMANENT_ERRORS = {
    "MessageIdInvalidError",
    "MessageEmptyError",
    "ChatForwardsRestrictedError",
    "MediaEmptyError",
}


class PermanentForwardError(Exception):
    def __init__(self, orig):
        self.orig = orig
        super().__init__(str(orig))


def is_permanent_error(exc: Exception) -> bool:
    return type(exc).__name__ in PERMANENT_ERRORS


@dataclass
class CloneProgress:
    running: bool = False
    phase: str = "idle"
    topics_done: int = 0
    topics_total: int = 0
    current_topic: str = ""
    msgs_ok: int = 0
    msgs_fail: int = 0
    msgs_skip: int = 0
    flood_waits: int = 0
    current_topic_msgs: int = 0
    current_topic_total: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "phase": self.phase,
            "topics_done": self.topics_done,
            "topics_total": self.topics_total,
            "current_topic": self.current_topic,
            "msgs_ok": self.msgs_ok,
            "msgs_fail": self.msgs_fail,
            "msgs_skip": self.msgs_skip,
            "flood_waits": self.flood_waits,
            "current_topic_msgs": self.current_topic_msgs,
            "current_topic_total": self.current_topic_total,
            "error": self.error,
        }


class CloneJob:
    def __init__(self):
        self.progress = CloneProgress()
        self._task: Optional[asyncio.Task] = None
        self._stop = False

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def request_stop(self) -> None:
        self._stop = True
        log_buffer.warn("CLONE", "Yêu cầu dừng — sẽ dừng sau batch hiện tại")

    async def start(self, cfg: dict[str, Any]) -> None:
        if self.is_running():
            raise RuntimeError("Clone đang chạy")
        self._stop = False
        self.progress = CloneProgress(running=True, phase="starting")
        self._task = asyncio.create_task(self._run(cfg))

    async def _run(self, cfg: dict[str, Any]) -> None:
        try:
            await run_forum_backup(cfg, self.progress, lambda: self._stop)
        except Exception as e:
            self.progress.error = str(e)
            log_buffer.err("CLONE", str(e))
        finally:
            self.progress.running = False
            self.progress.phase = "done" if not self.progress.error else "error"


clone_job = CloneJob()


async def resolve_entity(client: TelegramClient, raw: str):
    raw = raw.strip()
    try:
        return await client.get_entity(int(raw))
    except ValueError:
        return await client.get_entity(raw)


def is_forum(entity) -> bool:
    return isinstance(entity, Channel) and getattr(entity, "forum", False)


async def preload_topics_full(client, entity) -> dict[int, dict]:
    topics = {}
    offset_topic = 0
    while True:
        r = await client(
            GetForumTopicsRequest(
                channel=entity,
                q="",
                offset_date=0,
                offset_id=0,
                offset_topic=offset_topic,
                limit=100,
            )
        )
        if not r.topics:
            break
        for t in r.topics:
            topics[t.id] = {
                "title": getattr(t, "title", f"Topic {t.id}"),
                "icon_color": getattr(t, "icon_color", None),
                "icon_emoji_id": getattr(t, "icon_emoji_id", None),
            }
        if len(r.topics) < 100:
            break
        offset_topic = r.topics[-1].id
    return topics


async def send_msgs(
    client,
    msgs: list[Message],
    dst_entity,
    hide_sender: bool,
    dst_topic: Optional[int],
    src_peer,
    dst_peer,
    *,
    video_thumbnail_only: bool = False,
):
    if not msgs:
        return

    # Video → thumbnail only: send photo thumb instead of forward
    if video_thumbnail_only and len(msgs) == 1:
        msg = msgs[0]
        if msg.video and msg.document:
            thumb = None
            if msg.document.thumbs:
                thumb = msg.document.thumbs[-1]
            if thumb:
                try:
                    path = await client.download_media(msg, thumb=thumb)
                    if path:
                        kw = {}
                        if dst_topic and dst_topic != 1:
                            kw["reply_to"] = dst_topic
                        await client.send_file(
                            dst_entity,
                            path,
                            caption=msg.message or "",
                            formatting_entities=msg.entities,
                            **kw,
                        )
                        try:
                            os.remove(path)
                        except OSError:
                            pass
                        return
                except Exception as e:
                    log_buffer.warn("CLONE", f"Thumbnail fallback forward: {e}")

    kw = {}
    if dst_topic and dst_topic != 1:
        kw["top_msg_id"] = dst_topic

    for attempt in range(4):
        try:
            await client(
                ForwardMessagesRequest(
                    from_peer=src_peer,
                    to_peer=dst_peer,
                    id=[m.id for m in msgs],
                    random_id=[
                        int.from_bytes(os.urandom(8), "big") & 0x7FFFFFFFFFFFFFFF
                        for _ in msgs
                    ],
                    drop_author=bool(hide_sender),
                    **kw,
                )
            )
            return
        except FloodWaitError as e:
            log_buffer.warn("CLONE", f"FloodWait {e.seconds}s")
            await asyncio.sleep(e.seconds + 2)
        except Exception as e:
            if is_permanent_error(e):
                raise PermanentForwardError(e) from e
            if attempt == 3:
                raise
            await asyncio.sleep(2 * (attempt + 1))


async def clone_all_topics(
    client,
    dst_entity,
    map_key: str,
    src_topics: dict,
    dst_by_title: dict,
    icon_mode: str,
    custom_emoji_id: Optional[int],
    skip_general: bool,
) -> dict[int, int]:
    topic_map = load_topic_map(map_key)
    for src_id, info in sorted(src_topics.items()):
        title = info["title"]
        if src_id == 1:
            if skip_general:
                continue
            topic_map[1] = 1
            save_topic_map(map_key, topic_map)
            continue
        if src_id in topic_map:
            continue
        if title in dst_by_title:
            topic_map[src_id] = dst_by_title[title]
            save_topic_map(map_key, topic_map)
            continue

        icon_color = info.get("icon_color")
        icon_emoji_id = None
        if icon_mode == "clone":
            icon_emoji_id = info.get("icon_emoji_id")
        elif icon_mode == "fixed" and custom_emoji_id:
            icon_emoji_id = custom_emoji_id

        req_kw = {
            "channel": dst_entity,
            "title": title,
            "random_id": int.from_bytes(os.urandom(8), "little") & 0x7FFFFFFFFFFFFFFF,
        }
        if icon_color is not None:
            req_kw["icon_color"] = icon_color
        if icon_emoji_id:
            req_kw["icon_emoji_id"] = icon_emoji_id

        cr = await client(CreateForumTopicRequest(**req_kw))
        new_id = None
        for u in cr.updates:
            if hasattr(u, "message") and hasattr(u.message, "action"):
                new_id = u.message.id
                break
        if new_id:
            topic_map[src_id] = new_id
            dst_by_title[title] = new_id
            save_topic_map(map_key, topic_map)
            log_buffer.ok("PHASE1", f"Tạo topic '{title}' → {new_id}")
        await asyncio.sleep(1)
    return topic_map


async def forward_topic_messages(
    client,
    src_entity,
    dst_entity,
    src_peer,
    dst_peer,
    src_topic_id: int,
    dst_topic_id: int,
    map_key: str,
    cfg: dict,
    progress: CloneProgress,
    should_stop: Callable[[], bool],
) -> tuple[int, int]:
    hide_sender = cfg.get("hide_sender", True)
    only_filter = cfg.get("only_filter", "all")
    video_thumb = cfg.get("video_thumbnail_only", True)
    batch_size = int(cfg.get("batch_size", 50))

    topic_key = f"{map_key}_topic_{src_topic_id}"
    last_id = load_last_id(map_key, src_topic_id) or 0
    count = skipped = 0
    last_buf = last_id
    batch: list[Message] = []
    pending_album: dict = {"gid": None, "msgs": []}

    async def flush_batch():
        nonlocal count, last_buf
        if not batch:
            return
        n = len(batch)
        try:
            await send_msgs(
                client,
                batch,
                dst_entity,
                hide_sender,
                dst_topic_id,
                src_peer,
                dst_peer,
                video_thumbnail_only=video_thumb,
            )
            count += n
            progress.msgs_ok += n
            last_buf = batch[-1].id
            save_last_id(map_key, last_buf, src_topic_id)
            batch.clear()
        except PermanentForwardError:
            for m in batch:
                try:
                    await send_msgs(
                        client,
                        [m],
                        dst_entity,
                        hide_sender,
                        dst_topic_id,
                        src_peer,
                        dst_peer,
                        video_thumbnail_only=video_thumb,
                    )
                    count += 1
                    progress.msgs_ok += 1
                except PermanentForwardError:
                    skipped += 1
                    progress.msgs_skip += 1
                except Exception:
                    progress.msgs_fail += 1
                last_buf = m.id
                save_last_id(map_key, last_buf, src_topic_id)
            batch.clear()

    async def flush_album():
        nonlocal count, skipped, last_buf
        if not pending_album["msgs"]:
            return
        album = list(pending_album["msgs"])
        pending_album["gid"] = None
        pending_album["msgs"].clear()
        try:
            await send_msgs(
                client,
                album,
                dst_entity,
                hide_sender,
                dst_topic_id,
                src_peer,
                dst_peer,
                video_thumbnail_only=video_thumb,
            )
            count += len(album)
            progress.msgs_ok += len(album)
            last_buf = album[-1].id
            save_last_id(map_key, last_buf, src_topic_id)
        except PermanentForwardError:
            skipped += len(album)
            progress.msgs_skip += len(album)
            last_buf = album[-1].id
            save_last_id(map_key, last_buf, src_topic_id)

    if src_topic_id == 1:
        iterator = client.iter_messages(src_entity, min_id=last_id, reverse=True)
        filter_general = True
    else:
        iterator = client.iter_messages(
            src_entity, reply_to=src_topic_id, min_id=last_id, reverse=True
        )
        filter_general = False

    async for msg in iterator:
        if should_stop():
            break
        if not isinstance(msg, Message) or getattr(msg, "action", None):
            continue
        if filter_general:
            rt = getattr(msg, "reply_to", None)
            top = getattr(rt, "reply_to_top_id", None) or getattr(rt, "reply_to_msg_id", None)
            if top is not None and top != 1:
                continue

        if msg.grouped_id:
            if pending_album["gid"] == msg.grouped_id:
                pending_album["msgs"].append(msg)
            else:
                await flush_album()
                await flush_batch()
                pending_album["gid"] = msg.grouped_id
                pending_album["msgs"] = [msg]
            continue

        if pending_album["msgs"]:
            await flush_album()

        if only_filter == "media" and not msg.media:
            continue
        if only_filter == "video" and not msg.video:
            continue
        if only_filter == "photo" and not msg.photo:
            continue

        batch.append(msg)
        progress.current_topic_msgs = count + len(batch)
        if len(batch) >= batch_size:
            await flush_batch()

    await flush_album()
    await flush_batch()
    return count, skipped


async def run_forum_backup(
    cfg: dict[str, Any],
    progress: CloneProgress,
    should_stop: Callable[[], bool],
) -> None:
    client = await get_client()
    media_db.init_db()

    src_raw = cfg.get("src_raw", "")
    dst_raw = cfg.get("dst_raw", "")
    if not src_raw or not dst_raw:
        raise ValueError("Thiếu src_raw hoặc dst_raw")

    src_entity = await resolve_entity(client, src_raw)
    dst_entity = await resolve_entity(client, dst_raw)

    if not is_forum(src_entity) or not is_forum(dst_entity):
        raise ValueError("Cả nguồn và đích phải là Forum (bật Topics)")

    src_peer = await client.get_input_entity(src_entity)
    dst_peer = await client.get_input_entity(dst_entity)
    map_key = make_key(src_entity.id, dst_entity.id)

    src_name = getattr(src_entity, "title", str(src_entity.id))
    dst_name = getattr(dst_entity, "title", str(dst_entity.id))
    log_buffer.info("CLONE", f"SOURCE: {src_name} → DEST: {dst_name}")

    save_session_meta(
        map_key,
        {
            "key": map_key,
            "src_name": src_name,
            "dst_name": dst_name,
            "mode": "backup",
            "cfg": cfg,
        },
    )

    progress.phase = "phase1"
    src_topics = await preload_topics_full(client, src_entity)
    dst_topics = await preload_topics_full(client, dst_entity)
    dst_by_title = {info["title"]: tid for tid, info in dst_topics.items()}

    topic_map = await clone_all_topics(
        client,
        dst_entity,
        map_key,
        src_topics,
        dst_by_title,
        cfg.get("icon_mode", "clone"),
        cfg.get("custom_emoji_id"),
        cfg.get("skip_general", False),
    )

    progress.phase = "phase2"
    progress.topics_total = len([t for t in src_topics if t in topic_map])
    progress.topics_done = 0

    for src_id in sorted(src_topics.keys()):
        if should_stop():
            break
        if src_id not in topic_map:
            continue
        title = src_topics[src_id]["title"]
        progress.current_topic = title
        log_buffer.info("TOPIC", f"[{progress.topics_done + 1}/{progress.topics_total}] {title}")

        await forward_topic_messages(
            client,
            src_entity,
            dst_entity,
            src_peer,
            dst_peer,
            src_id,
            topic_map[src_id],
            map_key,
            cfg,
            progress,
            should_stop,
        )
        progress.topics_done += 1

    progress.phase = "done"
    log_buffer.ok(
        "CLONE",
        f"Hoàn thành: {progress.msgs_ok} ok, {progress.msgs_fail} fail, {progress.msgs_skip} skip",
    )
