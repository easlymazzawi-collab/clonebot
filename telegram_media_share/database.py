"""
database.py — Async SQLite database layer using aiosqlite.

Tables:
  media     — token → storage channel message mapping
  users     — tracked users
  admins    — admin user IDs
  broadcasts — broadcast history
"""
import time
import secrets
import string
import aiosqlite
from typing import Optional

import config


# ─── Token generation ─────────────────────────────────────────────────────────

_ALPHABET = string.ascii_letters + string.digits

def generate_token(length: int = 12) -> str:
    """Generate a URL-safe alphanumeric token."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


# ─── Schema ───────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS media (
    token           TEXT PRIMARY KEY,
    storage_chat_id INTEGER NOT NULL,
    storage_msg_id  INTEGER NOT NULL,
    media_type      TEXT NOT NULL,
    original_caption TEXT DEFAULT '',
    file_name       TEXT DEFAULT '',
    created_at      REAL NOT NULL,
    access_count    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS users (
    user_id      INTEGER PRIMARY KEY,
    username     TEXT DEFAULT '',
    first_name   TEXT DEFAULT '',
    last_name    TEXT DEFAULT '',
    joined_at    REAL NOT NULL,
    last_active  REAL,
    is_banned    INTEGER DEFAULT 0,
    ban_reason   TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS admins (
    user_id  INTEGER PRIMARY KEY,
    added_at REAL NOT NULL,
    added_by INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS broadcasts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at      REAL,
    sent_by      INTEGER,
    message_text TEXT DEFAULT '',
    total_sent   INTEGER DEFAULT 0,
    total_failed INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);
"""


# ─── Database class ───────────────────────────────────────────────────────────

