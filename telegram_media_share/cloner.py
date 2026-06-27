"""
cloner.py — Telethon-based forum cloner.

Workflow:
  Phase 1 — Clone topic structure (title, icon) from source forum to destination.
  Phase 2 — For each topic, iterate messages:
             · Video      → download thumbnail → send thumbnail + link caption
             · Photo      → send photo + link caption
             · Document / Audio / Voice / Animation → send file + link caption
             · Text only  → copy text with original formatting (no link)
             · Album      → each part processed individually

All media is first forwarded to a private STORAGE CHANNEL so the share-bot
can later serve it via copyMessage. A unique token is stored in the DB.
"""

import asyncio
import io
import json
import os
import re
import time
import datetime
from typing import Optional

from telethon import TelegramClient
from telethon.errors import FloodWaitError, ChatForwardsRestrictedError
from telethon.tl.types import (
    Message, Channel, MessageMediaEmpty, MessageMediaWebPage,
    MessageEntityCustomEmoji,
)
from telethon.tl.functions.messages import (
    CreateForumTopicRequest,
    GetForumTopicsRequest,
    ForwardMessagesRequest,
)

import config
from database import db


# ══════════════════════════════════════════════════════════════════════════════
# STATE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _state_path(key: str) -> str:
    return f"{config.STATE_FILE}_{key}.txt"

def _topicmap_path(key: str) -> str:
    return f"{config.TOPIC_MAP_FILE}_{key}.txt"

def _meta_path(key: str) -> str:
    return f"{config.SESSION_META_PREFIX}{key}.json"


def load_last_id(key: str) -> Optional[int]:
    p = _state_path(key)
    try:
        return int(open(p).read().strip()) if os.path.exists(p) else None
    except ValueError:
        return None


def save_last_id(key: str, mid: int):
    with open(_state_path(key), "w") as f:
        f.write(str(mid))


def load_topic_map(key: str) -> dict[int, int]:
    m: dict[int, int] = {}
    p = _topicmap_path(key)
    if os.path.exists(p):
        for line in open(p):
            if ":" in line:
                a, b = line.strip().split(":", 1)
                try:
                    m[int(a)] = int(b)
                except ValueError:
                    pass
    return m


def save_topic_map(key: str, mapping: dict[int, int]):
    with open(_topicmap_path(key), "w") as f:
        for a, b in mapping.items():
            f.write(f"{a}:{b}\n")


