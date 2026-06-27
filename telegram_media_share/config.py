"""
config.py — Load settings from .env / environment variables.
All values can be overridden by the interactive prompts in cloner.py.
"""
import os
from pathlib import Path

# Load .env if present (optional dependency)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

# ─── Telegram User Account ─────────────────────────────────────────────────────
API_ID   = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
PHONE    = os.getenv("PHONE", "")

# ─── Bot ───────────────────────────────────────────────────────────────────────
BOT_TOKEN       = os.getenv("BOT_TOKEN", "")
BOT_USERNAME    = os.getenv("BOT_USERNAME", "").lstrip("@")
STORAGE_CHANNEL = int(os.getenv("STORAGE_CHANNEL", "0"))

# Seed admins from env (additional admins stored in DB)
ADMIN_IDS: list[int] = [
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()
]

# ─── Database ──────────────────────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "media_share.db")

# ─── Session files ─────────────────────────────────────────────────────────────
SESSION_NAME       = "cloner_session"
STATE_FILE         = "cloner_state"
TOPIC_MAP_FILE     = "cloner_topicmap"
SESSION_META_PREFIX = "cloner_meta_"

# ─── Cloner tuning ─────────────────────────────────────────────────────────────
BATCH_SIZE      = 50        # messages per API forward call
FLOOD_SLEEP     = 2         # extra seconds after FloodWait
MAX_RETRY       = 4
SAVE_EVERY      = 50        # save state every N messages

# ─── Link format ───────────────────────────────────────────────────────────────
# {bot_username} and {token} are substituted at runtime
LINK_TEMPLATE   = "https://t.me/{bot_username}?start={token}"
CAPTION_TEMPLATE = "Nhấp vào link để xem:\n{link}"
# When original caption exists, append link to it
CAPTION_APPEND  = "\n\n📎 Nhấp để xem: {link}"
