"""The JARVIS agent: text in -> reasoning -> tool calls -> verified results -> reply.

Flow:
  user text
    -> if LLM online:  system prompt with tool schema, chat; parse tool call;
       execute via registry; feed result back; loop up to N iterations
    -> else:           rule-based intent engine (offline mode)
  -> final reply saved to history, returned to the server for TTS/UI.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from jarvis.brain import intent_engine, llm
from jarvis.brain.gemini import GeminiError
from jarvis.brain.context import build_messages, dynamic_context
from jarvis.brain.prompts import build_system_prompt
from jarvis.config.settings import get_settings
from jarvis.memory import manager as memory
from jarvis.memory.schemas import TaskRecord, TaskStep
from jarvis.security.confirmation import get_confirmation_manager
from jarvis.tools.base import TaskCancelled
from jarvis.tools.tool_registry import get_registry

log = logging.getLogger("jarvis.brain.agent")


class TaskCancelledByUser(Exception):
    pass


class CancelFlag:
    def __init__(self) -> None:
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()


class ExecutorEnvironment:
    """Bridges the tool registry to confirmations, cancellation and events."""

    def __init__(self, agent: "JarvisAgent"):
        self.agent = agent

    def confirm(self, tool: str, title: str, description: str,
                detail: Dict[str, Any], risk: str = "confirm") -> bool:
        return self.agent._await_confirmation(tool, title, description, detail, risk)

    def cancelled(self) -> bool:
        return self.agent.cancel_flag.cancelled

    def emit(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> None:
        self.agent.emit(event_type, data)


class JarvisAgent:
    def __init__(self) -> None:
        self.registry = get_registry()
        self.confirmations = get_confirmation_manager()
        self.cancel_flag = CancelFlag()
        self.env = ExecutorEnvironment(self)
        self.registry.executor = self.env  # type: ignore[assignment]
        self.emit_fn: Callable[[str, Dict[str, Any]], None] = lambda t, d: None
        self._busy = threading.Lock()

    # ------------------------------------------------------------------
    def emit(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> None:
        try:
            self.emit_fn(event_type, data or {})
        except Exception:  # noqa: BLE001
            log.exception("emit failed")

    def cancel_current(self) -> None:
        self.cancel_flag.cancel()
        self.confirmations.cancel_all()
        self.emit("task_stopped", {"reason": "user requested stop"})

    def reset_cancel(self) -> None:
        self.cancel_flag = CancelFlag()

    # ------------------------------------------------------------------
    def _await_confirmation(self, tool: str, title: str, description: str,
                            detail: Dict[str, Any], risk: str) -> bool:
        """Runs inside the executor worker thread; publishes the request and
        blocks on an asyncio future resolved from the UI/voice threads."""
        cm = self.confirmations

        future_holder: Dict[str, Any] = {}

        def publish(request_dict: Dict[str, Any]) -> None:
            self.emit("confirmation_request", request_dict)
            self.emit("state", {"state": "confirmation", "action": title})

        loop = cm._loop
        if loop is None or loop.is_closed() or not loop.is_running():
            # A stopped/dead loop can never resolve the future — deny safely
            # instead of hanging the executor thread.
            log.error("no live confirmation loop; denying %s", tool)
            return False

        import asyncio as _asyncio

        async def _do_request() -> bool:
            return await cm.request_approval(
                tool, title, description, detail, risk,
                timeout=180.0, on_request=publish)

        fut = _asyncio.run_coroutine_threadsafe(_do_request(), loop)
        try:
            return bool(fut.result(timeout=200))
        except Exception:  # noqa: BLE001
            log.exception("confirmation wait failed")
            return False

    # ------------------------------------------------------------------
    def process_text(self, text: str, source: str = "text") -> Dict[str, Any]:
        """Main entry: process one user command, return reply + metadata."""
        if not text or not text.strip():
            return {"reply": "", "state": "idle"}

        self.reset_cancel()
        if not self._busy.acquire(blocking=False):
            return {"reply": "I'm still working on the previous task.", "state": "busy"}

        try:
            return self._process_inner(text.strip(), source)
        finally:
            self._busy.release()

    def _process_inner(self, text: str, source: str) -> Dict[str, Any]:
        started = time.time()
        memory.remember_conversation("user", text, source)
        self.emit("conversation", {"role": "user", "content": text, "source": source})
        self.emit("state", {"state": "thinking"})

        llm_online = llm.is_online()          # provider-aware (Gemini or Ollama)
        tool_calls: List[Dict[str, Any]] = []
        reply = ""

        try:
            if llm_online:
                reply, tool_calls = self._run_llm_loop(text)
            else:
                reply, tool_calls = self._run_offline(text)
        except TaskCancelledByUser:
            reply = "Task stopped."
            self.emit("task_stopped", {"reason": "cancelled"})
        except (GeminiError, llm.LLMOffline) as exc:
            # Provider failure with a safe, user-facing message — never a
            # fabricated answer and never a raw exception dump.
            log.warning("provider unavailable: %s", exc)
            reply = str(exc)
            self.emit("error", {"message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            log.exception("agent failure")
            reply = f"Something went wrong: {exc}"
            self.emit("error", {"message": str(exc)})

        elapsed = int((time.time() - started) * 1000)
        memory.remember_conversation("jarvis", reply, source)
        self.emit("conversation", {"role": "jarvis", "content": reply, "source": "agent"})
        self.emit("state", {"state": "idle"})
        return {
            "reply": reply,
            "tool_calls": tool_calls,
            "llm_online": llm_online,
            "elapsed_ms": elapsed,
        }

    # ------------------------------------------------------------------
    def _run_llm_loop(self, text: str) -> tuple:
        settings = get_settings()
        registry = self.registry
        schemas = registry.schemas()
        system_prompt = build_system_prompt(schemas, dynamic_context())
        messages = [{"role": "system", "content": system_prompt}]
        history = memory.context_entries(limit=8)
        for e in history[:-1]:
            role = "assistant" if e["role"] == "jarvis" else "user"
            messages.append({"role": role, "content": e["content"][:400]})
        messages.append({"role": "user", "content": text})

        tool_calls: List[Dict[str, Any]] = []
        reply = ""
        max_iter = int(settings.get("ai.max_tool_iterations", 6))

        for _ in range(max_iter):
            if self.cancel_flag.cancelled:
                raise TaskCancelledByUser()
            self.emit("state", {"state": "thinking"})
            raw = llm.chat(messages)
            call = llm.extract_tool_call(raw)

            if call is None:
                reply = llm.strip_tool_json(raw) or "Done."
                break

            tool_name = call["tool"]
            args = call.get("arguments", {})
            tool_calls.append(call)
            self.emit("tool_call", {"tool": tool_name, "arguments": args})

            # nice spoken-status while executing
            self.emit("state", {"state": "executing", "action": _human_action(tool_name, args)})
            result = registry.execute(tool_name, args)
            tool_calls[-1]["result"] = result
            self.emit("tool_result", {"tool": tool_name, "result": result})

            if self.cancel_flag.cancelled:
                raise TaskCancelledByUser()

            # feed result back to the model
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user",
                             "content": f"Tool result for {tool_name}: {json.dumps(result)[:2000]}\n"
                                        f"Reply to the user now in one or two short sentences. "
                                        f"If another tool call is needed, send its JSON."})
        else:
            reply = "I hit my action limit before finishing that task."

        return reply, tool_calls

    # ------------------------------------------------------------------
    def _run_offline(self, text: str) -> tuple:
        lowered = text.lower().strip()

        # natural-language memory capture works offline
        extracted = memory.try_extract_memory(text)
        if extracted:
            memory.save_memory(**extracted)
            msg = f"Remembered: {extracted['key']} -> {extracted['value']}"
            return msg, [{"tool": "save_memory", "arguments": extracted, "result": {"success": True}}]

        # "open my X project"
        m = re.search(r"\bopen\b.*\bmy\b\s+(.+?)\s+(?:project|folder)", lowered)
        if m:
            name = m.group(1).strip()
            loc = memory.find_project_location(name)
            if loc:
                call = {"tool": "open_application",
                        "arguments": {"application": "explorer", "args": loc}}
                self.emit("state", {"state": "executing", "action": f"opening {name}"})
                result = self.registry.execute(call["tool"], call["arguments"])
                if result.get("success"):
                    return f"I found your {name} project. Opening it.", [call | {"result": result}]
                return f"I found the {name} project at {loc}, but opening it failed: {result.get('error')}", []

        intent = intent_engine.match_intent(text)
        if intent is None:
            return self._chat_unavailable_reply(text), []

        tool_name = intent["tool"]
        args = intent["arguments"]
        self.emit("tool_call", {"tool": tool_name, "arguments": args})
        self.emit("state", {"state": "executing", "action": _human_action(tool_name, args)})
        result = self.registry.execute(tool_name, args)
        self.emit("tool_result", {"tool": tool_name, "result": result})

        if result.get("success"):
            reply = _offline_success_reply(tool_name, args, result)
        else:
            reply = f"I attempted the action, but it failed: {result.get('error', 'unknown error')}"
        return reply, [{"tool": tool_name, "arguments": args, "result": result}]

    # ------------------------------------------------------------------
    def _chat_unavailable_reply(self, text: str) -> str:
        """Honest fallback when no model can answer, provider-aware.

        Direct-computer-command intents are handled before this point, so
        local control keeps working even with no model at all.
        """
        if llm.provider() == "gemini":
            reason = llm.offline_reason() or "the model is unavailable"
            return (f"My AI provider is unreachable right now ({reason}), so I "
                    "can't answer conversational questions at the moment. "
                    "Direct computer commands still work.")
        return intent_engine.respond_offline(text)

    # ------------------------------------------------------------------
    def status_snapshot(self) -> Dict[str, Any]:
        from jarvis.brain import llm as llm_mod
        return {
            "llm_online": llm_mod.is_online(),
            "provider": llm_mod.provider_name(),
            "model": llm_mod.active_model(),
            "models": llm_mod.list_models()[:20],
            "tools": self.registry.names(),
            "pending_confirmations": self.confirmations.pending(),
        }


def _human_action(tool: str, args: Dict[str, Any]) -> str:
    labels = {
        "open_application": "opening {application}",
        "close_application": "closing {application}",
        "focus_application": "focusing {application}",
        "run_terminal_command": "running: {command}",
        "create_folder": "creating folder {path}",
        "create_file": "creating file {path}",
        "delete_file": "deleting {path}",
        "search_files": "searching for {query}",
        "open_url": "opening {url}",
        "search_web": "searching for {query}",
        "take_screenshot": "taking screenshot",
        "get_memory_usage": "checking memory",
        "get_cpu_usage": "checking CPU",
        "save_memory": "saving to memory",
    }
    template = labels.get(tool, tool.replace("_", " "))
    try:
        return template.format(**{k: (str(v)[:60]) for k, v in args.items()})
    except (KeyError, IndexError):
        return template


def _offline_success_reply(tool: str, args: Dict[str, Any], result: Dict[str, Any]) -> str:
    payload = result.get("result", {}) if isinstance(result.get("result"), dict) else {}
    if tool == "open_application":
        app = args.get("application", "application")
        return f"{app.title()} is open." if payload.get("verified") else f"Launching {app}."
    if tool == "close_application":
        return "Closed."
    if tool == "get_memory_usage":
        r = payload
        return (f"You're using {r.get('percent')} percent of system memory — "
                f"{r.get('used_gb')} of {r.get('total_gb')} gigabytes.")
    if tool == "get_cpu_usage":
        return f"CPU is at {payload.get('percent')} percent."
    if tool == "get_battery_status":
        r = payload
        if not r.get("present"):
            return "No battery detected — you're on desktop power."
        state = "charging" if r.get("plugged_in") else "on battery"
        return f"Battery is at {r.get('percent')} percent, {state}."
    if tool == "get_disk_usage":
        disks = payload.get("disks", [])
        if disks:
            d = disks[0].get("usage") or {}
            return f"Primary disk is {d.get('percent')} percent full."
        return "Disk information unavailable."
    if tool == "get_current_time":
        return f"It's {payload.get('human')}."
    if tool == "take_screenshot":
        return "Screenshot saved."
    if tool == "create_folder":
        return "Folder created."
    if tool == "list_directory":
        items = payload.get("items", [])
        if not items:
            return "That folder is empty."
        dirs = sum(1 for i in items if i.get("type") == "dir")
        files = len(items) - dirs
        parts = []
        if dirs:
            parts.append(f"{dirs} folders")
        if files:
            parts.append(f"{files} files")
        first = ", ".join(i["name"] for i in items[:3])
        return f"That folder contains {' and '.join(parts)} — including {first}."
    if tool == "search_files":
        matches = payload.get("matches", [])
        if not matches:
            return "I couldn't find any matching files."
        first = matches[0]
        extra = f" plus {len(matches)-1} more" if len(matches) > 1 else ""
        return f"Found {first['name']}{extra}."
    if tool == "search_web":
        results = payload.get("results", [])
        if not results:
            return "The search returned no results."
        return f"Top result: {results[0].get('title', 'unknown')}."
    if tool == "open_url":
        return "Page is open."
    if tool == "save_memory":
        return "Stored."
    return "Done."