def save_session_meta(key, src_name, dst_name, cfg, progress=None):
    path = _meta_path(key)
    existing = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass
    data = {
        "key": key, "src_name": src_name, "dst_name": dst_name,
        "cfg": cfg,
        "created": existing.get("created", time.time()),
        "last_updated": time.time(),
    }
    if progress:
        data["progress"] = progress
    elif "progress" in existing:
        data["progress"] = existing["progress"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_all_sessions() -> list[dict]:
    sessions = []
    for fname in os.listdir("."):
        if fname.startswith(config.SESSION_META_PREFIX) and fname.endswith(".json"):
            try:
                with open(fname, "r", encoding="utf-8") as f:
                    sessions.append(json.load(f))
            except Exception:
                continue
    sessions.sort(key=lambda x: x.get("last_updated", 0), reverse=True)
    return sessions


def delete_session(key: str):
    patterns = [_meta_path(key), _state_path(key), _topicmap_path(key)]
    prefix = f"{config.STATE_FILE}_{key}_topic_"
    for fname in os.listdir("."):
        if fname.startswith(prefix) and fname.endswith(".txt"):
            patterns.append(fname)
    for p in patterns:
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception as e:
                print(f"  ⚠️  delete {p}: {e}")


def make_key(a: int, b: int) -> str:
    return f"{a}_{b}"


# ══════════════════════════════════════════════════════════════════════════════
# ENTITY / LINK HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def parse_link(raw: str) -> dict:
    raw = raw.strip()
    result = {"channel_raw": raw, "message_id": None, "topic_id": None}

    m = re.search(r't\.me/c/(\d+)/(\d+)/(\d+)', raw)
    if m:
        result.update(channel_raw=f"-100{m.group(1)}", topic_id=int(m.group(2)), message_id=int(m.group(3)))
        return result
    m = re.search(r't\.me/c/(\d+)/(\d+)', raw)
    if m:
        result.update(channel_raw=f"-100{m.group(1)}", message_id=int(m.group(2)))
        return result
    m = re.search(r't\.me/([^/]+)/(\d+)/(\d+)', raw)
    if m:
        result.update(channel_raw=m.group(1), topic_id=int(m.group(2)), message_id=int(m.group(3)))
        return result
    m = re.search(r't\.me/([^/]+)/(\d+)$', raw)
    if m:
        result.update(channel_raw=m.group(1), message_id=int(m.group(2)))
        return result
    return result


async def resolve_entity(client: TelegramClient, raw: str):
    raw = raw.strip()
    try:
        return await client.get_entity(int(raw))
    except ValueError:
        return await client.get_entity(raw)


def is_forum(entity) -> bool:
    return isinstance(entity, Channel) and getattr(entity, "forum", False)


# ══════════════════════════════════════════════════════════════════════════════
# TOPIC HELPERS
# ══════════════════════════════════════════════════════════════════════════════

async def preload_topics_full(client: TelegramClient, entity) -> dict[int, dict]:
    topics: dict[int, dict] = {}
    try:
        offset_topic = 0
        while True:
            r = await client(GetForumTopicsRequest(
                channel=entity, q="",
                offset_date=0, offset_id=0,
                offset_topic=offset_topic, limit=100,
            ))
            if not r.topics:
                break
            for t in r.topics:
                topics[t.id] = {
                    "title":         getattr(t, "title", f"Topic {t.id}"),
                    "icon_color":    getattr(t, "icon_color", None),
                    "icon_emoji_id": getattr(t, "icon_emoji_id", None),
                }
            if len(r.topics) < 100:
                break
            offset_topic = r.topics[-1].id
    except Exception as e:
        print(f"  ⚠️  preload_topics: {e}")
    return topics


async def clone_all_topics(
    client, dst_entity, map_key: str,
    src_topics: dict, dst_by_title: dict,
    icon_mode: str, custom_emoji_id: Optional[int],
    skip_general: bool,
) -> dict[int, int]:
    topic_map = load_topic_map(map_key)
    sorted_topics = sorted(src_topics.items())
    total = len(sorted_topics)
    created = skipped = mapped = 0

    print(f"\n  🧱 Phase 1: clone {total} topic(s)…")

    for i, (src_id, info) in enumerate(sorted_topics, 1):
        title = info["title"]

        if src_id == 1:
            if skip_general:
                print(f"  [{i}/{total}] ⏭️  General (id=1) — skip")
                skipped += 1
                continue
            topic_map[1] = 1
            mapped += 1
            save_topic_map(map_key, topic_map)
            print(f"  [{i}/{total}] 🏠 General (id=1) → map 1:1")
            continue

        if src_id in topic_map:
            print(f"  [{i}/{total}] ♻️  '{title}' → dst={topic_map[src_id]}")
            mapped += 1
            continue

        if title in dst_by_title:
            dst_id = dst_by_title[title]
            topic_map[src_id] = dst_id
            save_topic_map(map_key, topic_map)
            mapped += 1
            print(f"  [{i}/{total}] 🔗 '{title}' → existing dst={dst_id}")
            continue

        icon_color = info.get("icon_color")
        icon_emoji_id = None
        if icon_mode == "clone":
            icon_emoji_id = info.get("icon_emoji_id")
        elif icon_mode == "fixed" and custom_emoji_id:
            icon_emoji_id = custom_emoji_id

        new_id = None
        for attempt in range(config.MAX_RETRY):
            try:
                kw = {
                    "channel":   dst_entity,
                    "title":     title,
                    "random_id": int.from_bytes(os.urandom(8), "little") & 0x7FFFFFFFFFFFFFFF,
                }
                if icon_color is not None:
                    kw["icon_color"] = icon_color
                if icon_emoji_id:
                    kw["icon_emoji_id"] = icon_emoji_id

                cr = await client(CreateForumTopicRequest(**kw))
                for u in cr.updates:
                    if hasattr(u, "message") and hasattr(u.message, "action"):
                        new_id = u.message.id
                        break
                if new_id is None:
                    for u in cr.updates:
                        if hasattr(u, "id") and not hasattr(u, "message"):
                            new_id = u.id
                            break
                break
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds + config.FLOOD_SLEEP)
            except Exception as e:
                if attempt == config.MAX_RETRY - 1:
                    print(f"  [{i}/{total}] ⚠️  '{title}': {e}")
                else:
                    await asyncio.sleep(2 * (attempt + 1))

        if new_id:
            topic_map[src_id] = new_id
            dst_by_title[title] = new_id
            save_topic_map(map_key, topic_map)
            created += 1
            print(f"  [{i}/{total}] ✅ '{title}' → new dst={new_id}")

        await asyncio.sleep(1)

    print(f"\n  Phase 1 done | new={created} matched={mapped} skip={skipped}")
    return topic_map


