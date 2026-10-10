"""JARVIS backend server.

FastAPI app serving:
- the built React HUD (static files)
- REST endpoints (settings, status, history, memory, tools, confirmations)
- WebSocket /ws for real-time events (state, conversation, tool calls, tasks)

All heavy work (STT, LLM, tools) runs off the event loop; the UI stays fluid.
"""
from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Logging setup (before other jarvis imports)
# ---------------------------------------------------------------------------
from jarvis.config.settings import LOG_FILE, LOG_DIR, PROJECT_ROOT, get_settings

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("jarvis.server")

FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

app = FastAPI(title="JARVIS", version="1.0.0")


# ---------------------------------------------------------------------------
# Bootstrap: singletons
# ---------------------------------------------------------------------------
from jarvis.backend import events as ev
from jarvis.backend.events import get_event_bus
from jarvis.backend.planner import TaskPlanner
from jarvis.backend.state import snapshot as state_snapshot
from jarvis.backend.state import update as state_update
from jarvis.brain import llm as llm_mod
from jarvis.brain.agent import JarvisAgent
from jarvis.memory import manager as memory
from jarvis.security.confirmation import get_confirmation_manager
from jarvis.tools.tool_registry import get_registry
from jarvis.voice import text_to_speech as tts
from jarvis.voice.listener import VoiceListener

bus = get_event_bus()
settings = get_settings()
agent: Optional[JarvisAgent] = None
planner: Optional[TaskPlanner] = None
listener: Optional[VoiceListener] = None
confirmations = get_confirmation_manager()


@app.on_event("startup")
async def _startup() -> None:
    global agent, planner, listener
    bus.set_loop(asyncio.get_running_loop())
    confirmations.set_loop(asyncio.get_running_loop())

    agent = JarvisAgent()
    planner = TaskPlanner(agent)
    agent.emit_fn = _emit_from_agent
    state_update(app_state="idle", started_at=time.time())
    log.info("JARVIS backend starting; frontend at %s", FRONTEND_DIST)

    # voice pipeline (non-fatal if mic missing)
    def _on_command(text: str, source: str) -> None:
        _submit_user_command(text, source)

    listener = VoiceListener(on_command=_on_command, on_event=_emit_from_agent)
    threading.Thread(target=_start_voice_safely, daemon=True).start()

    # warm up TTS + whisper in background
    threading.Thread(target=tts.warmup, daemon=True).start()

    # system stats broadcaster
    asyncio.create_task(_stats_loop())

    # startup sequence events for the HUD boot screen
    await _boot_sequence()

    # spoken greeting
    if settings.get("tts.speak_startup", True):
        speak_text("All systems are online.")


def _start_voice_safely() -> None:
    try:
        if listener and not listener.start():
            log.warning("voice listener unavailable: %s", listener.last_error)
    except Exception:  # noqa: BLE001
        log.exception("voice listener crashed at startup")


async def _boot_sequence() -> None:
    steps = [
        ("VOICE ENGINE", listener.running if listener else False),
        ("AI ENGINE", llm_mod.is_online()),
        ("MEMORY", True),
        ("TOOL SYSTEM", True),
        ("SECURITY", True),
    ]
    bus.emit("boot", {"total": len(steps)})
    for name, ok in steps:
        await asyncio.sleep(0.25)
        bus.emit("boot_step", {"name": name, "ok": bool(ok)})
    bus.emit("boot_done", {})


def _emit_from_agent(event_type: str, data: Dict[str, Any]) -> None:
    """Bridge agent/voice events onto the bus (thread-safe)."""
    bus.emit_threadsafe(event_type, data)
    if event_type == "confirmation_request":
        # Spoken announcement of a pending approval (Phase 9).
        title = str(data.get("title", "this action")).replace("/", " or ")

        def _announce() -> None:
            time.sleep(0.5)  # let the confirmation state reach the HUD first
            speak_text(f"This action requires your approval: {title}. "
                       f"Approve or cancel when ready.")

        threading.Thread(target=_announce, daemon=True, name="confirm-announce").start()


