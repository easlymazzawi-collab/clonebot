"""Progress and state file helpers."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from shared.config import ROOT

STATE_PREFIX = "state"
TOPIC_MAP_PREFIX = "topicmap"
SESSION_META_PREFIX = "session_meta"
CAPTION_PROGRESS_DIR = ROOT / ".tg_caption_progress"


def make_key(src_id: int, dst_id: int) -> str:
    return f"{src_id}_{dst_id}"


def _state_path(key: str) -> Path:
    return ROOT / f"{STATE_PREFIX}_{key}.txt"


def _topic_state_path(key: str, topic_id: int) -> Path:
    return ROOT / f"{STATE_PREFIX}_{key}_topic_{topic_id}.txt"


def _topic_map_path(key: str) -> Path:
    return ROOT / f"{TOPIC_MAP_PREFIX}_{key}.txt"


def load_last_id(key: str, topic_id: Optional[int] = None) -> Optional[int]:
    path = _topic_state_path(key, topic_id) if topic_id else _state_path(key)
    if not path.exists():
        return None
    try:
        return int(path.read_text().strip())
    except ValueError:
        return None


def save_last_id(key: str, msg_id: int, topic_id: Optional[int] = None) -> None:
    path = _topic_state_path(key, topic_id) if topic_id else _state_path(key)
    path.write_text(str(msg_id))


def load_topic_map(key: str) -> dict[int, int]:
    path = _topic_map_path(key)
    mapping: dict[int, int] = {}
    if not path.exists():
        return mapping
    for line in path.read_text().splitlines():
        if ":" in line:
            a, b = line.strip().split(":", 1)
            try:
                mapping[int(a)] = int(b)
            except ValueError:
                pass
    return mapping


def save_topic_map(key: str, mapping: dict[int, int]) -> None:
    path = _topic_map_path(key)
    lines = [f"{a}:{b}\n" for a, b in mapping.items()]
    path.write_text("".join(lines))


def save_session_meta(key: str, data: dict[str, Any]) -> None:
    path = ROOT / f"{SESSION_META_PREFIX}_{key}.json"
    data["_last_update"] = datetime.now().isoformat(timespec="seconds")
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def load_all_sessions() -> list[dict[str, Any]]:
    sessions = []
    for path in ROOT.glob(f"{SESSION_META_PREFIX}_*.json"):
        try:
            with open(path, encoding="utf-8") as f:
                sessions.append(json.load(f))
        except Exception:
            continue
    sessions.sort(key=lambda x: x.get("_last_update", ""), reverse=True)
    return sessions


def caption_progress_key(entity_id, start_msg_id, end_msg_id, topic_id) -> str:
    s = f"{entity_id}|t={topic_id or 'NONE'}|{start_msg_id}|{end_msg_id or 'END'}"
    return hashlib.md5(s.encode()).hexdigest()[:16]


def caption_progress_path(key: str) -> Path:
    CAPTION_PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
    return CAPTION_PROGRESS_DIR / f"{key}.json"


def load_caption_progress(entity_id, start_msg_id, end_msg_id, topic_id) -> Optional[dict]:
    path = caption_progress_path(caption_progress_key(entity_id, start_msg_id, end_msg_id, topic_id))
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_caption_progress(entity_id, start_msg_id, end_msg_id, topic_id, data: dict) -> None:
    path = caption_progress_path(caption_progress_key(entity_id, start_msg_id, end_msg_id, topic_id))
    data["_last_update"] = datetime.now().isoformat(timespec="seconds")
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