# ══════════════════════════════════════════════════════════════════════════════
# MEDIA TYPE DETECTION & THUMBNAIL
# ══════════════════════════════════════════════════════════════════════════════

def detect_media_type(msg: Message) -> str:
    if msg.video:
        return "video"
    if msg.photo:
        return "photo"
    if msg.audio:
        return "audio"
    if msg.voice:
        return "voice"
    if msg.sticker:
        return "sticker"
    if msg.gif:
        return "animation"
    if msg.document:
        return "document"
    return "text"


async def download_thumbnail(client: TelegramClient, msg: Message) -> Optional[bytes]:
    """Download thumbnail bytes from a video or document with thumbs."""
    try:
        doc = getattr(msg, "document", None) or getattr(msg, "video", None)
        if doc and hasattr(doc, "thumbs") and doc.thumbs:
            buf = io.BytesIO()
            result = await client.download_media(doc.thumbs[-1], file=buf)
            if result:
                buf.seek(0)
                return buf.read()
        # Fallback: ask Telethon to download the thumbnail with thumb=-1
        buf = io.BytesIO()
        result = await client.download_media(msg, file=buf, thumb=-1)
        if result:
            buf.seek(0)
            data = buf.read()
            return data if data else None
    except Exception as e:
        print(f"  ⚠️  thumb download: {e}")
    return None


# ══════════════════════════════════════════════════════════════════════════════
# LINK CAPTION BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def build_link_caption(token: str, original_caption: str, bot_username: str) -> str:
    link = config.LINK_TEMPLATE.format(bot_username=bot_username, token=token)
    if original_caption and original_caption.strip():
        return original_caption.strip() + config.CAPTION_APPEND.format(link=link)
    return config.CAPTION_TEMPLATE.format(link=link)


# ══════════════════════════════════════════════════════════════════════════════
# CORE: PROCESS & SEND ONE MESSAGE
# ══════════════════════════════════════════════════════════════════════════════

