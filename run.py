#!/usr/bin/env python3
"""Entry point: start web dashboard + API server."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn

from shared.config import load_settings


def main():
    try:
        import telethon
        ver = getattr(telethon, "__version__", "?")
        print(f"   Telethon {ver}")
    except ImportError:
        print("   ⚠️  Chưa cài telethon — chạy: pip install -r requirements.txt")
        sys.exit(1)

    # Kiểm tra import forum API trước khi chạy server
    try:
        from shared.telethon_compat import CreateForumTopicRequest  # noqa: F401
    except ImportError as e:
        print("\n❌ Lỗi Telethon — thiếu file fix hoặc bản Telethon quá cũ.")
        print("   Cách 1 (nhanh): pip install --upgrade telethon==1.44.0")
        print("   Cách 2: tải lại ZIP mới từ GitHub (có shared/telethon_compat.py)")
        print(f"   Chi tiết: {e}\n")
        sys.exit(1)

    cfg = load_settings()
    host = cfg.get("server", {}).get("host", "0.0.0.0")
    port = int(cfg.get("server", {}).get("port", 8080))
    print(f"\n🚀 TG Forum Clone Dashboard")
    print(f"   http://localhost:{port}\n")
    uvicorn.run("backend.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
