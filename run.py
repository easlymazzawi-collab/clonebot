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
    cfg = load_settings()
    host = cfg.get("server", {}).get("host", "0.0.0.0")
    port = int(cfg.get("server", {}).get("port", 8080))
    print(f"\n🚀 TG Forum Clone Dashboard")
    print(f"   http://localhost:{port}\n")
    uvicorn.run("backend.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
