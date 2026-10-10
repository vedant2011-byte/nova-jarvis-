"""Conversation context assembly for LLM calls."""
from __future__ import annotations

import datetime as dt
import platform
from typing import Any, Dict, List

from jarvis.memory import manager as memory
from jarvis.tools.browser import playwright_available


def dynamic_context() -> str:
    """Compact real-time context string injected into the system prompt."""
    now = dt.datetime.now()
    online = _internet_online()
    lines = [
        f"Time: {now.strftime('%A %d %B %Y, %H:%M')}",
        f"OS: Windows {platform.release()}",
        f"Internet: {'online' if online else 'OFFLINE — say you need internet for web tasks'}",
        f"Browser automation: {'available' if playwright_available() else 'not installed'}",
    ]
    return "\n".join(lines)


def _internet_online() -> bool:
    import socket
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=1.0)
        return True
    except OSError:
        return False


def build_messages(user_input: str, history_limit: int = 10) -> List[Dict[str, str]]:
    """System + recent history + new user message."""
    entries = memory.context_entries(limit=history_limit)
    messages: List[Dict[str, str]] = []
    for e in entries:
        role = "assistant" if e["role"] == "jarvis" else e["role"]
        if role not in ("user", "assistant", "system"):
            continue
        messages.append({"role": role, "content": e["content"]})
    messages.append({"role": "user", "content": user_input})
    return messages
