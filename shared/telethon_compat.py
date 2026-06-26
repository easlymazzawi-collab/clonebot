"""Telethon imports compatible with old and new versions."""

from telethon.tl.functions.messages import ForwardMessagesRequest

try:
    from telethon.tl.functions.messages import (
        CreateForumTopicRequest,
        GetForumTopicsRequest,
    )
except ImportError:
    from telethon.tl.functions.channels import (
        CreateForumTopicRequest,
        GetForumTopicsRequest,
    )

__all__ = [
    "CreateForumTopicRequest",
    "GetForumTopicsRequest",
    "ForwardMessagesRequest",
]
