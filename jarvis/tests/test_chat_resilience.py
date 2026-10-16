"""Chat-hang regression suite (2026-10-16 production incident).

Production evidence from the deployed site (commit d7dc23b):
  * POST /api/command answered `{"accepted": true}` with NO reply, and not a
    single `conversation` event arrived over the WebSocket within 15 s —
    turn 1 already dead; earlier turns only worked by instance-affinity luck.
  * Settings reported backend STT unavailable (serverless has no mic).
  * planner multi-step messages ("X and then Y") referenced an unimported
    `memory` module.

These tests pin the fixed behavior:
  * 20 sequential turns: HTTP reply AND a conversation event per turn
  * provider timeout/error mid-run never wedges the next turn
  * a dropped WebSocket reconnects with boot replay and delivers live events
  * the API key never leaks over HTTP, WS frames or history
  * planner commands execute without NameError
"""
from __future__ import annotations

import asyncio
import queue
import time

import pytest
from fastapi.testclient import TestClient

FAKE_KEY = "AIzaFAKE-LEAK-CANARY-0123456789"
BOOT_TYPES = ("boot", "boot_step", "boot_done")


@pytest.fixture(scope="module")
def client():
    from jarvis.backend.server import app

    with TestClient(app) as c:  # runs startup events
        yield c


