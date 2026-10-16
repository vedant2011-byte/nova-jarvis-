"""Google Gemini chat provider (official REST API, no extra dependency).

Uses the documented v1beta ``generateContent`` endpoint through ``requests``
(a core project dependency), so the Vercel function bundle gains no new
packages. The API key is read ONLY from the GEMINI_API_KEY environment
variable (optionally set via the local .env file, which is git-ignored).

Security invariants:
  * the key is never echoed into responses, user-facing exceptions, logs,
    settings, or anything served to the frontend;
  * provider failures raise GeminiError whose str() is already a safe,
    user-facing message — no prompts, keys, or raw payloads are included.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Tuple

import requests

from jarvis.config.settings import get_settings

log = logging.getLogger("jarvis.brain.gemini")

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
# Selected against THIS account's key: 3.8-flash returned 503 UNAVAILABLE
# while 3.5-flash-lite answered 200 in ~2 s (stable + free-tier eligible).
# Override with GEMINI_MODEL / ai.gemini_model when your key gains access.
DEFAULT_MODEL = "gemini-3.5-flash-lite"
ENV_API_KEY = "GEMINI_API_KEY"
ENV_MODEL = "GEMINI_MODEL"
RETRY_DELAY_S = 1.5                  # single bounded retry on transient 5xx
MAX_MESSAGE_CHARS = 8000                # per-turn input bound
MAX_SYSTEM_CHARS = 20000                # system instruction bound
VALIDATE_TIMEOUT_S = 6.0
VALIDATE_CACHE_OK_TTL = 300.0
VALIDATE_CACHE_FAIL_TTL = 30.0


class GeminiError(Exception):
    """Provider failure. str(self) is safe to show to the user."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.user_message = message


# Cached result of the free GET /models credential check.
_validate: Dict[str, Any] = {"ts": 0.0, "ok": False, "models": [], "error": ""}


def api_key() -> str:
    return os.environ.get(ENV_API_KEY, "").strip()


def model_id() -> str:
    env_model = os.environ.get(ENV_MODEL, "").strip()
    if env_model:
        return env_model
    configured = str(get_settings().get("ai.gemini_model", "") or "").strip()
    return configured or DEFAULT_MODEL


def last_error() -> str:
    """Human-readable reason the provider is unhealthy ('' when healthy)."""
    return "" if _validate["ok"] else str(_validate.get("error") or "")


def _set_fail(message: str) -> bool:
    _validate.update(ts=time.time(), ok=False, error=message)
    return False


def _error_detail(body: Any) -> Tuple[str, str]:
    """Extract (google_status, reason) from a Google error envelope, safely."""
    if not isinstance(body, dict):
        return "", ""
    err = body.get("error")
    if not isinstance(err, dict):
        return "", ""
    status = str(err.get("status", ""))
    reason = ""
    for detail in err.get("details") or []:
        if isinstance(detail, dict) and detail.get("reason"):
            reason = str(detail["reason"])
            break
    return status, reason


def validate(timeout: float = VALIDATE_TIMEOUT_S, force: bool = False) -> bool:
    """Credential/network check via the free ``GET /models`` endpoint.

    Results are cached (success: 5 min, failure: 30 s) so per-request status
    checks never hammer the API. Returns True only if the key authenticated.
    """
    key = api_key()
    if not key:
        return _set_fail("No GEMINI_API_KEY is configured on the backend.")
    now = time.time()
    ttl = VALIDATE_CACHE_OK_TTL if _validate["ok"] else VALIDATE_CACHE_FAIL_TTL
    if not force and now - float(_validate["ts"]) < ttl:
        return bool(_validate["ok"])

    try:
        r = requests.get(
            f"{BASE_URL}/models",
            headers={"x-goog-api-key": key},
            params={"pageSize": 100},
            timeout=timeout,
        )
    except requests.Timeout:
        return _set_fail("The Gemini API did not respond in time (timeout).")
    except requests.RequestException:
        return _set_fail("Could not reach the Gemini API (network error).")

    if r.status_code in (400, 401, 403):
        status, reason = _error_detail(_safe_json(r))
        if reason == "API_KEY_INVALID" or status in ("INVALID_ARGUMENT", "UNAUTHENTICATED"):
            return _set_fail("The Gemini API key was rejected as invalid.")
        if status == "PERMISSION_DENIED":
            return _set_fail("The Gemini API key lacks permission for this API.")
        return _set_fail("The Gemini API rejected the request (authentication).")
    if r.status_code == 429:
        return _set_fail("Gemini rate limit or quota exceeded.")
    if r.status_code >= 500:
        return _set_fail(f"The Gemini service returned an error (HTTP {r.status_code}).")
    if not r.ok:
        return _set_fail(f"Gemini credential check failed (HTTP {r.status_code}).")

    try:
        names = [
            str(m.get("name", "")).split("/", 1)[-1]
            for m in (r.json().get("models") or [])
        ]
    except (ValueError, AttributeError):
        names = []
    _validate.update(
        ts=now, ok=True, models=[n for n in names if n], error="",
    )
    log.info("gemini credentials validated; %d models available", len(names))
    return True


