"""SQLite storage for album tokens and file_id mappings."""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from shared.config import DATA_DIR

DB_PATH = DATA_DIR / "media_db.sqlite"


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS albums (
                token TEXT PRIMARY KEY,
                src_chat_id INTEGER NOT NULL,
                src_msg_id INTEGER NOT NULL,
                dst_msg_id INTEGER,
                topic_name TEXT,
                media_type TEXT,
                file_ids TEXT NOT NULL,
                caption TEXT,
                view_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'ok',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_albums_src ON albums(src_chat_id, src_msg_id);
            CREATE TABLE IF NOT EXISTS stats (
                key TEXT PRIMARY KEY,
                value INTEGER DEFAULT 0
            );
            """
        )


def new_token(prefix: str = "alb") -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def update_file_ids(token: str, file_ids: list[str]) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE albums SET file_ids=?, status='ok' WHERE token=?",
            (json.dumps(file_ids), token),
        )


def save_album(
    *,
    src_chat_id: int,
    src_msg_id: int,
    file_ids: list[str],
    topic_name: str = "",
    media_type: str = "album",
    caption: str = "",
    dst_msg_id: Optional[int] = None,
    token: Optional[str] = None,
) -> str:
    token = token or new_token()
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO albums
            (token, src_chat_id, src_msg_id, dst_msg_id, topic_name,
             media_type, file_ids, caption, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                token,
                src_chat_id,
                src_msg_id,
                dst_msg_id,
                topic_name,
                media_type,
                json.dumps(file_ids),
                caption,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        conn.execute(
            "INSERT OR IGNORE INTO stats(key, value) VALUES('total_albums', 0)"
        )
        conn.execute("UPDATE stats SET value = value + 1 WHERE key='total_albums'")
    return token


def get_album(token: str) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM albums WHERE token=?", (token,)).fetchone()
    if not row:
        return None
    return {
        "token": row["token"],
        "src_chat_id": row["src_chat_id"],
        "src_msg_id": row["src_msg_id"],
        "dst_msg_id": row["dst_msg_id"],
        "topic_name": row["topic_name"],
        "media_type": row["media_type"],
        "file_ids": json.loads(row["file_ids"]),
        "caption": row["caption"] or "",
        "view_count": row["view_count"],
        "status": row["status"],
        "created_at": row["created_at"],
    }


def increment_views(token: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE albums SET view_count = view_count + 1 WHERE token=?", (token,)
        )
        conn.execute(
            "INSERT OR IGNORE INTO stats(key, value) VALUES('total_views', 0)"
        )
        conn.execute("UPDATE stats SET value = value + 1 WHERE key='total_views'")


def list_albums(limit: int = 100, search: str = "") -> list[dict[str, Any]]:
    with _connect() as conn:
        if search:
            rows = conn.execute(
                """
                SELECT * FROM albums
                WHERE token LIKE ? OR CAST(src_msg_id AS TEXT) LIKE ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (f"%{search}%", f"%{search}%", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM albums ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
    out = []
    for row in rows:
        out.append(
            {
                "token": row["token"],
                "src_msg_id": row["src_msg_id"],
                "topic_name": row["topic_name"],
                "media_type": row["media_type"],
                "file_count": len(json.loads(row["file_ids"])),
                "view_count": row["view_count"],
                "status": row["status"],
            }
        )
    return out


def get_stats() -> dict[str, int]:
    with _connect() as conn:
        rows = conn.execute("SELECT key, value FROM stats").fetchall()
        total_albums = conn.execute("SELECT COUNT(*) AS c FROM albums").fetchone()["c"]
        rows = conn.execute("SELECT file_ids FROM albums").fetchall()
        total_files = sum(len(json.loads(r["file_ids"])) for r in rows)
        total_views = conn.execute(
            "SELECT COALESCE(SUM(view_count), 0) AS c FROM albums"
        ).fetchone()["c"]
    stats = {r["key"]: r["value"] for r in rows}
    stats["total_albums"] = total_albums
    stats["total_files"] = total_files
    stats["total_views"] = total_views
    return stats
