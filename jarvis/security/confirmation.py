"""Confirmation system: pending approval requests with UI + voice answers."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("jarvis.confirmation")


@dataclass
class ConfirmationRequest:
    id: str
    tool: str
    title: str
    description: str
    detail: Dict[str, Any] = field(default_factory=dict)
    risk: str = "confirm"
    created_at: float = field(default_factory=time.time)
    # The future is created lazily by request_approval() on the *running* loop.
    # Creating one at dataclass-instantiation time would require an event loop
    # in the current thread (RuntimeError / DeprecationWarning otherwise).
    future: Optional[asyncio.Future] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "tool": self.tool,
            "title": self.title,
            "description": self.description,
            "detail": self.detail,
            "risk": self.risk,
        }


class ConfirmationManager:
    """Manages pending confirmation requests.

    The executor (async) awaits `request_approval` which registers a pending
    request and resolves when the UI or the voice pipeline answers.
    """

    def __init__(self) -> None:
        self._pending: Dict[str, ConfirmationRequest] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # -- executor side -------------------------------------------------------
    async def request_approval(
        self,
        tool: str,
        title: str,
        description: str,
        detail: Optional[Dict[str, Any]] = None,
        risk: str = "confirm",
        timeout: float = 120.0,
        on_request: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> bool:
        request = ConfirmationRequest(
            id=uuid.uuid4().hex[:10],
            tool=tool,
            title=title,
            description=description,
            detail=detail or {},
            risk=risk,
        )
        request.future = asyncio.get_running_loop().create_future()
        with self._lock:
            self._pending[request.id] = request
        if on_request:
            try:
                on_request(request.to_dict())
            except Exception:  # noqa: BLE001
                log.exception("on_request callback failed")
        log.info("confirmation requested id=%s tool=%s", request.id, tool)
        try:
            approved = await asyncio.wait_for(request.future, timeout=timeout)
            return bool(approved)
        except asyncio.TimeoutError:
            log.info("confirmation timed out id=%s", request.id)
            return False
        finally:
            with self._lock:
                self._pending.pop(request.id, None)

    # -- resolver side (called from UI websocket thread or voice pipeline) ----
    def resolve(self, request_id: str, approved: bool) -> bool:
        with self._lock:
            request = self._pending.get(request_id)
        if not request:
            return False
        if request.future is not None and request.future.done():
            return False
        loop = self._loop
        if loop is None or loop.is_closed():
            log.warning("no loop for confirmation resolve; denying %s", request_id)
            with self._lock:
                self._pending.pop(request_id, None)
            if request.future is not None:
                try:
                    request.future.set_result(False)
                except asyncio.InvalidStateError:
                    pass
            return False
        try:
            loop.call_soon_threadsafe(request.future.set_result, bool(approved))
        except RuntimeError:
            return False
        log.info("confirmation resolved id=%s approved=%s", request_id, approved)
        return True

    def resolve_latest(self, approved: bool) -> bool:
        with self._lock:
            if not self._pending:
                return False
            request = sorted(self._pending.values(), key=lambda r: r.created_at)[-1]
        return self.resolve(request.id, approved)

    def pending(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [r.to_dict() for r in sorted(self._pending.values(), key=lambda r: r.created_at)]

    def cancel_all(self) -> None:
        with self._lock:
            requests = list(self._pending.values())
            self._pending.clear()
        loop = self._loop
        for r in requests:
            if r.future is None or r.future.done():
                continue
            if loop is not None and not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(r.future.set_result, False)
                except RuntimeError:
                    pass
            else:
                try:
                    r.future.set_result(False)
                except asyncio.InvalidStateError:
                    pass


_manager: Optional[ConfirmationManager] = None


def get_confirmation_manager() -> ConfirmationManager:
    global _manager
    if _manager is None:
        _manager = ConfirmationManager()
    return _manager