def _wait_event(q: asyncio.Queue, pred, timeout: float = 5.0):
    """Poll a bus queue from the test thread with a real deadline.

    asyncio.Queue.get(timeout) needs a running loop in the *calling* thread,
    which the test thread doesn't have — so drain with get_nowait (the same
    pattern the boot-replay tests use) and sleep between attempts.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            ev = q.get_nowait()
        except asyncio.QueueEmpty:
            time.sleep(0.02)
            continue
        if pred(ev):
            return ev
    return None


def _drain(q: asyncio.Queue) -> list:
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except asyncio.QueueEmpty:
            return out


def _patch_llm(monkeypatch, chat_fn):
    """Deterministic provider: no network, no quota, no real key needed."""
    from jarvis.brain import llm

    monkeypatch.setattr(llm, "is_online", lambda *a, **k: True)
    monkeypatch.setattr(llm, "chat", chat_fn)


# ---------------------------------------------------------------------------
# 20 sequential turns
# ---------------------------------------------------------------------------
def test_twenty_sequential_turns_each_get_http_reply_and_ws_event(client, monkeypatch):
    from jarvis.backend import server as server_mod
    from jarvis.brain import llm
    from jarvis.memory import manager as memory

    calls = {"n": 0}

    def fake_chat(messages, timeout=None):
        calls["n"] += 1
        return f"turn-reply-{calls['n']}"

    _patch_llm(monkeypatch, fake_chat)
    memory.clear_history()
    sub = server_mod.bus.subscribe()
    _drain(sub)  # discard boot replay for this subscriber

    is_jarvis = lambda ev: (  # noqa: E731
        ev.get("type") == "conversation"
        and (ev.get("data") or {}).get("role") == "jarvis"
    )

    try:
        for i in range(20):
            text = f"turn number {i}"
            r = client.post("/api/command", json={"text": text, "source": "text"})
            assert r.status_code == 200, f"turn {i}: HTTP {r.status_code}"
            body = r.json()
            assert body.get("accepted") is True, f"turn {i}: {body}"
            assert body.get("done") is True, f"turn {i}: worker not finished: {body}"
            assert (body.get("reply") or "").strip(), f"turn {i}: empty reply: {body}"
            # the same reply must be observable as a live event (what the HUD
            # renders) — no silent turns
            ev = _wait_event(sub, is_jarvis, timeout=5.0)
            assert ev is not None, f"turn {i}: no conversation event emitted"
            assert (ev.get("data") or {}).get("content"), f"turn {i}: empty event content"

        assert calls["n"] == 20, (
            f"expected exactly one model call per turn, got {calls['n']} "
            "(duplicate processing)"
        )
        # the busy lock must be fully released after the run
        r = client.post("/api/command", json={"text": "turn number 21", "source": "text"})
        assert r.status_code == 200 and r.json().get("accepted") is True
    finally:
        memory.clear_history()


# ---------------------------------------------------------------------------
# provider errors / timeouts mid-conversation
# ---------------------------------------------------------------------------
def test_provider_timeout_does_not_wedge_next_turn(client, monkeypatch):
    from jarvis.backend import server as server_mod
    from jarvis.memory import manager as memory

    calls = {"n": 0}

    def flaky_chat(messages, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("simulated provider timeout")
        return "recovered reply"

    _patch_llm(monkeypatch, flaky_chat)
    memory.clear_history()
    sub = server_mod.bus.subscribe()
    _drain(sub)
    try:
        r1 = client.post("/api/command", json={"text": "first turn flaky", "source": "text"})
        assert r1.status_code == 200, r1.status_code
        reply1 = str(r1.json().get("reply") or "")
        assert reply1.strip(), f"timeout produced no usable reply: {r1.json()}"
        assert "Traceback" not in reply1
        assert FAKE_KEY not in reply1
        assert "GEMINI_API_KEY" not in reply1

        r2 = client.post("/api/command", json={"text": "second turn healthy", "source": "text"})
        assert r2.status_code == 200
        assert r2.json().get("accepted") is True, "busy lock stuck after failure"
        reply2 = str(r2.json().get("reply") or "")
        assert reply2.strip(), f"chat dead after provider error: {r2.json()}"
        # next turn must reach the provider again (not stuck in a dead state)
        assert calls["n"] >= 2, "provider never called again after timeout"
    finally:
        memory.clear_history()


def test_provider_error_response_never_contains_key_or_stack(client, monkeypatch):
    from jarvis.brain import gemini as gemini_mod
    from jarvis.memory import manager as memory

    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)

    def boom(messages, timeout=None):
        raise gemini_mod.GeminiError("quota", "Gemini rate limit or quota exceeded.")

    _patch_llm(monkeypatch, boom)
    memory.clear_history()
    try:
        r = client.post("/api/command", json={"text": "hello provider", "source": "text"})
        assert r.status_code == 200
        assert FAKE_KEY not in r.text
        assert "Traceback" not in r.text
        assert (r.json().get("reply") or "").strip()
        hist = client.get("/api/history?limit=5")
        assert FAKE_KEY not in hist.text
    finally:
        memory.clear_history()


# ---------------------------------------------------------------------------
# dropped WebSocket → recovery with boot replay + live delivery
# ---------------------------------------------------------------------------
def test_dropped_ws_recovers_replay_and_live_events(client, monkeypatch):
    from jarvis.memory import manager as memory

    _patch_llm(monkeypatch, lambda messages, timeout=None: "link recovered reply")
    memory.clear_history()
    try:
        # 1) healthy link: hello + boot replay present
        with client.websocket_connect("/ws") as ws:
            types = []
            for _ in range(60):
                msg = ws.receive_json()
                types.append(msg["type"])
                if msg["type"] == "boot_done":
                    break
            assert types[0] == "hello", types
            assert "boot_done" in types, types

        # 2) link is GONE — the command still works (bounded HTTP reply)
        r = client.post(
            "/api/command", json={"text": "while the link was down", "source": "text"}
        )
        assert r.status_code == 200
        assert (r.json().get("reply") or "").strip(), (
            "chat must not depend on a live WebSocket"
        )

        # 3) reconnect: boot replay again (the HUD's recovery handshake)…
        with client.websocket_connect("/ws") as ws2:
            types2 = []
            for _ in range(60):
                msg = ws2.receive_json()
                types2.append(msg["type"])
                if msg["type"] == "boot_done":
                    break
            assert types2[0] == "hello", types2
            assert "boot_done" in types2, types2

            # 4) …and events flow LIVE on the new socket after recovery
            r2 = client.post(
                "/api/command", json={"text": "turn after recovery", "source": "text"}
            )
            assert r2.status_code == 200
            got_reply = False
            for _ in range(60):
                msg = ws2.receive_json()
                if (
                    msg.get("type") == "conversation"
                    and (msg.get("data") or {}).get("role") == "jarvis"
                ):
                    got_reply = True
                    break
            assert got_reply, "no live conversation event after WS recovery"
    finally:
        memory.clear_history()


# ---------------------------------------------------------------------------
# API key leakage
# ---------------------------------------------------------------------------
def test_api_key_never_leaks_over_http_ws_or_history(client, monkeypatch):
    from jarvis.backend import server as server_mod
    from jarvis.memory import manager as memory

    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    monkeypatch.setattr(server_mod, "_llm_probe", lambda: (True, ["gemini-3.5-flash-lite"]))
    _patch_llm(monkeypatch, lambda messages, timeout=None: "safe reply for leak test")
    memory.clear_history()
    try:
        status = client.get("/api/status")
        assert FAKE_KEY not in status.text

        cmd = client.post("/api/command", json={"text": "leak canary turn", "source": "text"})
        assert FAKE_KEY not in cmd.text

        hist = client.get("/api/history?limit=10")
        assert FAKE_KEY not in hist.text

        frames: list[str] = []
        with client.websocket_connect("/ws") as ws:
            for _ in range(60):
                msg = ws.receive_json()
                frames.append(str(msg))
                if msg.get("type") == "boot_done":
                    break
        assert frames and any("hello" in f for f in frames)
        for f in frames:
            assert FAKE_KEY not in f, f"key leaked over WS: {f[:200]}"
    finally:
        memory.clear_history()


# ---------------------------------------------------------------------------
# planner regression ("X and then Y" → NameError before the fix)
# ---------------------------------------------------------------------------
def test_multi_step_planner_command_completes_without_nameerror(client, monkeypatch):
    from jarvis.memory import manager as memory

    _patch_llm(monkeypatch, lambda messages, timeout=None: "step done")
    memory.clear_history()
    try:
        r = client.post(
            "/api/command",
            json={"text": "check the ram and then say done", "source": "text"},
        )
        assert r.status_code == 200
        body = r.json()
        reply = str(body.get("reply") or "")
        assert reply.strip(), body
        assert "name 'memory'" not in reply, f"planner NameError returned to user: {reply}"
        assert "Traceback" not in reply
    finally:
        memory.clear_history()