async def process_and_clone_message(
    client: TelegramClient,
    msg: Message,
    src_entity,
    dst_entity,
    dst_topic_id: Optional[int],
    storage_entity,
    storage_entity_id: int,
    bot_username: str,
    hide_sender: bool,
) -> bool:
    """
    Convert one message to share-link format and send to destination.
    Returns True on success, False on skip/error.
    """
    has_real_media = (
        msg.media is not None
        and not isinstance(msg.media, (MessageMediaEmpty, MessageMediaWebPage))
    )

    # ── Text only ──────────────────────────────────────────────────────────────
    if not has_real_media:
        text = msg.text or msg.message
        if text:
            await client.send_message(
                dst_entity, text,
                formatting_entities=msg.entities,
                reply_to=dst_topic_id,
                parse_mode=None,
            )
        return True

    # ── Store original in storage channel ─────────────────────────────────────
    storage_msg_id: Optional[int] = None
    for attempt in range(config.MAX_RETRY):
        try:
            kw = {"top_msg_id": dst_topic_id} if dst_topic_id and dst_topic_id != 1 else {}
            stored = await client(ForwardMessagesRequest(
                from_peer   = src_entity,
                to_peer     = storage_entity,
                id          = [msg.id],
                random_id   = [int.from_bytes(os.urandom(8), "big") & 0x7FFFFFFFFFFFFFFF],
                drop_author = bool(hide_sender),
                noforwards  = False,
            ))
            for upd in stored.updates:
                if hasattr(upd, "message"):
                    storage_msg_id = upd.message.id
                    break
                if hasattr(upd, "id"):
                    storage_msg_id = upd.id
                    break
            break
        except ChatForwardsRestrictedError:
            # Channel has noforwards — cannot store, skip this message
            print(f"  ⚠️  msg {msg.id}: forward restricted, skip")
            return False
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + config.FLOOD_SLEEP)
        except Exception as e:
            if attempt == config.MAX_RETRY - 1:
                print(f"  ⚠️  storage forward failed id={msg.id}: {e}")
                return False
            await asyncio.sleep(2 * (attempt + 1))

    if not storage_msg_id:
        return False

    # ── Generate token and persist ─────────────────────────────────────────────
    media_type = detect_media_type(msg)
    original_caption = msg.text or msg.message or ""
    file_name = ""
    if msg.document and hasattr(msg.document, "attributes"):
        for attr in msg.document.attributes:
            if hasattr(attr, "file_name"):
                file_name = attr.file_name or ""
                break

    token = await db.save_media(
        storage_chat_id=storage_entity_id,
        storage_msg_id=storage_msg_id,
        media_type=media_type,
        original_caption=original_caption,
        file_name=file_name,
    )

    caption = build_link_caption(token, original_caption, bot_username)
    reply_kw = {"reply_to": dst_topic_id} if dst_topic_id else {}

    # ── Send to destination in link format ────────────────────────────────────
    try:
        if msg.video:
            thumb_bytes = await download_thumbnail(client, msg)
            if thumb_bytes:
                buf = io.BytesIO(thumb_bytes)
                buf.name = "thumbnail.jpg"
                await client.send_file(dst_entity, buf, caption=caption, **reply_kw)
            else:
                # No thumbnail — send text link only
                media_info = f"🎬 <b>Video</b>"
                if file_name:
                    media_info += f"\n📄 {file_name}"
                await client.send_message(dst_entity, f"{media_info}\n\n{caption}",
                                          parse_mode="html", **reply_kw)
        elif msg.photo:
            await client.send_file(dst_entity, msg.media, caption=caption, **reply_kw)
        elif msg.audio:
            await client.send_file(dst_entity, msg.media, caption=caption,
                                   voice_note=False, **reply_kw)
        elif msg.voice:
            await client.send_file(dst_entity, msg.media, caption=caption,
                                   voice_note=True, **reply_kw)
        elif msg.sticker:
            # Stickers: just post the sticker + caption as separate message
            await client.send_file(dst_entity, msg.media, **reply_kw)
            await client.send_message(dst_entity, caption, **reply_kw)
        elif msg.gif:
            await client.send_file(dst_entity, msg.media, caption=caption, **reply_kw)
        else:
            # Generic document
            await client.send_file(dst_entity, msg.media, caption=caption,
                                   force_document=True, **reply_kw)
        return True
    except Exception as e:
        print(f"  ⚠️  dst send failed id={msg.id}: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 2: FORWARD MESSAGES TOPIC-BY-TOPIC
# ══════════════════════════════════════════════════════════════════════════════

async def clone_topic_messages(
    client, src_entity, dst_entity,
    src_topic_id: int, dst_topic_id: int,
    storage_entity, storage_entity_id: int,
    bot_username: str,
    cfg: dict, map_key: str,
    stats: dict,
):
    """Iterate and clone all messages from one topic."""
    hide_sender = cfg["hide_sender"]
    only_filter = cfg["only_filter"]

    topic_key = f"{map_key}_topic_{src_topic_id}"
    last_id   = load_last_id(topic_key)
    min_id    = last_id if last_id else 0

    count = skipped = 0
    last_id_buf = last_id or 0
    pending_album: dict = {"gid": None, "msgs": []}

    SCAN_LOG = 200
    SCAN_SAVE = 500
    scan_n = 0

    if src_topic_id == 1:
        iterator = client.iter_messages(src_entity, min_id=min_id, reverse=True)
        filter_general = True
    else:
        iterator = client.iter_messages(src_entity, reply_to=src_topic_id,
                                        min_id=min_id, reverse=True)
        filter_general = False

    async def flush_album():
        nonlocal count, skipped
        if not pending_album["msgs"]:
            return
        # Forward album members individually (they still share grouped_id in storage)
        for m in pending_album["msgs"]:
            ok = await process_and_clone_message(
                client, m, src_entity, dst_entity, dst_topic_id,
                storage_entity, storage_entity_id, bot_username, hide_sender,
            )
            if ok:
                count += 1
            else:
                skipped += 1
        pending_album["gid"] = None
        pending_album["msgs"].clear()

    async for msg in iterator:
        scan_n += 1
        if scan_n % SCAN_LOG == 0:
            print(f"    🔍 scan={scan_n} ok={count} skip={skipped} id~{getattr(msg,'id','?')}   ", end="\r")

        if not isinstance(msg, Message):
            if not (pending_album["msgs"]):
                last_id_buf = getattr(msg, "id", last_id_buf)
                if scan_n % SCAN_SAVE == 0 and last_id_buf:
                    save_last_id(topic_key, last_id_buf)
            continue

        if getattr(msg, "action", None) is not None:
            skipped += 1
            stats["skipped"] += 1
            if not pending_album["msgs"]:
                last_id_buf = msg.id
            continue

        if filter_general:
            rt = getattr(msg, "reply_to", None)
            msg_top = getattr(rt, "reply_to_top_id", None) or getattr(rt, "reply_to_msg_id", None)
            if msg_top is not None and msg_top != 1:
                if not pending_album["msgs"]:
                    last_id_buf = msg.id
                continue

        # Filter
        has_media = msg.media and not isinstance(msg.media, (MessageMediaEmpty, MessageMediaWebPage))
        if only_filter == "media" and not has_media:
            if not pending_album["msgs"]:
                last_id_buf = msg.id
            skipped += 1; continue
        if only_filter == "video" and not msg.video:
            if not pending_album["msgs"]:
                last_id_buf = msg.id
            skipped += 1; continue
        if only_filter == "photo" and not msg.photo:
            if not pending_album["msgs"]:
                last_id_buf = msg.id
            skipped += 1; continue

        # Album handling
        if msg.grouped_id:
            if pending_album["gid"] != msg.grouped_id:
                await flush_album()
                pending_album["gid"] = msg.grouped_id
            pending_album["msgs"].append(msg)
            continue

        # Msg is a single message — flush any pending album first
        if pending_album["msgs"]:
            await flush_album()

        ok = await process_and_clone_message(
            client, msg, src_entity, dst_entity, dst_topic_id,
            storage_entity, storage_entity_id, bot_username, hide_sender,
        )
        if ok:
            count += 1
            stats["normal"] += 1
            last_id_buf = msg.id
            save_last_id(topic_key, last_id_buf)
            print(f"    ➡️  [{count}] id={msg.id}          ", end="\r")
        else:
            skipped += 1
            stats["skipped"] += 1
            last_id_buf = msg.id
            save_last_id(topic_key, last_id_buf)

        # Rate limiting — polite pacing
        await asyncio.sleep(0.5)

    # Flush remaining album
    await flush_album()
    if last_id_buf:
        save_last_id(topic_key, last_id_buf)

    return count, skipped


# ══════════════════════════════════════════════════════════════════════════════
# MAIN RUN SESSION
# ══════════════════════════════════════════════════════════════════════════════

async def run_clone_session(client: TelegramClient, cfg: dict):
    src_raw      = cfg["src_raw"]
    dst_raw      = cfg["dst_raw"]
    storage_raw  = cfg["storage_raw"]
    bot_username = cfg["bot_username"]
    skip_general = cfg["skip_general"]
    icon_mode    = cfg["icon_mode"]
    emoji_raw    = cfg.get("emoji_raw", "")
    only_filter  = cfg["only_filter"]
    hide_sender  = cfg["hide_sender"]

    _sep()
    print("  🚀 BẮT ĐẦU CLONE FORUM → SHARE-LINK FORMAT")
    _sep()

    # Resolve entities
    try:
        src_entity     = await resolve_entity(client, src_raw)
        dst_entity     = await resolve_entity(client, dst_raw)
        storage_entity = await resolve_entity(client, storage_raw)
    except Exception as e:
        print(f"  ❌ Không tìm thấy kênh: {e}")
        return

    if not is_forum(src_entity):
        print("  ❌ Kênh NGUỒN không phải forum (chưa bật Topics).")
        return
    if not is_forum(dst_entity):
        print("  ❌ Kênh ĐÍCH không phải forum (chưa bật Topics).")
        return

    storage_entity_id = storage_entity.id
    src_name = str(getattr(src_entity, "title", src_entity.id))
    dst_name = str(getattr(dst_entity, "title", dst_entity.id))
    map_key  = make_key(src_entity.id, dst_entity.id)

    print(f"  SOURCE  : {src_name}")
    print(f"  DEST    : {dst_name}")
    print(f"  STORAGE : {getattr(storage_entity, 'title', storage_entity_id)}")
    print(f"  BOT     : @{bot_username}")

    save_session_meta(map_key, src_name, dst_name, cfg)

    # Parse custom emoji if needed
    custom_emoji_id: Optional[int] = None
    if icon_mode == "fixed" and emoji_raw:
        custom_emoji_id = await _parse_emoji_input(client, emoji_raw)
        if not custom_emoji_id:
            icon_mode = "none"

    # Preload topics
    print("\n  📋 Preload topics…")
    src_topics = await preload_topics_full(client, src_entity)
    dst_topics = await preload_topics_full(client, dst_entity)
    dst_by_title = {info["title"]: tid for tid, info in dst_topics.items()}
    print(f"      src={len(src_topics)} topics | dst={len(dst_topics)} topics")

    if not src_topics:
        print("  ❌ Không load được topics nguồn (cần quyền admin?)")
        return

    # ── Phase 1: Clone topics ─────────────────────────────────────────────────
    topic_map = await clone_all_topics(
        client, dst_entity, map_key,
        src_topics, dst_by_title,
        icon_mode, custom_emoji_id,
        skip_general,
    )

    # ── Phase 2: Clone messages ───────────────────────────────────────────────
    stats = {"normal": 0, "album": 0, "skipped": 0, "errors": 0}
    total_ok = 0
    total_skip = 0
    sorted_ids = sorted(src_topics.keys())
    total_topics = len(sorted_ids)

    print(f"\n  📤 Phase 2: clone messages ({total_topics} topics)…\n")

    for idx, src_id in enumerate(sorted_ids, 1):
        title = src_topics[src_id]["title"]
        if src_id not in topic_map:
            print(f"  [{idx}/{total_topics}] ⏭️  '{title}' — not in topic_map")
            continue

        dst_id = topic_map[src_id]
        print(f"  [{idx}/{total_topics}] 📂 '{title}' (src={src_id} → dst={dst_id})")

        save_session_meta(map_key, src_name, dst_name, cfg, progress={
            "total_ok": total_ok, "topics_done": idx - 1,
            "topics_total": total_topics, "current_topic": title,
        })

        try:
            n, sk = await clone_topic_messages(
                client, src_entity, dst_entity,
                src_id, dst_id,
                storage_entity, storage_entity_id,
                bot_username, cfg, map_key, stats,
            )
            total_ok   += n
            total_skip += sk
            tail = f" ({sk} skip)" if sk else ""
            print(f"      ✅ {n} msg OK{tail}" + " " * 20)

            save_session_meta(map_key, src_name, dst_name, cfg, progress={
                "total_ok": total_ok, "topics_done": idx,
                "topics_total": total_topics, "current_topic": title,
            })
        except Exception as e:
            print(f"\n      ❌ '{title}': {e}")
            stats["errors"] += 1

    _sep()
    print(f"  🎉 CLONE HOÀN THÀNH!")
    print(f"  📊 Tổng msg OK    : {total_ok}")
    print(f"      Bỏ qua        : {total_skip}")
    print(f"      Lỗi           : {stats['errors']}")
    print(f"      Topics xử lý  : {len([s for s in sorted_ids if s in topic_map])}/{total_topics}")
    _sep()


# ══════════════════════════════════════════════════════════════════════════════
# EMOJI PARSER
# ══════════════════════════════════════════════════════════════════════════════

async def _parse_emoji_input(client: TelegramClient, raw: str) -> Optional[int]:
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    parsed = parse_link(raw)
    if not parsed.get("message_id"):
        return None
    try:
        entity = await resolve_entity(client, parsed["channel_raw"])
        msg = await client.get_messages(entity, ids=parsed["message_id"])
        if msg and msg.entities:
            for ent in msg.entities:
                if isinstance(ent, MessageEntityCustomEmoji):
                    return ent.document_id
    except Exception as e:
        print(f"  ⚠️  emoji parse: {e}")
    return None


# ══════════════════════════════════════════════════════════════════════════════
# INPUT HELPERS & INTERACTIVE MENU
# ══════════════════════════════════════════════════════════════════════════════

def _sep():
    print("─" * 56)


def _ask(prompt: str, default: str = "") -> str:
    val = input(prompt).strip()
    return val if val else default


def _yn(prompt: str, default: str = "n") -> bool:
    return _ask(f"{prompt} (y/n, Enter={default}): ", default).lower() == "y"


def input_clone_config(bot_username_default: str = "", storage_default: str = "") -> dict:
    print()
    _sep()
    print("  ⚙️   CẤU HÌNH CLONE FORUM → SHARE-LINK")
    _sep()

    src_raw      = _ask("📥 Forum NGUỒN (ID / username / link): ")
    dst_raw      = _ask("📤 Forum ĐÍCH  (ID / username / link): ")
    storage_raw  = _ask(
        f"🗄  STORAGE CHANNEL (ID){' [' + storage_default + ']' if storage_default else ''}: ",
        storage_default,
    )
    bot_username = _ask(
        f"🤖 Bot username (không cần @){' [' + bot_username_default + ']' if bot_username_default else ''}: ",
        bot_username_default,
    ).lstrip("@")

    print("\n🎨 Icon topic:")
    print("  1. Clone icon từ source  2. Emoji cố định  3. Chỉ title")
    ic = _ask("Lựa chọn [1-3, Enter=1]: ", "1")
    icon_mode = {"1": "clone", "2": "fixed", "3": "none"}.get(ic, "clone")
    emoji_raw = ""
    if icon_mode == "fixed":
        emoji_raw = _ask("   Link tin nhắn có emoji / document_id: ", "")
        if not emoji_raw:
            icon_mode = "none"

    print("\n📌 Filter nội dung:")
    print("  1. Tất cả   2. Chỉ media   3. Chỉ video   4. Chỉ ảnh")
    fc = _ask("Lựa chọn [1-4, Enter=1]: ", "1")
    only_filter = {"1": "all", "2": "media", "3": "video", "4": "photo"}.get(fc, "all")

    hide_sender  = _yn("\n🙈 Ẩn tên người gửi khi lưu vào storage?")
    skip_general = _yn("🏠 Bỏ qua topic General (id=1)?", "n")

    return {
        "src_raw":      parse_link(src_raw)["channel_raw"],
        "dst_raw":      dst_raw,
        "storage_raw":  storage_raw,
        "bot_username": bot_username,
        "icon_mode":    icon_mode,
        "emoji_raw":    emoji_raw,
        "only_filter":  only_filter,
        "hide_sender":  hide_sender,
        "skip_general": skip_general,
    }


def _format_session(s: dict) -> str:
    src  = s.get("src_name", "?")
    dst  = s.get("dst_name", "?")
    last = s.get("last_updated", 0)
    ts   = datetime.datetime.fromtimestamp(last).strftime("%d/%m %H:%M") if last else "?"
    prog = s.get("progress", {})
    parts = []
    if prog.get("total_ok") is not None:
        parts.append(f"✅{prog['total_ok']} msg")
    if prog.get("topics_done") is not None and prog.get("topics_total"):
        parts.append(f"{prog['topics_done']}/{prog['topics_total']} topic")
    if prog.get("current_topic"):
        parts.append(f"đang: «{prog['current_topic']}»")
    tail = f" [{ts}]"
    if parts:
        tail = "  — " + " · ".join(parts) + tail
    return f"{src}  →  {dst}{tail}"


def pick_session_or_new() -> Optional[dict]:
    sessions = load_all_sessions()
    if not sessions:
        return None
    print()
    _sep()
    print("  📚  PHIÊN ĐANG DANG DỞ")
    _sep()
    for i, s in enumerate(sessions, 1):
        print(f"  {i}. {_format_session(s)}")
    print()
    print("  Enter / số = tiếp tục | n = tạo mới | d <số> = xóa")
    _sep()

    while True:
        choice = _ask("  Lựa chọn: ", "1").strip().lower()
        if choice in ("n", "new"):
            return None
        if choice.startswith("d"):
            try:
                idx = int(choice.replace("d", "").strip()) - 1
                if 0 <= idx < len(sessions):
                    s = sessions[idx]
                    if _yn(f"  Xóa phiên '{s.get('src_name')} → {s.get('dst_name')}'?", "n"):
                        delete_session(s["key"])
                        print("  🗑️  Đã xóa.")
                    return pick_session_or_new()
            except ValueError:
                pass
            print("  ❌ Sai format. Ví dụ: d 2")
            continue
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(sessions):
                return sessions[idx]
            print(f"  ❌ Số không hợp lệ (1-{len(sessions)}).")
        except ValueError:
            print("  ❌ Gõ lại: số / n / d <số>")


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT (called from main.py)
# ══════════════════════════════════════════════════════════════════════════════

async def run_cloner():
    """Interactive cloner loop."""
    print("╔══════════════════════════════════════════════════════╗")
    print("║  Telegram Forum → Share-Link Cloner  v1.0            ║")
    print("╚══════════════════════════════════════════════════════╝")

    client = TelegramClient(config.SESSION_NAME, config.API_ID, config.API_HASH)
    await client.start(phone=config.PHONE)
    print("✅ Đã đăng nhập Telegram\n")

    bot_username_default = config.BOT_USERNAME
    storage_default      = str(config.STORAGE_CHANNEL) if config.STORAGE_CHANNEL else ""

    while True:
        picked = pick_session_or_new()

        if picked:
            cfg = picked["cfg"]
            cfg.setdefault("bot_username", bot_username_default)
            cfg.setdefault("storage_raw", storage_default)
            _sep()
            print(f"  ♻️  Tiếp tục: {picked.get('src_name')}  →  {picked.get('dst_name')}")
            _sep()
        else:
            cfg = input_clone_config(
                bot_username_default=bot_username_default,
                storage_default=storage_default,
            )

        await run_clone_session(client, cfg)
        bot_username_default = cfg.get("bot_username", bot_username_default)

        print()
        again = _ask("🔄 Tiếp tục phiên mới? (y/n, Enter=n): ", "n").lower()
        if again != "y":
            break

    print("\n👋 Tạm biệt!")
    await client.disconnect()
