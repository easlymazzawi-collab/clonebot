"""FastAPI backend — connects HTML dashboard to Userbot + Bot."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from bot import bot_main, media_db
from shared.config import ROOT, env_override, load_settings, save_settings
from shared.logger import log_buffer
from shared.progress import load_all_sessions
from userbot import caption_editor, clone_forum
from userbot.media_scanner import scan_topic_media
from userbot.telegram_client import disconnect_client, get_me_info, is_connected

app = FastAPI(title="TG Forum Clone API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

INDEX_PATH = ROOT / "index.html"


# ── Pydantic models ──────────────────────────────────────────────

class SettingsUpdate(BaseModel):
    telegram: Optional[dict] = None
    bot: Optional[dict] = None
    clone: Optional[dict] = None
    forum: Optional[dict] = None
    webhook_url: Optional[str] = None


class CloneStartRequest(BaseModel):
    src_raw: str = ""
    dst_raw: str = ""
    hide_sender: bool = True
    video_thumbnail_only: bool = True
    batch_size: int = 50
    only_filter: str = "all"
    icon_mode: str = "clone"
    skip_general: bool = False


class CaptionStartRequest(BaseModel):
    start_link: str
    end_link: str = ""
    new_text: str = "{caption_goc}\n\n— Nhấp vào link này để xem: {bot_link}"
    delay: float = 1.2
    embed_bot_link: bool = True


class ScanMediaRequest(BaseModel):
    src_raw: str
    topic_id: int
    topic_name: str = ""


# ── Static ───────────────────────────────────────────────────────

@app.get("/")
async def index():
    if not INDEX_PATH.exists():
        raise HTTPException(404, "index.html not found")
    return FileResponse(INDEX_PATH)


# ── Status & Stats ───────────────────────────────────────────────

@app.get("/api/status")
async def api_status():
    cfg = load_settings()
    userbot_ok = is_connected()
    bot_ok = bot_main.is_running()
    try:
        me = await get_me_info() if userbot_ok else None
    except Exception:
        me = None
        userbot_ok = False

    return {
        "userbot": {
            "connected": userbot_ok,
            "user": me,
        },
        "bot": {
            "running": bot_ok,
            "username": cfg.get("bot", {}).get("username", ""),
        },
        "clone": clone_forum.clone_job.progress.to_dict(),
        "caption": caption_editor.caption_job.progress.to_dict(),
    }


@app.get("/api/stats")
async def api_stats():
    db_stats = media_db.get_stats()
    clone_p = clone_forum.clone_job.progress
    return {
        "msgs_cloned": clone_p.msgs_ok,
        "topics_done": clone_p.topics_done,
        "topics_total": clone_p.topics_total,
        "media_files": db_stats.get("total_files", 0),
        "albums": db_stats.get("total_albums", 0),
        "bot_views": db_stats.get("total_views", 0),
        "bot_requests": db_stats.get("total_albums", 0),
    }


@app.get("/api/logs")
async def api_logs(limit: int = 200, level: Optional[str] = None):
    return {"logs": log_buffer.list(limit=limit, level=level)}


@app.delete("/api/logs")
async def api_clear_logs():
    log_buffer.clear()
    return {"ok": True}


# ── Config ───────────────────────────────────────────────────────

@app.get("/api/config")
async def api_get_config():
    cfg = env_override(load_settings())
    # Mask secrets for display
    safe = _mask_secrets(cfg)
    return safe


@app.post("/api/config")
async def api_save_config(body: SettingsUpdate):
    cfg = load_settings()
    data = body.model_dump(exclude_none=True)
    for key, val in data.items():
        if isinstance(val, dict) and isinstance(cfg.get(key), dict):
            cfg[key].update(val)
        else:
            cfg[key] = val
    save_settings(cfg)
    log_buffer.ok("SYSTEM", "Đã lưu cấu hình")
    return {"ok": True}


def _mask_secrets(cfg: dict) -> dict:
    import copy

    c = copy.deepcopy(cfg)
    if c.get("telegram", {}).get("api_hash"):
        h = c["telegram"]["api_hash"]
        c["telegram"]["api_hash"] = h[:4] + "..." if len(h) > 4 else "***"
    if c.get("bot", {}).get("token"):
        t = c["bot"]["token"]
        c["bot"]["token"] = t[:8] + "..." if len(t) > 8 else "***"
    return c


# ── Userbot connect ──────────────────────────────────────────────

@app.post("/api/userbot/connect")
async def api_userbot_connect():
    try:
        me = await get_me_info()
        return {"ok": True, "user": me}
    except Exception as e:
        log_buffer.err("USERBOT", str(e))
        raise HTTPException(400, str(e))


@app.post("/api/userbot/disconnect")
async def api_userbot_disconnect():
    await disconnect_client()
    return {"ok": True}


# ── Bot control ──────────────────────────────────────────────────

@app.post("/api/bot/start")
async def api_bot_start():
    ok = await bot_main.start_bot()
    if not ok:
        raise HTTPException(400, "Chưa cấu hình bot token")
    return {"ok": True}


@app.post("/api/bot/stop")
async def api_bot_stop():
    await bot_main.stop_bot()
    return {"ok": True}


# ── Clone ────────────────────────────────────────────────────────

@app.post("/api/clone/start")
async def api_clone_start(body: CloneStartRequest):
    if clone_forum.clone_job.is_running():
        raise HTTPException(409, "Clone đang chạy")

    cfg = load_settings()
    clone_cfg = {**cfg.get("clone", {}), **body.model_dump()}
    if not clone_cfg.get("src_raw") or not clone_cfg.get("dst_raw"):
        raise HTTPException(400, "Thiếu forum nguồn hoặc đích")

    await clone_forum.clone_job.start(clone_cfg)
    log_buffer.info("CLONE", "Bắt đầu clone từ dashboard")
    return {"ok": True, "progress": clone_forum.clone_job.progress.to_dict()}


@app.post("/api/clone/stop")
async def api_clone_stop():
    clone_forum.clone_job.request_stop()
    return {"ok": True}


@app.get("/api/clone/progress")
async def api_clone_progress():
    return clone_forum.clone_job.progress.to_dict()


@app.get("/api/sessions")
async def api_sessions():
    return {"sessions": load_all_sessions()}


# ── Caption ──────────────────────────────────────────────────────

@app.post("/api/caption/start")
async def api_caption_start(body: CaptionStartRequest):
    if caption_editor.caption_job.is_running():
        raise HTTPException(409, "Caption edit đang chạy")

    cfg = load_settings()
    cap_cfg = {
        **body.model_dump(),
        "bot_username": cfg.get("bot", {}).get("username", ""),
    }
    await caption_editor.caption_job.start(cap_cfg)
    return {"ok": True}


@app.post("/api/caption/stop")
async def api_caption_stop():
    caption_editor.caption_job.request_stop()
    return {"ok": True}


@app.get("/api/caption/progress")
async def api_caption_progress():
    return caption_editor.caption_job.progress.to_dict()


# ── Media ────────────────────────────────────────────────────────

@app.get("/api/media/albums")
async def api_media_albums(limit: int = 100, search: str = ""):
    media_db.init_db()
    return {"albums": media_db.list_albums(limit=limit, search=search)}


@app.get("/api/media/stats")
async def api_media_stats():
    media_db.init_db()
    return media_db.get_stats()


@app.post("/api/media/scan")
async def api_media_scan(body: ScanMediaRequest):
    cfg = load_settings()
    bot_username = cfg.get("bot", {}).get("username", "")
    if not bot_username:
        raise HTTPException(400, "Chưa cấu hình bot username")

    if not bot_main.is_running():
        await bot_main.start_bot()

    from userbot.clone_forum import resolve_entity
    from userbot.telegram_client import get_client

    client = await get_client()
    src = await resolve_entity(client, body.src_raw)

    async def _run():
        try:
            stats = await scan_topic_media(
                src,
                body.topic_id,
                bot_username=bot_username,
                topic_name=body.topic_name,
            )
            log_buffer.ok("SCAN", f"Xong: {stats['albums']} albums")
        except Exception as e:
            log_buffer.err("SCAN", str(e))

    asyncio.create_task(_run())
    return {"ok": True, "message": "Scan đang chạy nền"}


# ── Startup ──────────────────────────────────────────────────────

@app.on_event("startup")
async def on_startup():
    media_db.init_db()
    cfg = load_settings()
    if not (ROOT / "config" / "settings.json").exists():
        save_settings(cfg)
    log_buffer.info("SYSTEM", "TG Forum Clone API started")


@app.on_event("shutdown")
async def on_shutdown():
    await bot_main.stop_bot()
    await disconnect_client()