def cached_models() -> List[str]:
    return list(_validate.get("models") or [])


def _safe_json(r: "requests.Response") -> Any:
    try:
        return r.json()
    except ValueError:
        return None


def _raise_for_status(r: "requests.Response", model: str) -> None:
    status, reason = _error_detail(_safe_json(r))
    code = r.status_code
    if code == 400 and (reason == "API_KEY_INVALID" or "API key" in (reason or "")):
        raise GeminiError("auth", "The Gemini API key was rejected as invalid.")
    if code in (401, 403):
        raise GeminiError(
            "auth",
            "The Gemini API key was rejected "
            f"(HTTP {code}{', ' + status if status else ''}). Check GEMINI_API_KEY.",
        )
    if code == 404:
        raise GeminiError(
            "bad_model",
            f"Gemini model '{model}' is not available to this API key. "
            "Set GEMINI_MODEL (or ai.gemini_model) to a model the key can use.",
        )
    if code == 429:
        raise GeminiError(
            "quota",
            "Gemini rate limit or quota exceeded. Wait a moment and try again "
            "(free-tier quotas are limited).",
        )
    if code >= 500:
        raise GeminiError(
            "server",
            f"The Gemini service returned an error (HTTP {code}). Try again shortly.",
        )
    raise GeminiError("http", f"Gemini request failed (HTTP {code}).")


def _map_messages(messages: List[Dict[str, str]]) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Split the chat-style message list into a system prompt and contents."""
    system_parts: List[str] = []
    contents: List[Dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        text = str(m.get("content") or "")
        if not text:
            continue
        if role == "system":
            system_parts.append(text[:MAX_SYSTEM_CHARS])
        elif role == "user":
            contents.append({"role": "user", "parts": [{"text": text[:MAX_MESSAGE_CHARS]}]})
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": text[:MAX_MESSAGE_CHARS]}]})
    return system_parts, contents


def chat(messages: List[Dict[str, str]], timeout: float | None = None) -> str:
    """Blocking chat completion. Raises GeminiError with a safe message."""
    key = api_key()
    if not key:
        raise GeminiError(
            "missing_key",
            "No GEMINI_API_KEY is configured on the backend, so chat is unavailable.",
        )
    settings = get_settings()
    t = timeout or float(settings.get("ai.request_timeout", 60))
    t = max(5.0, min(float(t), 120.0))          # always bounded
    model = model_id()

    system_parts, contents = _map_messages(messages)
    if not contents:
        raise GeminiError("bad_request", "There is no user message to send to the model.")

    payload: Dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": int(settings.get("ai.max_output_tokens", 1024)),
            "temperature": float(settings.get("ai.temperature", 0.4)),
        },
    }
    if system_parts:
        payload["system_instruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}

    for attempt in (1, 2):
        try:
            r = requests.post(
                f"{BASE_URL}/models/{model}:generateContent",
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json=payload,
                timeout=t,
            )
        except requests.Timeout as exc:
            raise GeminiError(
                "timeout", f"The Gemini request timed out after {t:.0f} seconds.",
            ) from exc
        except requests.RequestException as exc:
            raise GeminiError(
                "network", "Could not reach the Gemini API (network error).",
            ) from exc
        # One bounded retry for transient 5xx (never on timeout/network,
        # never unbounded).
        if r.status_code < 500 or attempt == 2:
            break
        time.sleep(RETRY_DELAY_S)

    if not r.ok:
        _raise_for_status(r, model)

    data = _safe_json(r)
    if not isinstance(data, dict):
        raise GeminiError("bad_response", "Gemini returned an unreadable response.")

    candidates = data.get("candidates") or []
    if not candidates:
        feedback = data.get("promptFeedback") or {}
        block = feedback.get("blockReason") or "no candidates returned"
        raise GeminiError(
            "blocked",
            f"The model returned no text ({block}). Rephrase the message and retry.",
        )

    first = candidates[0] or {}
    parts = (first.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
    if not text:
        finish = first.get("finishReason") or "empty output"
        if finish == "MAX_TOKENS":
            raise GeminiError(
                "truncated",
                "The model's reply hit the output-token limit before producing text. "
                "Raise ai.max_output_tokens in settings.",
            )
        raise GeminiError("blocked", f"The model returned no text (finish: {finish}).")

    usage = data.get("usageMetadata") or {}
    log.debug(
        "gemini ok model=%s prompt=%s out=%s",
        model, usage.get("promptTokenCount"), usage.get("candidatesTokenCount"),
    )
    return text
