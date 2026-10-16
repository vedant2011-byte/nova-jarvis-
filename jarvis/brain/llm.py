"""Local LLM client (Ollama-compatible).

The runtime and model are configurable; nothing is hard-coded to one model.
Provides: health check, model listing, structured chat completion with
JSON tool-call parsing, and graceful offline behaviour.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any, Dict, Generator, List, Optional

import requests

from jarvis.brain import gemini as gemini_provider
from jarvis.config.settings import get_settings

log = logging.getLogger("jarvis.brain.llm")


class LLMOffline(Exception):
    pass


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------
def provider() -> str:
    """Active chat provider: "gemini" | "ollama" | "off".

    `ai.provider` = auto (default) prefers Gemini whenever a GEMINI_API_KEY
    is configured, otherwise the local Ollama runtime. `ai.runtime == "off"
    remains the legacy kill-switch and always wins.
    """
    settings = get_settings()
    if settings.get("ai.runtime", "ollama") == "off":
        return "off"
    choice = str(settings.get("ai.provider", "auto")).lower().strip()
    if choice in ("gemini", "ollama", "off"):
        return choice
    return "gemini" if gemini_provider.api_key() else "ollama"


def provider_name() -> str:
    return provider()


def active_model() -> str:
    """Model id the current provider will actually use."""
    if provider() == "gemini":
        return gemini_provider.model_id()
    return str(get_settings().get("ai.model", "llama3.2:3b"))


def offline_reason() -> str:
    """Human-readable reason chat is unavailable ('' when healthy)."""
    p = provider()
    if p == "off":
        return "The AI runtime is disabled in settings."
    if p == "gemini":
        if not gemini_provider.api_key():
            return "No GEMINI_API_KEY is configured on the backend."
        return gemini_provider.last_error() or "The Gemini API is unreachable right now."
    return f"No local Ollama server detected at {get_settings().get('ai.host')}."


def _host() -> str:
    return str(get_settings().get("ai.host", "http://127.0.0.1:11434")).rstrip("/")


def is_online(timeout: float = 2.0) -> bool:
    """True when the active provider is reachable and credentials work."""
    p = provider()
    if p == "off":
        return False
    if p == "gemini":
        # Cached credential check against the free GET /models endpoint.
        return gemini_provider.validate()
    return _ollama_is_online(timeout)


def _ollama_is_online(timeout: float = 2.0) -> bool:
    try:
        r = requests.get(f"{_host()}/api/tags", timeout=timeout)
        return r.status_code == 200
    except requests.RequestException:
        return False


def list_models() -> List[str]:
    p = provider()
    if p == "off":
        return []
    if p == "gemini":
        gemini_provider.validate()
        return gemini_provider.cached_models() or [gemini_provider.model_id()]
    return _ollama_list_models()


def _ollama_list_models() -> List[str]:
    try:
        r = requests.get(f"{_host()}/api/tags", timeout=3)
        r.raise_for_status()
        models = r.json().get("models", [])
        return [m.get("name", "") for m in models if m.get("name")]
    except requests.RequestException:
        return []


def _messages_payload(messages: List[Dict[str, str]], stream: bool) -> Dict[str, Any]:
    settings = get_settings()
    return {
        "model": settings.get("ai.model", "llama3.2:3b"),
        "messages": messages,
        "stream": stream,
        "options": {
            "temperature": float(settings.get("ai.temperature", 0.2)),
            "num_ctx": int(settings.get("ai.num_ctx", 4096)),
        },
    }


def chat(messages: List[Dict[str, str]], timeout: Optional[float] = None) -> str:
    """Blocking chat completion through the active provider.

    Raises LLMOffline (Ollama) or GeminiError (Gemini); both carry a
    user-safe message in str(exc).
    """
    if provider() == "gemini":
        return gemini_provider.chat(messages, timeout=timeout)
    return _ollama_chat(messages, timeout)


def _ollama_chat(messages: List[Dict[str, str]],
                  timeout: Optional[float] = None) -> str:
    """Blocking chat completion against local Ollama. Raises LLMOffline."""
    settings = get_settings()
    t = timeout or float(settings.get("ai.request_timeout", 60))
    try:
        r = requests.post(f"{_host()}/api/chat", json=_messages_payload(messages, False),
                          timeout=t)
        r.raise_for_status()
        data = r.json()
        return (data.get("message") or {}).get("content", "").strip()
    except requests.ConnectionError as exc:
        raise LLMOffline(f"LLM runtime unreachable at {_host()}") from exc
    except requests.Timeout as exc:
        raise LLMOffline("LLM request timed out") from exc
    except requests.HTTPError as exc:
        raise LLMOffline(f"LLM HTTP error: {exc.response.status_code}") from exc


def chat_stream(messages: List[Dict[str, str]],
                timeout: Optional[float] = None) -> Generator[str, None, None]:
    """Streaming chat completion (Ollama only), yielding content chunks."""
    if provider() == "gemini":
        raise LLMOffline("Streaming is not supported by the Gemini provider.")
    settings = get_settings()
    t = timeout or float(settings.get("ai.request_timeout", 60))
    try:
        with requests.post(f"{_host()}/api/chat", json=_messages_payload(messages, True),
                           timeout=t, stream=True) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                piece = (chunk.get("message") or {}).get("content", "")
                if piece:
                    yield piece
                if chunk.get("done"):
                    break
    except requests.ConnectionError as exc:
        raise LLMOffline(f"LLM runtime unreachable at {_host()}") from exc


# ---------------------------------------------------------------------------
# Tool-call extraction from model output
# ---------------------------------------------------------------------------

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
_BARE_JSON_RE = re.compile(r"(\{[^{}]*\"tool\"[^{}]*\})", re.S)


def _extract_balanced_json(text: str) -> list:
    """Extract balanced {...} regions containing a top-level tool key."""
    out: list = []
    idx = text.find("{")
    while idx != -1:
        depth = 0
        in_str = False
        escape = False
        for i in range(idx, len(text)):
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[idx:i + 1]
                    if '"tool"' in candidate:
                        out.append(candidate)
                    break
        idx = text.find("{", idx + 1)
    return out


def extract_tool_call(text: str) -> Optional[Dict[str, Any]]:
    """Extract a structured tool call from model output.

    Accepts fenced JSON, bare JSON objects with a "tool" key, or a list of
    such objects (returns the first). Returns None when no valid call exists.
    """
    if not text:
        return None
    candidates: List[str] = []
    for match in _JSON_BLOCK_RE.finditer(text):
        candidates.append(match.group(1))
    stripped = text.strip()
    if stripped.startswith("{") and '"tool"' in stripped:
        candidates.append(stripped)
    candidates.extend(_extract_balanced_json(text))

    for raw in candidates:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            data = data[0] if data else None
        if isinstance(data, dict) and isinstance(data.get("tool"), str):
            args = data.get("arguments", {})
            if not isinstance(args, dict):
                args = {}
            return {"tool": data["tool"], "arguments": args}
    return None


def strip_tool_json(text: str) -> str:
    """Remove JSON blocks from model text so only prose remains."""
    cleaned = _JSON_BLOCK_RE.sub("", text)
    cleaned = _BARE_JSON_RE.sub("", cleaned)
    return cleaned.strip()
