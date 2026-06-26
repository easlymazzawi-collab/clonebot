"""Streaming caption editor with topic isolation (v3.1)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Optional

from telethon.errors import (
    ChatAdminRequiredError,
    FloodWaitError,
    MessageAuthorRequiredError,
    MessageIdInvalidError,
    MessageNotModifiedError,
)

from shared.link_parser import msg_in_topic, parse_message_link
from shared.logger import log_buffer
from shared.progress import load_caption_progress, save_caption_progress
from userbot.telegram_client import get_client


@dataclass
class CaptionProgress:
    running: bool = False
    ok: int = 0
    fail: int = 0
    skip: int = 0
    total: int = 0
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "running": self.running,
            "ok": self.ok,
            "fail": self.fail,
            "skip": self.skip,
            "total": self.total,
            "error": self.error,
        }


class CaptionJob:
    def __init__(self):
        self.progress = CaptionProgress()
        self._task: Optional[asyncio.Task] = None
        self._stop = False

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def request_stop(self) -> None:
        self._stop = True

    async def start(self, cfg: dict[str, Any]) -> None:
        if self.is_running():
            raise RuntimeError("Caption edit đang chạy")
        self._stop = False
        self.progress = CaptionProgress(running=True)
        self._task = asyncio.create_task(self._run(cfg))

    async def _run(self, cfg: dict[str, Any]) -> None:
        try:
            await stream_caption_edit(cfg, self.progress, lambda: self._stop)
        except Exception as e:
            self.progress.error = str(e)
            log_buffer.err("CAPTION", str(e))
        finally:
            self.progress.running = False


caption_job = CaptionJob()


async def edit_message_safe(client, entity, msg_id, new_text, new_entities):
    try:
        await client.edit_message(
            entity, msg_id, text=new_text, formatting_entities=new_entities
        )
        return True, "OK"
    except MessageNotModifiedError:
        return True, "skip"
    except MessageIdInvalidError:
        return False, "invalid"
    except (ChatAdminRequiredError, MessageAuthorRequiredError):
        return False, "no permission"
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        try:
            await client.edit_message(
                entity, msg_id, text=new_text, formatting_entities=new_entities
            )
            return True, "OK after flood"
        except Exception as e2:
            return False, str(e2)
    except Exception as e:
        return False, str(e)


def pick_better_album_msg(a, b):
    if a.message and not b.message:
        return a
    if b.message and not a.message:
        return b
    return a if a.id < b.id else b


def render_caption(template: str, *, caption_goc: str, bot_link: str, topic_name: str, msg_id: int) -> str:
    return (
        template.replace("{caption_goc}", caption_goc or "")
        .replace("{bot_link}", bot_link)
        .replace("{topic_name}", topic_name)
        .replace("{msg_id}", str(msg_id))
    )


async def stream_caption_edit(
    cfg: dict[str, Any],
    progress: CaptionProgress,
    should_stop: Callable[[], bool],
) -> None:
    client = await get_client()
    start_link = cfg["start_link"]
    end_link = cfg.get("end_link", "")
    new_text = cfg["new_text"]
    new_entities = cfg.get("new_entities")
    delay = float(cfg.get("delay", 1.2))
    bot_username = cfg.get("bot_username", "")
    embed_link = cfg.get("embed_bot_link", True)

    entity_id, start_msg_id, topic_id = parse_message_link(start_link)
    end_msg_id = None
    if end_link:
        _, end_msg_id, _ = parse_message_link(end_link)

    entity = await client.get_entity(entity_id)
    prog_data = load_caption_progress(entity_id, start_msg_id, end_msg_id, topic_id) or {}
    done_ids = set(prog_data.get("edited_msg_ids", []))
    failed_ids = dict(prog_data.get("failed_msg_ids", {}))

    kwargs = {
        "entity": entity,
        "min_id": max(0, start_msg_id - 11),
        "reverse": True,
    }
    if end_msg_id:
        kwargs["max_id"] = end_msg_id + 1
    if topic_id is not None:
        kwargs["reply_to"] = topic_id

    current = None
    counter = 0

    async def do_edit(target_msg, label: str):
        nonlocal counter
        if should_stop():
            return
        counter += 1
        progress.total = counter
        if target_msg.id in done_ids:
            progress.skip += 1
            return

        cap_goc = target_msg.message or ""
        bot_link = cfg.get("bot_link", "")
        if embed_link and bot_username and not bot_link:
            from bot import media_db
            from userbot.media_scanner import build_bot_link

            token = media_db.new_token()
            media_db.save_album(
                token=token,
                src_chat_id=entity.id,
                src_msg_id=target_msg.id,
                file_ids=[],
                caption=cap_goc,
            )
            bot_link = build_bot_link(bot_username, token)

        text = render_caption(
            new_text,
            caption_goc=cap_goc,
            bot_link=bot_link,
            topic_name=cfg.get("topic_name", ""),
            msg_id=target_msg.id,
        )

        ok, note = await edit_message_safe(client, entity, target_msg.id, text, new_entities)
        if ok:
            progress.ok += 1
            done_ids.add(target_msg.id)
            log_buffer.ok("CAPTION", f"msg {target_msg.id} ({label}) {note}")
        else:
            progress.fail += 1
            failed_ids[str(target_msg.id)] = note
            log_buffer.err("CAPTION", f"msg {target_msg.id}: {note}")

        prog_data.update(
            {
                "edited_msg_ids": sorted(done_ids),
                "failed_msg_ids": failed_ids,
                "caption_preview": text[:120],
            }
        )
        save_caption_progress(entity_id, start_msg_id, end_msg_id, topic_id, prog_data)
        await asyncio.sleep(delay)

    async def flush_current():
        nonlocal current
        if not current:
            return
        _, best_msg, max_id = current
        current = None
        if max_id < start_msg_id:
            return
        await do_edit(best_msg, "album")

    async for msg in client.iter_messages(**kwargs):
        if should_stop():
            break
        if topic_id is not None and not msg_in_topic(msg, topic_id):
            continue
        if not msg.media:
            continue

        if msg.grouped_id:
            gid = msg.grouped_id
            if current and current[0] == gid:
                current = (gid, pick_better_album_msg(current[1], msg), max(current[2], msg.id))
            else:
                await flush_current()
                current = (gid, msg, msg.id)
        else:
            await flush_current()
            if msg.id >= start_msg_id:
                await do_edit(msg, "single")

    await flush_current()
    log_buffer.ok("CAPTION", f"Xong: {progress.ok} ok, {progress.fail} fail, {progress.skip} skip")
