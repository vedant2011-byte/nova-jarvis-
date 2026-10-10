"""Central tool registry.

Every tool declares: name, description, JSON-schema arguments, risk level,
executor, and timeout. The registry validates arguments against the schema
before execution and rejects malformed tool calls.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("jarvis.tools")

_EXECUTOR: Optional["ToolExecutorProtocol"] = None


class ToolExecutorProtocol:
    """Minimal interface the registry needs from the executor environment."""

    def confirm(self, tool: str, title: str, description: str, detail: Dict[str, Any],
                risk: str = "confirm") -> Any:  # coroutine
        raise NotImplementedError

    def cancelled(self) -> bool:
        raise NotImplementedError

    def emit(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> None:
        raise NotImplementedError

    def get_context(self) -> Dict[str, Any]:
        """Dynamic context (cwd, last urls, etc.) provided to tools."""
        return {}


@dataclass
class Tool:
    name: str
    description: str
    args_schema: Dict[str, Any]
    executor_fn: Callable[..., Any]           # may be sync or async; kwargs = validated args
    risk: str = "safe"                        # safe | confirm | high-risk | dynamic
    timeout_s: float = 30.0
    category: str = "general"
    confirmation_title: str = ""
    confirmation_description: str = ""

    def validate(self, args: Dict[str, Any]) -> Optional[str]:
        """Tiny JSON-schema validation (type + required + enum + min/max)."""
        props = self.args_schema.get("properties", {})
        required = self.args_schema.get("required", [])
        for r in required:
            if r not in args:
                return f"missing required argument '{r}'"
        for key, value in args.items():
            if key not in props:
                return f"unexpected argument '{key}'"
            spec = props[key]
            expected = spec.get("type")
            if expected and not _type_ok(value, expected):
                return f"argument '{key}' must be {expected}, got {type(value).__name__}"
            if "enum" in spec and value not in spec["enum"]:
                return f"argument '{key}' must be one of {spec['enum']}"
            if expected == "string" and "max_length" in spec and len(value) > spec["max_length"]:
                return f"argument '{key}' exceeds max length {spec['max_length']}"
            if expected in ("number", "integer"):
                if "minimum" in spec and value < spec["minimum"]:
                    return f"argument '{key}' below minimum {spec['minimum']}"
                if "maximum" in spec and value > spec["maximum"]:
                    return f"argument '{key}' above maximum {spec['maximum']}"
        return None

    def schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "risk": self.risk,
            "category": self.category,
            "parameters": self.args_schema,
        }


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}
        self.executor: Optional[ToolExecutorProtocol] = None

    # -- registration --------------------------------------------------------
    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            log.warning("tool '%s' re-registered; overwriting", tool.name)
        self._tools[tool.name] = tool
        log.debug("registered tool %s (risk=%s)", tool.name, tool.risk)

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def names(self) -> List[str]:
        return sorted(self._tools.keys())

    def schemas(self) -> List[Dict[str, Any]]:
        return [self._tools[n].schema() for n in sorted(self._tools.keys())]

    # -- execution ------------------------------------------------------------
    def execute(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Validate -> permission -> confirm -> run -> wrap result.

        Synchronous by design: tools run on agent worker threads. When a
        confirmation is required, the executor environment bridges to the
        asyncio UI loop and blocks this worker until the user answers.
        """
        import concurrent.futures
        import concurrent.futures as _cf
        from jarvis.security.permissions import RiskLevel, decide
        from jarvis.tools.base import TaskCancelled

        started = time.perf_counter()
        tool = self._tools.get(name)
        if tool is None:
            return {"success": False, "tool": name, "error": f"Unknown tool '{name}'"}

        # 1. schema validation
        err = tool.validate(args or {})
        if err:
            log.info("tool %s rejected: %s", name, err)
            return {"success": False, "tool": name, "error": f"Invalid arguments: {err}"}

        # 2. permission decision
        decision = decide(name, args or {})
        exec_env = self.executor

        def _cancelled() -> bool:
            return bool(exec_env and exec_env.cancelled())

        if _cancelled():
            return {"success": False, "tool": name, "error": "cancelled by user"}

        if decision.risk == RiskLevel.BLOCKED:
            return {"success": False, "tool": name,
                    "error": f"Blocked by security policy: {decision.reason}"}

        # 3. confirmation if needed (blocking bridge handled by exec_env)
        if decision.requires_confirmation:
            if exec_env is None:
                return {"success": False, "tool": name,
                        "error": "Confirmation required but no executor environment is available."}
            title = tool.confirmation_title or f"Run {name}"
            desc = tool.confirmation_description or tool.description
            detail = _confirmation_detail(name, args or {}, decision)
            try:
                approved = exec_env.confirm(tool=name, title=title, description=desc,
                                            detail=detail, risk=decision.risk.value)
            except TaskCancelled:
                return {"success": False, "tool": name, "error": "cancelled by user"}
            if not approved:
                return {"success": False, "tool": name, "error": "User denied permission."}

        if _cancelled():
            return {"success": False, "tool": name, "error": "cancelled by user"}

        # 4. execute with timeout
        try:
            if asyncio.iscoroutinefunction(tool.executor_fn):
                # async tool functions run on a private loop
                loop = None
                if exec_env is not None and hasattr(exec_env, "get_loop"):
                    loop = exec_env.get_loop()
                coro = tool.executor_fn(**(args or {}))
                if loop is not None:
                    fut = asyncio.run_coroutine_threadsafe(coro, loop)
                    result = fut.result(timeout=tool.timeout_s)
                else:
                    with _cf.ThreadPoolExecutor(max_workers=1) as pool:
                        fut = pool.submit(asyncio.run, coro)
                        result = fut.result(timeout=tool.timeout_s)
            else:
                with _cf.ThreadPoolExecutor(max_workers=1) as pool:
                    fut = pool.submit(tool.executor_fn, **(args or {}))
                    try:
                        result = fut.result(timeout=tool.timeout_s)
                    except _cf.TimeoutError:
                        return {"success": False, "tool": name,
                                "error": f"Timed out after {tool.timeout_s:.0f}s",
                                "duration_ms": int((time.perf_counter() - started) * 1000)}
        except concurrent.futures.TimeoutError:
            duration = int((time.perf_counter() - started) * 1000)
            return {"success": False, "tool": name,
                    "error": f"Timed out after {tool.timeout_s:.0f}s", "duration_ms": duration}
        except TaskCancelled:
            return {"success": False, "tool": name, "error": "cancelled by user"}
        except TypeError as exc:
            # bad kwargs that still passed validation
            return {"success": False, "tool": name, "error": f"Bad arguments: {exc}"}
        except TaskCancelled:
            return {"success": False, "tool": name, "error": "cancelled by user"}
        except FileNotFoundError as exc:
            return {"success": False, "tool": name, "error": f"Not found: {exc}"}
        except PermissionError as exc:
            return {"success": False, "tool": name, "error": f"Permission denied: {exc}"}
        except Exception as exc:  # noqa: BLE001
            log.exception("tool %s crashed", name)
            return {"success": False, "tool": name, "error": f"{type(exc).__name__}: {exc}"}

        duration = int((time.perf_counter() - started) * 1000)
        if isinstance(result, dict):
            result.setdefault("tool", name)
            if "duration_ms" not in result:
                result["duration_ms"] = duration
            if exec_env:
                result.setdefault("cancelled", _cancelled())
            return result
        return {"success": True, "tool": name, "result": result, "duration_ms": duration}


def _confirmation_detail(tool: str, args: Dict[str, Any], decision: Any) -> Dict[str, Any]:
    detail: Dict[str, Any] = {"arguments": args}
    if tool == "run_terminal_command":
        detail["risk_analysis"] = decision.detail
    if tool == "delete_file":
        detail["path"] = args.get("path")
    return detail


_registry: Optional[ToolRegistry] = None


def get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        register_all(_registry)
    return _registry


def register_all(registry: ToolRegistry) -> None:
    from jarvis.tools import applications, browser, filesystem, screenshot, system, terminal

    for module in (applications, filesystem, terminal, system, screenshot, browser):
        module.register(registry)
