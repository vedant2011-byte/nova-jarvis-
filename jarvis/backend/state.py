"""Global application state singleton and health snapshot."""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

log = logging.getLogger("jarvis.backend.state")

_lock = threading.Lock()
_state: Dict[str, Any] = {
    "app_state": "initializing",   # initializing | idle | listening | thinking | executing | confirmation | speaking | error
    "last_action": "",
    "voice_listening": False,
    "started_at": None,
}


def update(**kwargs: Any) -> None:
    with _lock:
        _state.update(kwargs)
        _state["app_state"] = _state.get("app_state", "idle")


def get(key: str, default: Any = None) -> Any:
    with _lock:
        return _state.get(key, default)


def snapshot() -> Dict[str, Any]:
    with _lock:
        return dict(_state)


def set_started() -> None:
    import time
    with _lock:
        _state["started_at"] = time.time()
