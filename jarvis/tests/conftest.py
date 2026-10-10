import os
import sys
from pathlib import Path

import pytest

# Ensure the jarvis package root is importable
ROOT = Path(__file__).resolve().parent.parent
PARENT = ROOT.parent
for p in (str(ROOT), str(PARENT)):
    if p not in sys.path:
        sys.path.insert(0, p)

# Use a temp data dir so tests never touch real user data
os.environ.setdefault("JARVIS_DATA_DIR", str(ROOT / "data-test"))
os.environ.setdefault("JARVIS_LOG_DIR", str(ROOT / "logs-test"))
# pytest temp dirs live under AppData; relax default protected list for tests
os.environ.setdefault("JARVIS_DISABLE_DEFAULT_PROTECTED", "1")


@pytest.fixture()
def temp_allowed_root(tmp_path):
    """Register a temp dir as the only allowed root for path tests."""
    from jarvis.config.settings import get_settings
    settings = get_settings()
    old = settings.get("permissions.allowed_roots")
    settings.set("permissions.allowed_roots", [str(tmp_path)])
    yield tmp_path
    settings.set("permissions.allowed_roots", old)


@pytest.fixture(autouse=True)
def _clean_settings():
    """Start every test from default settings; persist nothing."""
    import json
    from jarvis.config import settings as settings_mod
    settings_mod.reset_settings_singleton()
    s = settings_mod.get_settings()
    yield
    # wipe the file so later tests start clean
    try:
        s.path.unlink(missing_ok=True)
    except OSError:
        pass
    settings_mod.reset_settings_singleton()


@pytest.fixture(autouse=True, scope="session")
def _fake_tts_engine():
    """Deterministic TTS in unit tests: the queue/worker/callback logic runs
    for real, but no SAPI/COM and no audio. The REAL engine is verified by the
    live server smoke tests, not in-process (cross-thread COM hangs).

    This MUST be session-scoped with a permanent patch, not monkeypatched per
    test: the TTS worker is a global singleton, so an utterance still queued
    when one test ends would be spoken by the next test with the REAL
    _speak_sapi — creating a real SAPI/COM engine (plus a comtypes event
    advise sink) that deadlocks the interpreter at exit.
    """
    from jarvis.voice import text_to_speech as tts
    tts.pyttsx3 = None
    tts._speak_sapi = lambda text, settings: True
    yield


@pytest.fixture(autouse=True)
def _no_capture_for_voice_tests(request):
    """The TTS/STT worker threads (started while the server tests ran) deadlock
    with pytest's global stream capture on Windows. Running the voice module
    with capture suspended keeps threads + captured stdio decoupled."""
    if request.node.fspath.purebasename != "test_voice.py":
        yield
        return
    capman = request.config.pluginmanager.getplugin("capturemanager")
    try:
        if capman:
            capman.suspend_global_capture(in_=True)
        yield
    finally:
        if capman:
            capman.resume_global_capture()