# ---------------------------------------------------------------------------
# Stats loop (real system metrics to the HUD)
# ---------------------------------------------------------------------------
async def _stats_loop() -> None:
    from jarvis.tools import system as sys_tools
    try:
        while True:
            try:
                cpu = sys_tools.get_cpu_usage()["result"]["percent"]
                mem = sys_tools.get_memory_usage()["result"]
                batt = sys_tools.get_battery_status()["result"]
                net = sys_tools.get_network_status()["result"]
                bus.emit("system_stats", {
                    "cpu": cpu,
                    "ram_percent": mem["percent"],
                    "ram_used_gb": mem["used_gb"],
                    "ram_total_gb": mem["total_gb"],
                    "battery": batt,
                    "internet": net["internet"],
                })
            except Exception:  # noqa: BLE001
                log.exception("stats loop error")
            await asyncio.sleep(2)
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# Command processing (shared by REST + WebSocket + voice)
# ---------------------------------------------------------------------------
_APPROVE_RE = re.compile(r"\b(approve|approved|approve it|confirm|confirmed|yes|yes please|go ahead|do it)\b", re.I)
_DENY_RE = re.compile(r"\b(deny|denied|cancel|cancelled|canceled|reject|abort|no|don't|do not|stop)\b", re.I)


def _submit_user_command(text: str, source: str = "text") -> None:
    """Run a user command through planner/agent on a worker thread."""
    if not text.strip():
        return

    # Voice/UI answers to a pending confirmation bypass the agent's busy lock.
    # Only EXPLICIT approve/deny words resolve a confirmation — never silence
    # or unrelated speech (no auto-approval of dangerous actions).
    if confirmations.pending():
        if _APPROVE_RE.search(text):
            if confirmations.resolve_latest(True):
                bus.emit_threadsafe("confirmation_resolved", {"approved": True})
                return
        elif _DENY_RE.search(text):
            if confirmations.resolve_latest(False):
                bus.emit_threadsafe("confirmation_resolved", {"approved": False})
                return

    def _work() -> None:
        assert planner is not None and agent is not None
        try:
            if agent.cancel_flag.cancelled:
                agent.reset_cancel()
            if planner.needs_planning(text):
                out = planner.execute_plan(text, source)
            else:
                out = agent.process_text(text, source)
            reply = out.get("reply", "")
            if reply:
                speak_text(reply)
        except Exception:  # noqa: BLE001
            log.exception("command processing failed")
            speak_text("Something went wrong while processing that.")

    threading.Thread(target=_work, daemon=True, name="agent-work").start()  # daemon


def speak_text(text: str) -> None:
    if not text:
        return
    bus.emit_threadsafe("state", {"state": "speaking"})
    bus.emit_threadsafe("conversation", {"role": "jarvis", "content": text, "source": "tts"})

    def _after() -> None:
        # Do not stomp a newer pipeline state (e.g. a pending confirmation).
        if agent is not None and confirmations.pending():
            bus.emit_threadsafe("state", {"state": "confirmation"})
            return
        bus.emit_threadsafe("state", {"state": "idle"})

    def _speak() -> None:
        try:
            ok = tts.speak(text, wait=True)
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            bus.emit_threadsafe("error", {"message": "Voice output unavailable"})
        _after()

    threading.Thread(target=_speak, daemon=True, name="tts-speak").start()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class CommandIn(BaseModel):
    text: str
    source: str = "text"


class SettingsIn(BaseModel):
    patch: Dict[str, Any]


class ConfirmIn(BaseModel):
    id: Optional[str] = None
    approved: bool


class MemoryIn(BaseModel):
    category: str = "fact"
    key: str
    value: str


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "state": state_snapshot()})


@app.get("/api/status")
async def status() -> JSONResponse:
    assert agent is not None
    return JSONResponse({
        "state": state_snapshot(),
        "llm": {
            "online": llm_mod.is_online(),
            "models": llm_mod.list_models()[:30],
            "configured": settings.get("ai.model"),
            "runtime": settings.get("ai.runtime"),
        },
        "voice": {
            "listening": bool(listener and listener.running),
            "devices": _voice_devices(),
            "mode": settings.get("voice.mode"),
            "stt": _stt_info(),
            "last_error": listener.last_error if listener else "",
        },
        "tts": tts.available_engines(),
        "tts_voices": tts.list_voices(),
        "browser": {"playwright": _browser_available()},
        "pending_confirmations": confirmations.pending(),
    })


