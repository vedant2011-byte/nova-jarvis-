"""Gemini provider tests — every network call is mocked; nothing hits the live API.

Covers: successful responses, multi-turn context mapping, missing/invalid key,
quota (429), 5xx, timeouts, network failures, blocked/empty output, provider
dispatch, prompt identity, request bounds, and key-leak prevention.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
import requests

from jarvis.brain import gemini, llm
from jarvis.brain.gemini import GeminiError
from jarvis.config.settings import get_settings

FAKE_KEY = "AIza-TEST-KEY-never-a-real-one"

JARVIS_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = JARVIS_ROOT.parent


class FakeResponse:
    def __init__(self, status_code: int = 200, payload=None):
        self.status_code = status_code
        self._payload = payload
        self.ok = 200 <= status_code < 400

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class StubRequests:
    """Stand-in for the `requests` module as seen by gemini.py only."""

    Timeout = requests.Timeout
    ConnectionError = requests.ConnectionError
    RequestException = requests.RequestException

    def __init__(self) -> None:
        self.calls: list = []
        self.post_response: FakeResponse | None = None
        self.post_queue: list = []       # consumed in order, one per call
        self.get_response: FakeResponse | None = None
        self.post_error: Exception | None = None
        self.get_error: Exception | None = None

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        if self.post_error is not None:
            raise self.post_error
        if self.post_queue:
            return self.post_queue.pop(0)
        return self.post_response

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        if self.get_error is not None:
            raise self.get_error
        return self.get_response


@pytest.fixture()
def stub(monkeypatch) -> StubRequests:
    s = StubRequests()
    monkeypatch.setattr(gemini, "requests", s)
    monkeypatch.setattr(
        gemini, "_validate", {"ts": 0.0, "ok": False, "models": [], "error": ""}
    )
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    return s


def msgs() -> list:
    return [
        {"role": "system", "content": "You are Nova."},
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello."},
        {"role": "user", "content": "I am Vedant"},
    ]


def _text_response(text: str) -> FakeResponse:
    return FakeResponse(200, {
        "candidates": [{"content": {"parts": [{"text": text}]},
                        "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
    })


def _bare_agent():
    """A JarvisAgent without running __init__ (no global-registry mutation).

    Only the attributes the chat path touches are provided; tools and the
    confirmation executor are never reached in these tests.
    """
    import threading
    from jarvis.brain.agent import CancelFlag, JarvisAgent
    from jarvis.tools.tool_registry import get_registry
    agent = object.__new__(JarvisAgent)
    agent.registry = get_registry()
    agent.cancel_flag = CancelFlag()
    agent.emit_fn = lambda t, d: None
    agent._busy = threading.Lock()
    return agent


# ---------------------------------------------------------------------------
# Success path + payload shape
# ---------------------------------------------------------------------------
def test_gemini_success_returns_text(stub):
    stub.post_response = _text_response("Hi Vedant!")
    out = gemini.chat(msgs())
    assert out == "Hi Vedant!"

    method, url, kw = stub.calls[0]
    assert method == "POST"
    assert url.startswith(gemini.BASE_URL)
    assert ":generateContent" in url
    # key travels in a header, never in the URL
    assert FAKE_KEY not in url
    assert kw["headers"]["x-goog-api-key"] == FAKE_KEY
    assert kw["timeout"] <= 120  # always bounded

    payload = kw["json"]
    assert payload["system_instruction"]["parts"][0]["text"] == "You are Nova."
    assert [c["role"] for c in payload["contents"]] == ["user", "model", "user"]
    assert payload["generationConfig"]["maxOutputTokens"] == 1024


def test_long_input_is_truncated(stub):
    stub.post_response = _text_response("ok")
    gemini.chat([{"role": "user", "content": "y" * 50_000}])
    payload = stub.calls[0][2]["json"]
    sent = payload["contents"][0]["parts"][0]["text"]
    assert len(sent) == gemini.MAX_MESSAGE_CHARS


def test_no_user_message_rejected(stub):
    with pytest.raises(GeminiError) as ei:
        gemini.chat([{"role": "system", "content": "only system"}])
    assert ei.value.kind == "bad_request"
    assert not stub.calls  # nothing sent to the network


# ---------------------------------------------------------------------------
# Missing / invalid credentials, quota, server errors, network failures
# ---------------------------------------------------------------------------
def test_missing_key_raises_safe_error(stub, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    with pytest.raises(GeminiError) as ei:
        gemini.chat(msgs())
    assert ei.value.kind == "missing_key"
    assert "GEMINI_API_KEY" in str(ei.value)
    assert not stub.calls  # no network attempt without a key


@pytest.mark.parametrize("code,payload,kind,needle", [
    (400, {"error": {"status": "INVALID_ARGUMENT",
                     "details": [{"reason": "API_KEY_INVALID"}]}},
     "auth", "invalid"),
    (403, {"error": {"status": "PERMISSION_DENIED"}}, "auth", "GEMINI_API_KEY"),
    (429, {"error": {"status": "RESOURCE_EXHAUSTED"}}, "quota", "quota"),
    (500, {"error": {"status": "INTERNAL"}}, "server", "HTTP 500"),
    (503, {"error": {"status": "UNAVAILABLE"}}, "server", "HTTP 503"),
    (404, {"error": {"status": "NOT_FOUND"}}, "bad_model", "GEMINI_MODEL"),
])
def test_http_error_taxonomy(stub, monkeypatch, code, payload, kind, needle):
    monkeypatch.setattr(gemini, "RETRY_DELAY_S", 0)   # keep the 5xx retry fast
    stub.post_response = FakeResponse(code, payload)
    with pytest.raises(GeminiError) as ei:
        gemini.chat(msgs())
    assert ei.value.kind == kind
    assert needle in str(ei.value)
    assert FAKE_KEY not in str(ei.value)  # never leaks into user messages


def test_timeout_mapped_to_safe_message(stub):
    stub.post_error = requests.Timeout("slow")
    with pytest.raises(GeminiError) as ei:
        gemini.chat(msgs())
    assert ei.value.kind == "timeout"
    assert "timed out" in str(ei.value)


def test_network_failure_mapped(stub):
    stub.post_error = requests.ConnectionError("down")
    with pytest.raises(GeminiError) as ei:
        gemini.chat(msgs())
    assert ei.value.kind == "network"
    assert FAKE_KEY not in str(ei.value)


def test_blocked_output(stub):
    stub.post_response = FakeResponse(200, {
        "promptFeedback": {"blockReason": "SAFETY"},
        "candidates": [],
    })
    with pytest.raises(GeminiError) as ei:
        gemini.chat(msgs())
    assert ei.value.kind == "blocked"
    assert "SAFETY" in str(ei.value)


def test_max_tokens_empty_reply_is_explicit(stub):
    stub.post_response = FakeResponse(200, {
        "candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": []}}],
    })
    with pytest.raises(GeminiError) as ei:
        gemini.chat(msgs())
    assert ei.value.kind == "truncated"
    assert "max_output_tokens" in str(ei.value)


def test_unreadable_body(stub):
    stub.post_response = FakeResponse(200, None)
    with pytest.raises(GeminiError) as ei:
        gemini.chat(msgs())
    assert ei.value.kind == "bad_response"


def test_transient_5xx_is_retried_once_and_recovers(stub, monkeypatch):
    monkeypatch.setattr(gemini, "RETRY_DELAY_S", 0)
    stub.post_queue = [
        FakeResponse(503, {"error": {"status": "UNAVAILABLE"}}),
        _text_response("Recovered."),
    ]
    assert gemini.chat(msgs()) == "Recovered."
    assert len(stub.calls) == 2          # exactly one retry


def test_5xx_retry_is_bounded(stub, monkeypatch):
    monkeypatch.setattr(gemini, "RETRY_DELAY_S", 0)
    stub.post_response = FakeResponse(503, {"error": {"status": "UNAVAILABLE"}})
    with pytest.raises(GeminiError) as ei:
        gemini.chat(msgs())
    assert ei.value.kind == "server"
    assert len(stub.calls) == 2          # never more than one retry


def test_timeout_and_network_failures_are_not_retried(stub):
    stub.post_error = requests.Timeout("slow")
    with pytest.raises(GeminiError):
        gemini.chat(msgs())
    assert len(stub.calls) == 1          # a retry would double worst-case latency
    stub.calls.clear()
    stub.post_error = requests.ConnectionError("down")
    with pytest.raises(GeminiError):
        gemini.chat(msgs())
    assert len(stub.calls) == 1


# ---------------------------------------------------------------------------
# Credential validation (free GET /models) — caching included
# ---------------------------------------------------------------------------
def test_validate_success_caches_models(stub):
    stub.get_response = FakeResponse(200, {"models": [
        {"name": "models/gemini-3.8-flash"},
        {"name": "models/gemini-3.5-flash-lite"},
    ]})
    assert gemini.validate() is True
    assert gemini.cached_models() == ["gemini-3.8-flash", "gemini-3.5-flash-lite"]
    assert gemini.validate() is True       # cached — no second call
    assert len(stub.calls) == 1
    assert stub.calls[0][0] == "GET"
    assert FAKE_KEY not in stub.calls[0][1]
    assert stub.calls[0][2]["headers"]["x-goog-api-key"] == FAKE_KEY


def test_validate_rejects_bad_key_and_caches_failure(stub):
    stub.get_response = FakeResponse(400, {"error": {
        "status": "INVALID_ARGUMENT", "details": [{"reason": "API_KEY_INVALID"}]}})
    assert gemini.validate() is False
    assert "invalid" in gemini.last_error().lower()
    assert gemini.validate() is False       # failure cached (30 s TTL)
    assert len(stub.calls) == 1


def test_validate_network_failure_is_reported(stub):
    stub.get_error = requests.ConnectionError("down")
    assert gemini.validate() is False
    assert gemini.last_error()


def test_validate_without_key_never_hits_network(stub, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    assert gemini.validate() is False
    assert "GEMINI_API_KEY" in gemini.last_error()
    assert not stub.calls


# ---------------------------------------------------------------------------
# Provider selection + dispatch through llm.py
# ---------------------------------------------------------------------------
def test_provider_auto_selects_gemini_with_key(stub):
    assert llm.provider() == "gemini"
    assert llm.provider_name() == "gemini"
    assert llm.active_model() == "gemini-3.5-flash-lite"  # verified for this key


def test_provider_falls_back_to_ollama_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert llm.provider() == "ollama"


def test_provider_explicit_choice_and_off_switch(stub):
    s = get_settings()
    s.set("ai.provider", "ollama", save=False)
    assert llm.provider() == "ollama"
    s.set("ai.provider", "auto", save=False)
    s.set("ai.runtime", "off", save=False)   # legacy kill-switch always wins
    assert llm.provider() == "off"
    assert llm.is_online() is False
    assert isinstance(llm.offline_reason(), str)
    assert llm.offline_reason() == "The AI runtime is disabled in settings."


def test_chat_dispatch_routes_to_gemini(stub):
    stub.post_response = _text_response("PONG")
    assert llm.chat(msgs()) == "PONG"


def test_chat_dispatch_routes_to_ollama_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(llm, "_ollama_chat",
                        lambda messages, timeout=None: "OLLAMA")
    assert llm.chat(msgs()) == "OLLAMA"


def test_is_online_uses_provider_health(stub):
    stub.get_response = FakeResponse(200, {"models": [{"name": "models/gemini-3.8-flash"}]})
    assert llm.is_online() is True


def test_offline_reason_is_always_plain_string(stub, monkeypatch):
    assert isinstance(llm.offline_reason(), str)   # regression: was a tuple
    monkeypatch.delenv("GEMINI_API_KEY")
    get_settings().set("ai.provider", "gemini", save=False)
    reason = llm.offline_reason()
    assert isinstance(reason, str)
    assert "GEMINI_API_KEY" in reason


def test_streaming_refused_for_gemini(stub):
    with pytest.raises(llm.LLMOffline):
        next(llm.chat_stream(msgs()))


# ---------------------------------------------------------------------------
# Agent behaviour with an unavailable provider
# ---------------------------------------------------------------------------
def test_chat_unavailable_reply_gemini_is_specific(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    agent = _bare_agent()
    msg = agent._chat_unavailable_reply("hi")
    assert "AI provider" in msg


def test_chat_unavailable_reply_keeps_local_canned_text(monkeypatch):
    from jarvis.brain import intent_engine
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    agent = _bare_agent()
    assert agent._chat_unavailable_reply("hello") == intent_engine.respond_offline("hello")


def test_provider_error_surfaces_as_safe_reply(monkeypatch):
    """A GeminiError raised mid-conversation must not become a stack dump."""
    from jarvis.brain.gemini import GeminiError as GE

    agent = _bare_agent()
    events: list = []
    agent.emit_fn = lambda t, d: events.append((t, d))

    monkeypatch.setattr(llm, "is_online", lambda *a, **k: True)
    monkeypatch.setattr(
        llm, "chat",
        lambda *a, **k: (_ for _ in ()).throw(
            GE("quota", "Gemini rate limit or quota exceeded."))
    )
    out = agent.process_text("hello there")
    assert out["reply"] == "Gemini rate limit or quota exceeded."
    assert any(t == "error" and "quota" in d.get("message", "").lower()
               for t, d in events)
    assert FAKE_KEY not in out["reply"]


# ---------------------------------------------------------------------------
# Multi-turn conversation context
# ---------------------------------------------------------------------------
def test_multi_turn_history_reaches_the_model(monkeypatch):
    from jarvis.memory import manager as memory

    memory.clear_history()
    memory.remember_conversation("user", "I am Vedant", "text")
    memory.remember_conversation("jarvis", "Noted, Vedant.", "text")
    # _process_inner always records the incoming message before building the
    # context window — mirror that call order here.
    memory.remember_conversation("user", "What is my name?", "text")

    captured: dict = {}

    def fake_chat(messages, timeout=None):
        captured["messages"] = messages
        return "Hi Vedant!"

    monkeypatch.setattr(llm, "chat", fake_chat)
    agent = _bare_agent()
    try:
        reply, tool_calls = agent._run_llm_loop("What is my name?")
        assert reply == "Hi Vedant!"
        msgs_out = captured["messages"]
        # bounded: system + <=8 history entries + current message
        assert len(msgs_out) <= 10
        assert msgs_out[0]["role"] == "system"
        assert "Nova" in msgs_out[0]["content"]
        assert any(m["role"] == "user" and "I am Vedant" in m["content"]
                   for m in msgs_out[1:-1])
        assert any(m["role"] == "assistant" and "Noted" in m["content"]
                   for m in msgs_out[1:-1])
        assert msgs_out[-1] == {"role": "user", "content": "What is my name?"}
        assert tool_calls == []
    finally:
        memory.clear_history()


# ---------------------------------------------------------------------------
# System prompt: identity, override, injection defence
# ---------------------------------------------------------------------------
def test_system_prompt_identity_and_ground_rules():
    from jarvis.brain.prompts import build_system_prompt
    p = build_system_prompt([], "")
    assert "Nova" in p
    assert "untrusted" in p.lower()
    assert "never fabricate" in p.lower() or "never claim" in p.lower()


def test_system_prompt_custom_instruction_override():
    from jarvis.brain.prompts import build_system_prompt
    get_settings().set("ai.system_instruction", "You are Override-Bot.", save=False)
    p = build_system_prompt([], "")
    assert p.startswith("You are Override-Bot.")


def test_tool_rules_still_present_with_custom_instruction():
    from jarvis.brain.prompts import build_system_prompt
    get_settings().set("ai.system_instruction", "Custom.", save=False)
    p = build_system_prompt([{"name": "take_screenshot", "risk": "safe",
                              "description": "Screenshot",
                              "parameters": {"properties": {}}}], "")
    assert "TOOL CALL FORMAT" in p
    assert "take_screenshot" in p


# ---------------------------------------------------------------------------
# Secrets hygiene
# ---------------------------------------------------------------------------
def test_api_key_never_logged(stub, caplog):
    stub.post_response = FakeResponse(400, {"error": {
        "status": "INVALID_ARGUMENT", "details": [{"reason": "API_KEY_INVALID"}]}})
    stub.get_response = FakeResponse(400, {"error": {
        "status": "INVALID_ARGUMENT", "details": [{"reason": "API_KEY_INVALID"}]}})
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(GeminiError):
            gemini.chat(msgs())
        gemini.validate(force=True)
    assert FAKE_KEY not in caplog.text


def test_frontend_sources_never_reference_gemini_key():
    """No key material in frontend sources — not the env var name, not values."""
    src = JARVIS_ROOT / "frontend" / "src"
    offenders = []
    for p in src.rglob("*"):
        if not (p.is_file() and p.suffix in {".ts", ".tsx", ".js", ".jsx"}):
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if "GEMINI_API_KEY" in text or "AIza" in text:
            offenders.append(str(p))
    assert offenders == []


def test_env_example_is_placeholder_only_and_ignored_by_git():
    example = JARVIS_ROOT / ".env.example"
    assert example.exists()
    for line in example.read_text(encoding="utf-8").splitlines():
        if line.startswith("GEMINI_API_KEY="):
            assert line.split("=", 1)[1].strip() == "", "no real key in .env.example"
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in [l.strip() for l in gitignore]


def test_no_new_dependency_for_gemini():
    """The provider must ride on existing deps only (requests)."""
    reqs = (JARVIS_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "requests" in reqs
    assert "google-genai" not in reqs
    assert "google-generativeai" not in reqs
