"""Deployment-shape tests for the Vercel `api` service.

These guard the root cause of the 2026-10-11 black-screen incident: in the
deployed function the bundle contains ONLY the service root's contents, so
vercel_entry.py sits at the bundle root and its old ``parent.parent`` sys.path
shim raised ``ModuleNotFoundError: No module named 'jarvis'`` — every
invocation returned 500 FUNCTION_INVOCATION_FAILED and the HUD boot screen
never received its ``boot_done`` event.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

JARVIS_DIR = Path(__file__).resolve().parent.parent  # the service root (jarvis/)

# Directories that make up the importable `jarvis` package in the bundle
# (mirrors what survives excludeFiles in vercel.json).
_PACKAGE_DIRS = ("backend", "brain", "config", "memory", "security", "tools", "voice")


def test_vercel_entry_imports_in_repo_layout() -> None:
    """The entrypoint must import in the ordinary repo checkout."""
    sys.path.insert(0, str(JARVIS_DIR))
    try:
        mod = importlib.import_module("vercel_entry")
    finally:
        sys.path.remove(str(JARVIS_DIR))
    assert mod.app.title == "JARVIS"
    routes = {getattr(r, "path", None) for r in mod.app.routes}
    assert "/ws" in routes
    assert "/api/status" in routes


def test_vercel_entry_imports_in_flattened_bundle(tmp_path) -> None:
    """Simulate the deployed Vercel bundle: only the service root's contents.

    vercel_entry.py ends up at the bundle root with no `jarvis` directory
    above it. Importing it must still work (this exact scenario produced
    FUNCTION_INVOCATION_FAILED in production).
    """
    for d in _PACKAGE_DIRS:
        shutil.copytree(JARVIS_DIR / d, tmp_path / d,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(JARVIS_DIR / "vercel_entry.py", tmp_path / "vercel_entry.py")

    env = dict(os.environ)
    env["JARVIS_DATA_DIR"] = str(tmp_path / "data")
    env["JARVIS_LOG_DIR"] = str(tmp_path / "logs")
    env["JARVIS_DISABLE_DEFAULT_PROTECTED"] = "1"

    code = (
        "import vercel_entry\n"
        "app = vercel_entry.app\n"
        "routes = {getattr(r, 'path', None) for r in app.routes}\n"
        "assert app.title == 'JARVIS', app.title\n"
        "assert '/ws' in routes, routes\n"
        "assert '/api/status' in routes, routes\n"
        "print('FLATTENED-OK')\n"
    )
    p = subprocess.run([sys.executable, "-c", code], cwd=str(tmp_path),
                       env=env, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, f"stdout={p.stdout}\nstderr={p.stderr}"
    assert "FLATTENED-OK" in p.stdout


def test_settings_survives_unwritable_data_dir(tmp_path) -> None:
    """A read-only/uncreatable data dir must not crash settings at import.

    On serverless filesystems (everything except /tmp is read-only) the old
    ``mkdir`` in Settings.load raised OSError and killed the whole function.
    """
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"")  # an existing FILE: mkdir under it must fail
    from jarvis.config.settings import Settings

    s = Settings(path=blocker / "sub" / "settings.json")
    # defaults are loaded and usable in memory
    assert s.get("server.port") == 8765
    # saving must also fail loudly-but-safely (no exception escapes)
    s.set("general.assistant_name", "JARVIS-TEST")


def test_database_falls_back_to_memory_when_dir_unwritable(tmp_path) -> None:
    """An uncreatable DB dir must degrade to in-memory, not 500.

    Production observed: ``GET /api/history`` returned 500 because
    ``sqlite3.connect`` on a read-only serverless filesystem raised.
    """
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"")  # an existing FILE: mkdir under it must fail
    from jarvis.memory.database import Database
    from jarvis.memory.schemas import ConversationEntry

    db = Database(db_path=blocker / "sub" / "jarvis.db")
    assert db._persistent is False
    cid = db.add_conversation(ConversationEntry(
        role="user", content="hello in-memory", ts=1.0, source="text",
    ))
    assert cid >= 1
    entries = db.get_conversations(limit=10)
    assert any(e.content == "hello in-memory" for e in entries)
    db.close()


def test_database_still_persists_when_dir_writable(tmp_path) -> None:
    """The in-memory fallback must not affect the normal writable case."""
    from jarvis.memory.database import Database

    db = Database(db_path=tmp_path / "jarvis.db")
    assert db._persistent is True
    db.close()


# ---------------------------------------------------------------------------
# boot_done delivery over WebSocket (2026-10-11 follow-up incident)
#
# The entrypoint fix restored HTTP, but the HUD still reported the backend
# offline: the boot sequence emits `boot_done` EXACTLY ONCE at startup, and
# with serverless cold starts the WebSocket frequently attaches to an
# instance whose boot already finished — zero subscribers, event lost, HUD
# waits forever. The bus must replay boot lifecycle events to late
# subscribers.
# ---------------------------------------------------------------------------
def _drain(q: asyncio.Queue) -> list:
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except asyncio.QueueEmpty:
            return out


def test_boot_events_replayed_to_late_subscriber() -> None:
    """A subscriber attaching AFTER boot completed must still see boot_done."""
    from jarvis.backend.events.bus import EventBus

    bus = EventBus()
    bus.emit("boot", {"total": 1})
    bus.emit("boot_step", {"name": "SECURITY", "ok": True})
    bus.emit("boot_done", {})

    q = bus.subscribe()  # late subscriber — the serverless WS case
    types = [e["type"] for e in _drain(q)]
    assert types == ["boot", "boot_step", "boot_done"]


def test_live_subscriber_gets_no_duplicates() -> None:
    """A subscriber attached BEFORE boot must not receive events twice."""
    from jarvis.backend.events.bus import EventBus

    bus = EventBus()
    live = bus.subscribe()
    bus.emit("boot_done", {})
    assert [e["type"] for e in _drain(live)] == ["boot_done"]

    late = bus.subscribe()
    assert [e["type"] for e in _drain(late)] == ["boot_done"]


def test_boot_history_is_bounded() -> None:
    """Replay history must stay bounded (no unbounded serverless memory use)."""
    from jarvis.backend.events.bus import EventBus, _BOOT_HISTORY_LIMIT

    bus = EventBus()
    for i in range(_BOOT_HISTORY_LIMIT * 3):
        bus.emit("boot_step", {"name": f"S{i}", "ok": True})
    assert len(bus._boot_history) <= _BOOT_HISTORY_LIMIT
    q = bus.subscribe()
    assert len(_drain(q)) <= _BOOT_HISTORY_LIMIT


def test_non_boot_events_are_not_replayed() -> None:
    """Only boot lifecycle events are retained; live traffic stays live-only."""
    from jarvis.backend.events.bus import EventBus

    bus = EventBus()
    bus.emit("state", {"state": "idle"})
    bus.emit("system_stats", {"cpu": 1})
    assert _drain(bus.subscribe()) == []


def test_ws_receives_boot_done_after_startup() -> None:
    """End-to-end (production shape): REST first, then a fresh WebSocket.

    Startup (which emits boot_done) runs before the WS connects — exactly the
    deployed cold-start sequence that left the HUD waiting forever.
    """
    from fastapi.testclient import TestClient
    from jarvis.backend.server import app

    with TestClient(app) as client:
        # REST works (the probe that already succeeded in production)
        assert client.get("/api/status").status_code == 200
        with client.websocket_connect("/ws") as ws:
            types = []
            for _ in range(50):  # bounded: hello + boot replay + live chatter
                msg = ws.receive_json()
                types.append(msg["type"])
                if msg["type"] == "boot_done":
                    break
            assert "boot_done" in types, f"boot_done missing; got {types}"
            assert types[0] == "hello", types
