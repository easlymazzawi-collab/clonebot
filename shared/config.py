"""Load and save application configuration."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
SETTINGS_PATH = CONFIG_DIR / "settings.json"
EXAMPLE_PATH = CONFIG_DIR / "settings.example.json"


def _defaults() -> dict[str, Any]:
    if EXAMPLE_PATH.exists():
        with open(EXAMPLE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_settings() -> dict[str, Any]:
    if not SETTINGS_PATH.exists():
        cfg = _defaults()
        save_settings(cfg)
        return deepcopy(cfg)
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    base = _defaults()
    return _deep_merge(base, data)


def save_settings(data: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(SETTINGS_PATH)


def _deep_merge(base: dict, override: dict) -> dict:
    out = deepcopy(base)
    for key, val in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(val, dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def env_override(cfg: dict[str, Any]) -> dict[str, Any]:
    tg = cfg.setdefault("telegram", {})
    if os.getenv("TG_API_ID"):
        tg["api_id"] = int(os.getenv("TG_API_ID", "0"))
    if os.getenv("TG_API_HASH"):
        tg["api_hash"] = os.getenv("TG_API_HASH", "")
    if os.getenv("TG_PHONE"):
        tg["phone"] = os.getenv("TG_PHONE", "")
    if os.getenv("BOT_TOKEN"):
        cfg.setdefault("bot", {})["token"] = os.getenv("BOT_TOKEN", "")
    return cfg
