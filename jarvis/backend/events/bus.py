"""Async event bus that broadcasts system events to WebSocket subscribers."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger("jarvis.events")


class EventBus:
    """Fan-out event bus. Producers call `emit`; WS handler attaches queues."""

    def __init__(self) -> None:
        self._subscribers: List[asyncio.Queue] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    def emit(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> None:
        event = {"type": event_type, "ts": time.time(), "data": data or {}}
        log.debug("event %s %s", event_type, data)
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
