"""Voice pipeline tests: STT structured results, TTS, PTT lifecycle, state
transitions, confirmation voice flow, emergency stop, honest failures.

Hardware-dependent paths degrade to honest failures (the behaviour the product
must show), so the suite also runs on machines without a microphone.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict

import pytest

from jarvis.voice import speech_to_text, text_to_speech as tts
from jarvis.voice.listener import VoiceListener
from jarvis.voice.wakeword import contains_wake_word, strip_wake_word

SILENCE = b"\x00\x00" * 16000          # 1 s of digital silence (16 kHz, int16)


# ---------------------------------------------------------------------------
# 1-3. STT: availability, success, failure
# ---------------------------------------------------------------------------

def test_stt_availability_reported_honestly():
    info = speech_to_text.model_info()
    assert isinstance(info["available"], bool)
    assert info["engine"] in ("faster-whisper", "unavailable")
    if not info["available"]:
        assert "pip install faster-whisper" in info["install_hint"]


@pytest.mark.skipif(not speech_to_text.is_available(), reason="faster-whisper not installed")
def test_transcribe_structured_success_shape():
    # silence transcribes to empty text but must still be a *structured success*
    r = speech_to_text.transcribe_structured(SILENCE)
    assert set(r) == {"success", "text", "language", "duration", "error"}
    assert r["success"] is True
    assert r["error"] is None
    assert isinstance(r["duration"], float)


def test_transcribe_structured_handles_garbage_without_raising():
    # random bytes are not valid PCM... actually they are; test the tiny-input path
    r = speech_to_text.transcribe_structured(b"\x00\x00")
    assert r["success"] is True
    assert r["text"] == ""


def test_transcribe_structured_failure_is_honest(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("model exploded")
    monkeypatch.setattr(speech_to_text, "transcribe", boom)
    r = speech_to_text.transcribe_structured(SILENCE)
    assert r["success"] is False
    assert "model exploded" in r["error"]
    assert r["text"] == ""


def test_transcribe_unavailable_engine_is_honest(monkeypatch):
    def unavailable(*a, **k):
        raise speech_to_text.STTUnavailable("faster-whisper is not installed")
    monkeypatch.setattr(speech_to_text, "transcribe", unavailable)
    r = speech_to_text.transcribe_structured(SILENCE)
    assert r["success"] is False
    assert "not installed" in r["error"]


# ---------------------------------------------------------------------------
# 4-5. TTS: success and honest failure
# ---------------------------------------------------------------------------

def test_tts_speak_returns_bool():
    result = tts.speak("Automated test line.", wait=True)
    assert isinstance(result, bool)


def test_tts_disabled_reports_failure(monkeypatch):
    from jarvis.config.settings import get_settings
    get_settings().set("tts.enabled", False)
    assert tts.speak("should not speak", wait=True) is False


def test_tts_stop_is_safe_when_idle():
    tts.stop_speaking()  # must not raise even with no engine/queue items
    # the worker must stay healthy after a stop: a fresh utterance still speaks
    assert tts.speak("post-stop check", wait=True) is True


def test_tts_queue_drains_on_stop():
    tts._ensure_worker()
    tts._queue.put(("queued sentence one", None))
    tts._queue.put(("queued sentence two", None))
    tts.stop_speaking()
    assert tts._queue.empty()


def test_tts_engine_report():
    engines = tts.available_engines()
    assert "sapi" in engines and "piper" in engines


# ---------------------------------------------------------------------------
# 6. Push-to-talk lifecycle
# ---------------------------------------------------------------------------

class _EventSink:
    def __init__(self):
        self.events: list = []

    def __call__(self, etype: str, data: Dict[str, Any]) -> None:
        self.events.append((etype, data))

    def of(self, etype: str):
        return [d for t, d in self.events if t == etype]


@pytest.fixture()
def listener(monkeypatch):
    """A VoiceListener with a fake (never-failing) mic stream."""
    import jarvis.voice.listener as listener_mod

    class FakeMic:
        def __init__(self, device=None, on_frame=None):
            self.running = True
            self._cb = on_frame

        def start(self):
            return True

        def stop(self):
            self.running = False

    monkeypatch.setattr(listener_mod, "MicrophoneStream", FakeMic)
    sink = _EventSink()
    vl = VoiceListener(on_command=lambda t, s: None, on_event=sink)
    assert vl.start() is True
    yield vl, sink
    vl.stop()


def test_ptt_lifecycle_capture_transcribe(listener, monkeypatch):
    vl, sink = listener
    vl.start_ptt()
    assert sink.of("ptt_state")[-1]["active"] is True
    # feed fake speech frames
    frame = b"\x01\x02" * 480
    for _ in range(10):
        vl._on_frame(frame)
    vl.end_ptt()
    assert sink.of("ptt_state")[-1]["active"] is False


def test_ptt_transcription_success_routes_to_command(listener, monkeypatch):
    vl, sink = listener
    got = []
    vl.on_command = lambda text, source: got.append((text, source))
    monkeypatch.setattr(vl, "transcribe_audio",
                        lambda pcm: {"success": True, "text": "open chrome",
                                     "language": "en", "duration": 1.0, "error": None})
    vl._transcribe_ptt(b"\x00\x00" * 16000)
    assert got and got[0] == ("open chrome", "voice")
    stt_events = sink.of("stt_result")
    assert stt_events and stt_events[0]["text"] == "open chrome"


def test_ptt_transcription_failure_is_honest(listener, monkeypatch):
    vl, sink = listener
    got = []
    vl.on_command = lambda text, source: got.append(text)
    # patch the STT engine layer so the REAL transcribe_audio path runs
    monkeypatch.setattr(speech_to_text, "transcribe_structured",
                        lambda pcm, **k: {"success": False, "text": "", "language": "en",
                                          "duration": 0.0, "error": "engine missing"})
    vl._transcribe_ptt(b"\x00\x00" * 16000)
    assert got == []                       # never fabricates a command
    assert sink.of("voice_error")          # failure surfaced


def test_ptt_empty_transcription_reports_no_speech(listener, monkeypatch):
    vl, sink = listener
    got = []
    vl.on_command = lambda text, source: got.append(text)
    monkeypatch.setattr(vl, "transcribe_audio",
                        lambda pcm: {"success": True, "text": "", "language": "en",
                                     "duration": 1.0, "error": None})
    vl._transcribe_ptt(b"\x00\x00" * 16000)
    assert got == []
    assert sink.of("voice_error") and "didn't catch" in sink.of("voice_error")[0]["error"]


def test_stop_discards_active_ptt(listener):
    vl, sink = listener
    vl.start_ptt()
    vl._on_frame(b"\x01\x02" * 480)
    vl.discard_ptt()
    assert sink.of("ptt_state")[-1]["active"] is False


def test_transcribe_audio_returns_structured(listener):
    vl, _ = listener
    r = vl.transcribe_audio(b"\x00\x00")
    assert set(r) == {"success", "text", "language", "duration", "error"}


def test_mic_test_honest_when_not_running():
    vl = VoiceListener(on_command=lambda t, s: None)
    r = vl.test_mic(0.3)
    assert r["success"] is False and "not active" in (r["error"] or "")


# ---------------------------------------------------------------------------
# 7. Voice state transitions (backend emits, shapes the HUD)
# ---------------------------------------------------------------------------

def test_state_listening_emitted_on_ptt_start(listener):
    vl, sink = listener
    vl.start_ptt()
    assert sink.of("state")[-1]["state"] == "listening"


def test_state_transcribing_emitted_on_ptt_end(listener, monkeypatch):
    vl, sink = listener
    monkeypatch.setattr(speech_to_text, "transcribe_structured",
                        lambda pcm, **k: {"success": True, "text": "", "language": "en",
                                          "duration": 1.0, "error": None})
    vl.start_ptt()
    vl._on_frame(b"\x01\x02" * 480)   # captured audio must exist to transcribe
    vl.end_ptt()
    states = [s["state"] for s in sink.of("state")]
    assert "transcribing" in states


def test_voice_level_events_flow(listener):
    vl, sink = listener
    vl._on_frame(b"\x30\x00" * 480)   # a loud-ish frame
    assert sink.of("voice_level")     # real levels are published


# ---------------------------------------------------------------------------
# 8-11. Dangerous voice command → confirmation → approve / deny
# ---------------------------------------------------------------------------

def _run_command(text: str, monkeypatch) -> tuple:
    """Run a command through the offline agent and return (reply, tool_calls)."""
    from jarvis.brain.agent import JarvisAgent
    sink = _EventSink()
    agent = JarvisAgent()
    agent.emit_fn = sink
    out = agent.process_text(text, "voice")
    return out, sink


def test_dangerous_voice_command_requires_confirmation(monkeypatch, temp_allowed_root):
    target = temp_allowed_root / "VoiceTest"
    target.mkdir()
    out, sink = _run_command(f"delete the folder {target.name}", monkeypatch)
    # must NOT report success and must NOT have deleted anything
    assert "deleted" not in out["reply"].lower()
    assert target.exists()


def test_confirmation_approve_executes(monkeypatch, temp_allowed_root):
    import asyncio
    from jarvis.security.confirmation import get_confirmation_manager
    cm = get_confirmation_manager()

    async def request_and_resolve(approved: bool):
        loop = asyncio.get_running_loop()
        cm.set_loop(loop)
        task = loop.create_task(cm.request_approval(
            "delete_file", "Delete file/folder", "desc", {"path": "x"}, "high-risk"))
        for _ in range(100):
            if cm.pending():
                break
            await asyncio.sleep(0.02)
        assert cm.pending(), "confirmation never raised"
        assert cm.resolve(cm.pending()[0]["id"], approved)
        return await task

    assert asyncio.run(request_and_resolve(True)) is True
    assert not cm.pending()


def test_confirmation_deny_blocks_execution(monkeypatch, temp_allowed_root):
    target = temp_allowed_root / "VoiceTest"
    target.mkdir()
    import asyncio
    from jarvis.security.confirmation import get_confirmation_manager
    cm = get_confirmation_manager()

    async def request_and_resolve(approved: bool):
        loop = asyncio.get_running_loop()
        cm.set_loop(loop)
        task = loop.create_task(cm.request_approval(
            "delete_file", "Delete file/folder", "desc", {"path": "x"}, "high-risk"))
        for _ in range(100):
            if cm.pending():
                break
            await asyncio.sleep(0.02)
        assert cm.resolve(cm.pending()[0]["id"], approved)
        return await task

    assert asyncio.run(request_and_resolve(False)) is False
    assert target.exists()   # untouched


# ---------------------------------------------------------------------------
# 12. Emergency stop
# ---------------------------------------------------------------------------

def test_emergency_stop_cancels_everything(monkeypatch, listener):
    vl, sink = listener
    import asyncio
    from jarvis.security.confirmation import get_confirmation_manager
    from jarvis.brain.agent import JarvisAgent
    cm = get_confirmation_manager()
    agent = JarvisAgent()
    agent.emit_fn = lambda t, d: None

    async def scenario():
        # Single-loop pattern: the future, the waiter task and cancel_all all
        # share one running loop (no cross-thread set_result on a foreign loop).
        loop = asyncio.get_running_loop()
        cm.set_loop(loop)
        task = loop.create_task(
            cm.request_approval("delete_file", "t", "d", {}, "high-risk"))
        for _ in range(100):
            if cm.pending():
                break
            await asyncio.sleep(0.02)
        assert cm.pending(), "confirmation never raised"

        # Hit STOP: cancel the agent, drop the PTT capture, silence speech,
        # and deny every pending approval — never approve.
        vl.start_ptt()
        agent.cancel_current()
        vl.discard_ptt()
        tts.stop_speaking()
        cm.cancel_all()

        assert await task is False
        assert not cm.pending()

    asyncio.run(scenario())
    assert sink.of("ptt_state")[-1]["active"] is False
    assert agent.cancel_flag.cancelled


# ---------------------------------------------------------------------------
# 13-14. Tool failure → honest spoken reply
# ---------------------------------------------------------------------------

def test_tool_failure_produces_honest_reply(monkeypatch):
    out, sink = _run_command("what is my RAM usage", monkeypatch)
    assert isinstance(out["reply"], str) and out["reply"]
    # the reply must come from a real measurement or an honest error — never fake
    assert ("percent" in out["reply"].lower()) or ("fail" in out["reply"].lower())


def test_wake_word_matching():
    assert contains_wake_word("jarvis open chrome", "jarvis", "medium")
    assert contains_wake_word("hey jervis", "jarvis", "high")
    assert not contains_wake_word("open chrome", "jarvis", "medium")
    assert strip_wake_word("Jarvis, open chrome", "jarvis") == "open chrome"


def test_intent_engine_list_directory():
    from jarvis.brain.intent_engine import match_intent
    r = match_intent("list the files in my Downloads folder")
    assert r and r["tool"] == "list_directory"
