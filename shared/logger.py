"""In-memory log buffer for dashboard."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

LogLevel = Literal["ok", "warn", "err", "info", "skip"]


@dataclass
class LogEntry:
    ts: str
    level: LogLevel
    source: str
    message: str


class LogBuffer:
    def __init__(self, maxlen: int = 2000):
        self._entries: deque[LogEntry] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def add(self, level: LogLevel, source: str, message: str) -> LogEntry:
        entry = LogEntry(
            ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            level=level,
            source=source,
            message=message,
        )
        with self._lock:
            self._entries.append(entry)
        return entry

    def ok(self, source: str, msg: str) -> LogEntry:
        return self.add("ok", source, msg)

    def warn(self, source: str, msg: str) -> LogEntry:
        return self.add("warn", source, msg)

    def err(self, source: str, msg: str) -> LogEntry:
        return self.add("err", source, msg)

    def info(self, source: str, msg: str) -> LogEntry:
        return self.add("info", source, msg)

    def skip(self, source: str, msg: str) -> LogEntry:
        return self.add("skip", source, msg)

    def list(self, limit: int = 200, level: str | None = None) -> list[dict]:
        with self._lock:
            items = list(self._entries)
        if level:
            items = [e for e in items if e.level == level]
        return [
            {"ts": e.ts, "level": e.level, "source": e.source, "message": e.message}
            for e in items[-limit:]
        ]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


log_buffer = LogBuffer()
