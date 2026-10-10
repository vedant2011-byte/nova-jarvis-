"""Tool result envelope and cancellation token."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class CancelToken:
    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True

    def throw_if_cancelled(self) -> None:
        if self.cancelled:
            raise TaskCancelled()


class TaskCancelled(Exception):
    """Raised inside a tool when the user pressed STOP."""


@dataclass
class ToolResult:
    success: bool
    tool: str
    result: Optional[Any] = None
    error: Optional[str] = None
    duration_ms: int = 0
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "success": self.success,
            "tool": self.tool,
            "duration_ms": self.duration_ms,
        }
        if self.success:
            d["result"] = self.result
        else:
            d["error"] = self.error or "Unknown error"
        if self.meta:
            d["meta"] = self.meta
        return d

    @classmethod
    def ok(cls, tool: str, result: Any, duration_ms: int = 0, meta: Optional[Dict] = None) -> "ToolResult":
        return cls(success=True, tool=tool, result=result, duration_ms=duration_ms, meta=meta or {})

    @classmethod
    def fail(cls, tool: str, error: str, duration_ms: int = 0, meta: Optional[Dict] = None) -> "ToolResult":
        return cls(success=False, tool=tool, error=error, duration_ms=duration_ms, meta=meta or {})


def now_ms() -> int:
    return int(time.time() * 1000)
