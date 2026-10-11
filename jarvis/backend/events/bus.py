"""Async event bus that broadcasts system events to WebSocket subscribers."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger("jarvis.events")

# Boot lifecycle events are emitted EXACTLY ONCE during server startup.
# With serverless deployments (Vercel) the WebSocket connection frequently
# attaches to an instance whose boot sequence already finished, so these
# events would be emitted with zero subscribers and lost forever — the HUD
# then waits for a `boot_done` that can never arrive. Retain them (bounded)
# and replay to each new subscriber on connect.
BOOT_REPLAY_EVENTS = frozenset({"boot", "boot_step", "boot_done"})
_BOOT_HISTORY_LIMIT = 100


class EventBus:
    """Fan-out event bus. Producers call `emit`; WS handler attaches queues."""

    def __init__(self) -> None:
        self._subscribers: List[asyncio.Queue] = []
        self._boot_history: List[Dict[str, Any]] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        # Replay boot lifecycle events emitted before this subscriber attached.
        # History is capped well below the queue's maxsize, so this cannot block.
        for past in list(self._boot_history):
            try:
                q.put_nowait(past)
            except asyncio.QueueFull:  # pragma: no cover - defensive
                break
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    def emit(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> None:
        event = {"type": event_type, "ts": time.time(), "data": data or {}}
        log.debug("event %s %s", event_type, data)
        if event_type in BOOT_REPLAY_EVENTS:
            self._boot_history.append(event)
            if len(self._boot_history) > _BOOT_HISTORY_LIMIT:
                del self._boot_history[:-_BOOT_HISTORY_LIMIT]
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("event queue full; dropping event %s", event_type)

    def emit_threadsafe(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> None:
        """Emit from a non-asyncio thread."""
        event = {"type": event_type, "ts": time.time(), "data": data or {}}
        if self._loop is None:
            self.emit(event_type, data)
            return
        try:
            self._loop.call_soon_threadsafe(self.emit, event_type, data)
        except RuntimeError:
            pass


_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