def _voice_devices() -> List[Dict[str, Any]]:
    """Cached device list only — never query PortAudio on a request path."""
    try:
        from jarvis.voice import audio as audio_mod
        return list(audio_mod._devices_cache or [])
    except Exception:  # noqa: BLE001
        return []


def _stt_info() -> Dict[str, Any]:
    try:
        from jarvis.voice import speech_to_text
        return speech_to_text.model_info()
    except Exception:  # noqa: BLE001
        return {"engine": "unavailable", "available": False}


def _browser_available() -> bool:
    try:
        from jarvis.tools.browser import playwright_available
        return playwright_available()
    except Exception:  # noqa: BLE001
        return False


@app.post("/api/command")
async def api_command(cmd: CommandIn) -> JSONResponse:
    _submit_user_command(cmd.text, cmd.source)
    return JSONResponse({"accepted": True})


@app.post("/api/confirm")
async def api_confirm(payload: ConfirmIn) -> JSONResponse:
    ok = False
    if payload.id:
        ok = confirmations.resolve(payload.id, payload.approved)
    else:
        ok = confirmations.resolve_latest(payload.approved)
    if ok:
        bus.emit("confirmation_resolved", {"approved": payload.approved})
    return JSONResponse({"resolved": ok})


@app.get("/api/history")
async def api_history(limit: int = 100) -> JSONResponse:
    return JSONResponse({"entries": memory.get_history(limit)})


@app.delete("/api/history")
async def api_clear_history() -> JSONResponse:
    n = memory.clear_history()
    bus.emit("history_cleared", {})
    return JSONResponse({"cleared": n})


@app.get("/api/memory")
async def api_memory(query: str = "", category: str = "") -> JSONResponse:
    items = memory.retrieve_memory(category=category or None, query=query)
    return JSONResponse({"items": items})


@app.post("/api/memory")
async def api_memory_add(item: MemoryIn) -> JSONResponse:
    saved = memory.save_memory(item.category, item.key, item.value)
    return JSONResponse({"saved": saved})


@app.delete("/api/memory/{item_id}")
async def api_memory_delete(item_id: int) -> JSONResponse:
    ok = memory.delete_memory(item_id)
    return JSONResponse({"deleted": ok})


@app.post("/api/memory/clear")
async def api_memory_clear() -> JSONResponse:
    n = memory.clear_memory()
    return JSONResponse({"cleared": n})


@app.get("/api/settings")
async def api_settings_get() -> JSONResponse:
    return JSONResponse({"settings": settings.as_dict()})


@app.post("/api/settings")
async def api_settings_set(payload: SettingsIn) -> JSONResponse:
    settings.update(payload.patch)
    if listener:
        listener.apply_settings()
    bus.emit("settings_updated", {"keys": list(payload.patch.keys())})
    return JSONResponse({"ok": True})


@app.get("/api/tools")
async def api_tools() -> JSONResponse:
    assert agent is not None
    return JSONResponse({"tools": agent.registry.schemas()})


@app.get("/api/tasks")
async def api_tasks() -> JSONResponse:
    return JSONResponse({"tasks": memory.recent_tasks(20)})


@app.post("/api/stop")
async def api_stop() -> JSONResponse:
    assert agent is not None
    from jarvis.tools import terminal as terminal_tools
    agent.cancel_current()
    confirmations.cancel_all()
    if listener is not None:
        listener.discard_ptt()
    try:
        terminal_tools.stop_managed_server()
    except Exception:  # noqa: BLE001
        log.exception("failed stopping managed servers")
    tts.stop_speaking()
    bus.emit("task_stopped", {"reason": "stop button"})
    bus.emit("state", {"state": "idle", "message": "TASK STOPPED"})
    return JSONResponse({"stopped": True})