class Database:
    def __init__(self, path: str = config.DB_PATH):
        self.path = path
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self):
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        # Seed initial admins from config
        for uid in config.ADMIN_IDS:
            await self._ensure_admin(uid, added_by=0)
        await self._db.commit()

    async def close(self):
        if self._db:
            await self._db.close()

    async def _ensure_admin(self, user_id: int, added_by: int = 0):
        await self._db.execute(
            "INSERT OR IGNORE INTO admins (user_id, added_at, added_by) VALUES (?, ?, ?)",
            (user_id, time.time(), added_by),
        )

    # ── Media ──────────────────────────────────────────────────────────────────

    async def save_media(
        self,
        storage_chat_id: int,
        storage_msg_id: int,
        media_type: str,
        original_caption: str = "",
        file_name: str = "",
    ) -> str:
        """Store a media entry and return the generated token."""
        token = generate_token()
        # Ensure uniqueness (astronomically unlikely collision, but safe)
        while True:
            row = await self.get_media(token)
            if row is None:
                break
            token = generate_token()

        await self._db.execute(
            """INSERT INTO media
               (token, storage_chat_id, storage_msg_id, media_type,
                original_caption, file_name, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (token, storage_chat_id, storage_msg_id, media_type,
             original_caption, file_name, time.time()),
        )
        await self._db.commit()
        return token

    async def get_media(self, token: str) -> Optional[dict]:
        async with self._db.execute(
            "SELECT * FROM media WHERE token = ?", (token,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def increment_access(self, token: str):
        await self._db.execute(
            "UPDATE media SET access_count = access_count + 1 WHERE token = ?",
            (token,),
        )
        await self._db.commit()

    async def delete_media(self, token: str) -> bool:
        cur = await self._db.execute("DELETE FROM media WHERE token = ?", (token,))
        await self._db.commit()
        return cur.rowcount > 0

    async def count_media(self) -> int:
        async with self._db.execute("SELECT COUNT(*) FROM media") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    async def total_accesses(self) -> int:
        async with self._db.execute("SELECT COALESCE(SUM(access_count),0) FROM media") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    # ── Users ──────────────────────────────────────────────────────────────────

    async def upsert_user(
        self,
        user_id: int,
        username: str = "",
        first_name: str = "",
        last_name: str = "",
    ):
        now = time.time()
        await self._db.execute(
            """INSERT INTO users (user_id, username, first_name, last_name, joined_at, last_active)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 username    = excluded.username,
                 first_name  = excluded.first_name,
                 last_name   = excluded.last_name,
                 last_active = excluded.last_active""",
            (user_id, username or "", first_name or "", last_name or "", now, now),
        )
        await self._db.commit()

    async def get_user(self, user_id: int) -> Optional[dict]:
        async with self._db.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def ban_user(self, user_id: int, reason: str = ""):
        await self._db.execute(
            "UPDATE users SET is_banned = 1, ban_reason = ? WHERE user_id = ?",
            (reason, user_id),
        )
        await self._db.commit()

    async def unban_user(self, user_id: int):
        await self._db.execute(
            "UPDATE users SET is_banned = 0, ban_reason = '' WHERE user_id = ?",
            (user_id,),
        )
        await self._db.commit()

    async def is_banned(self, user_id: int) -> bool:
        async with self._db.execute(
            "SELECT is_banned FROM users WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
            return bool(row[0]) if row else False

    async def count_users(self) -> int:
        async with self._db.execute("SELECT COUNT(*) FROM users") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    async def get_all_user_ids(self) -> list[int]:
        async with self._db.execute(
            "SELECT user_id FROM users WHERE is_banned = 0"
        ) as cur:
            rows = await cur.fetchall()
            return [r[0] for r in rows]

    async def get_recent_users(self, limit: int = 20) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM users ORDER BY last_active DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    # ── Admins ─────────────────────────────────────────────────────────────────

    async def is_admin(self, user_id: int) -> bool:
        async with self._db.execute(
            "SELECT 1 FROM admins WHERE user_id = ?", (user_id,)
        ) as cur:
            return await cur.fetchone() is not None

    async def add_admin(self, user_id: int, added_by: int = 0) -> bool:
        try:
            await self._db.execute(
                "INSERT OR IGNORE INTO admins (user_id, added_at, added_by) VALUES (?, ?, ?)",
                (user_id, time.time(), added_by),
            )
            await self._db.commit()
            return True
        except Exception:
            return False

    async def remove_admin(self, user_id: int) -> bool:
        cur = await self._db.execute(
            "DELETE FROM admins WHERE user_id = ?", (user_id,)
        )
        await self._db.commit()
        return cur.rowcount > 0

    async def list_admins(self) -> list[int]:
        async with self._db.execute("SELECT user_id FROM admins") as cur:
            rows = await cur.fetchall()
            return [r[0] for r in rows]

    # ── Broadcasts ─────────────────────────────────────────────────────────────

    async def log_broadcast(
        self, sent_by: int, message_text: str, total_sent: int, total_failed: int
    ) -> int:
        cur = await self._db.execute(
            """INSERT INTO broadcasts (sent_at, sent_by, message_text, total_sent, total_failed)
               VALUES (?, ?, ?, ?, ?)""",
            (time.time(), sent_by, message_text, total_sent, total_failed),
        )
        await self._db.commit()
        return cur.lastrowid

    async def get_broadcast_history(self, limit: int = 10) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM broadcasts ORDER BY sent_at DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    # ── Settings ───────────────────────────────────────────────────────────────

    async def get_setting(self, key: str, default: str = "") -> str:
        async with self._db.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else default

    async def set_setting(self, key: str, value: str):
        await self._db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        await self._db.commit()

    # ── Stats ──────────────────────────────────────────────────────────────────

    async def get_stats(self) -> dict:
        return {
            "total_users":   await self.count_users(),
            "total_media":   await self.count_media(),
            "total_accesses": await self.total_accesses(),
            "total_admins":  len(await self.list_admins()),
        }


# Module-level singleton — initialized in main/bot entry point
db = Database()
