"""Parse Telegram message / channel links."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class ParsedLink:
    channel_raw: str
    message_id: Optional[int] = None
    topic_id: Optional[int] = None


def parse_link(raw: str) -> ParsedLink:
    raw = raw.strip()
    result = ParsedLink(channel_raw=raw)

    m = re.search(r"t\.me/c/(\d+)/(\d+)/(\d+)", raw)
    if m:
        return ParsedLink(f"-100{m.group(1)}", int(m.group(3)), int(m.group(2)))

    m = re.search(r"t\.me/c/(\d+)/(\d+)", raw)
    if m:
        return ParsedLink(f"-100{m.group(1)}", int(m.group(2)))

    m = re.search(r"t\.me/([^/]+)/(\d+)/(\d+)", raw)
    if m:
        return ParsedLink(m.group(1), int(m.group(3)), int(m.group(2)))

    m = re.search(r"t\.me/([^/]+)/(\d+)$", raw)
    if m:
        return ParsedLink(m.group(1), int(m.group(2)))

    return result


def parse_message_link(link: str) -> tuple[str | int, int, Optional[int]]:
    """Compatibility with caption editor: (entity, msg_id, topic_id)."""
    p = parse_link(link)
    if p.message_id is None:
        raise ValueError(f"Không parse được link: {link!r}")
    return p.channel_raw, p.message_id, p.topic_id


def msg_in_topic(msg, topic_id: Optional[int]) -> bool:
    if topic_id is None:
        return True
    if msg.id == topic_id:
        return True
    rt = getattr(msg, "reply_to", None)
    if rt is None:
        return False
    top = getattr(rt, "reply_to_top_id", None)
    if top == topic_id:
        return True
    if top is None and getattr(rt, "reply_to_msg_id", None) == topic_id:
        return True
    return False