@app.post("/api/ptt/start")
async def api_ptt_start() -> JSONResponse:
    if not listener or not listener.running:
        return JSONResponse({"ok": False, "error": "Microphone unavailable"}, status_code=503)
    listener.start_ptt()
    return JSONResponse({"ok": True})


@app.post("/api/ptt/end")
async def api_ptt_end() -> JSONResponse:
    if not listener:
        return JSONResponse({"ok": False, "error": "Voice not running"}, status_code=503)
    listener.end_ptt()
    return JSONResponse({"ok": True})


@app.post("/api/voice/test")
async def api_voice_test() -> JSONResponse:
    """Measure live mic level for ~1.5 s (runs on a worker thread)."""
    if not listener or not listener.running:
        return JSONResponse(
            {"success": False, "detected": False, "level": 0.0,
             "error": listener.last_error if listener else "Voice engine not running"},
            status_code=503)
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, listener.test_mic)
    return JSONResponse(result)


@app.post("/api/tts/test")
async def api_tts_test() -> JSONResponse:
    """Speak a short test line; reports honest success/failure."""
    loop = asyncio.get_running_loop()
    ok = await loop.run_in_executor(None, tts.speak, "Voice output test.", True)
    return JSONResponse({"success": bool(ok),
                         "error": None if ok else "No speech engine available"})


@app.get("/api/wakeword/status")
async def api_wakeword_status() -> JSONResponse:
    """Honest wake-word capability: transcription-based matcher is built in."""
    stt_ok = _stt_info().get("available", False)
    return JSONResponse({
        "available": bool(stt_ok),
        "engine": "transcription-fuzzy",
        "note": "" if stt_ok else "Requires faster-whisper (pip install faster-whisper)",
    })


@app.post("/api/voice/mode")
async def api_voice_mode(mode: str) -> JSONResponse:
    settings.set("voice.mode", mode)
    bus.emit("voice_state", {"mode": mode})
    return JSONResponse({"ok": True, "mode": mode})


@app.get("/api/logs")
async def api_logs(tail: int = 200) -> JSONResponse:
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        return JSONResponse({"lines": [l.rstrip() for l in lines[-tail:]]})
    except OSError as exc:
        return JSONResponse({"lines": [f"log read failed: {exc}"]})


@app.post("/api/shutdown")
async def api_shutdown() -> JSONResponse:
    bus.emit("state", {"state": "idle"})
    import os
    os._exit(0)


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    queue = bus.subscribe()
    try:
        # initial snapshot
        await ws.send_json({"type": "hello", "data": {"state": state_snapshot()}})
        while True:
            # broadcast queued events
            try:
                event = queue.get_nowait()
                await ws.send_json(event)
                continue
            except asyncio.QueueEmpty:
                pass
            # receive client messages with short timeout
            try:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=0.1)
            except asyncio.TimeoutError:
                await asyncio.sleep(0.05)
                continue
            await _handle_ws_message(ws, msg)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("websocket error")
    finally:
        bus.unsubscribe(queue)


async def _handle_ws_message(ws: WebSocket, msg: Dict[str, Any]) -> None:
    mtype = msg.get("type")
    if mtype == "ping":
        await ws.send_json({"type": "pong", "data": {}})
    elif mtype == "command":
        text = str(msg.get("text", "")).strip()
        _submit_user_command(text, "text")
    elif mtype == "confirm":
        confirmations.resolve(str(msg.get("id", "")), bool(msg.get("approved", False)))
    elif mtype == "ptt":
        if msg.get("action") == "start":
            if listener and listener.running:
                listener.start_ptt()
        else:
            if listener:
                listener.end_ptt()
    elif mtype == "stop":
        await api_stop()


# ---------------------------------------------------------------------------
# Static frontend (built React app)
# ---------------------------------------------------------------------------
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="hud")
else:
    @app.get("/")
    async def _no_frontend() -> JSONResponse:
        return JSONResponse({
            "error": "Frontend not built. Run: cd frontend && npm install && npm run build",
        }, status_code=404)


def main() -> None:
    import uvicorn
    host = settings.get("server.host", "127.0.0.1")
    port = int(settings.get("server.port", 8765))
    log.info("starting uvicorn on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
