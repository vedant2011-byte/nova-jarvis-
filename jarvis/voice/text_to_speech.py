"""Local text-to-speech with a single dedicated worker thread.

pyttsx3/SAPI COM objects must only be touched from one thread, so all
speaking is serialized through a queue. Piper synthesizes to WAV and plays
via sounddevice. All engines are local and free.
"""
from __future__ import annotations

import atexit
import gc
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Any, Optional

from jarvis.config.settings import DATA_DIR, get_settings

log = logging.getLogger("jarvis.voice.tts")

try:
    import pyttsx3  # type: ignore
except ImportError:
    pyttsx3 = None  # type: ignore

try:
    from piper import PiperVoice  # type: ignore
except ImportError:
    PiperVoice = None  # type: ignore

_engine_lock = threading.Lock()
_sapi_probe: Optional[bool] = None
_sapi_engine: Any = None          # owned by the TTS worker thread ONLY
_engine_thread_id: Optional[int] = None
_piper_voice: Any = None
_piper_path: str = ""
_voices_cache: list = []

_queue: "queue.Queue[Any]" = queue.Queue()
_SHUTDOWN = object()              # sentinel: tells the worker to exit cleanly
_worker_started = False
_worker_thread: Optional[threading.Thread] = None
_speaking = threading.Event()
_stop_flag = threading.Event()
_last_spoken_ts = 0.0


def _probe_sapi() -> bool:
    """Import-level availability check ONLY. Never creates a COM engine here:
    SAPI engines are apartment-threaded (the worker thread must create its
    own) and pyttsx3.init() spawns a non-daemon driver thread per engine."""
    global _sapi_probe
    if _sapi_probe is not None:
        return _sapi_probe
    _sapi_probe = pyttsx3 is not None
    return _sapi_probe


def _get_piper():
    global _piper_voice, _piper_path
    settings = get_settings()
    voice_path = str(settings.get("tts.piper_voice", "") or "")
    if PiperVoice is None or not voice_path or not Path(voice_path).exists():
        return None
    with _engine_lock:
        if _piper_voice is not None and _piper_path == voice_path:
            return _piper_voice
        try:
            _piper_voice = PiperVoice.load(voice_path)
            _piper_path = voice_path
            log.info("piper voice loaded: %s", voice_path)
            return _piper_voice
        except Exception:  # noqa: BLE001
            log.exception("failed to load piper voice %s", voice_path)
            return None


# ---------------------------------------------------------------------------
# TTS worker thread
# ---------------------------------------------------------------------------

def _ensure_worker() -> None:
    global _worker_started, _worker_thread
    if _worker_started and _worker_thread is not None and _worker_thread.is_alive():
        return
    _worker_started = True

    def _loop() -> None:
        while True:
            item = _queue.get()
            if item is _SHUTDOWN:
                # Release the COM engine ON this thread: finalizing it from
                # any other thread deadlocks the process at interpreter exit
                # (comtypes Unadvise into an STA that is no longer pumping).
                _release_engine()
                return
            if item is None:
                continue
            text, done_cb = item
            _speaking.set()
            ok = False
            try:
                ok = _speak_blocking(text)
                if not ok:
                    log.warning("TTS failed for: %.60s", text)
            except Exception:  # noqa: BLE001
                log.exception("TTS worker error")
            finally:
                _speaking.clear()
                if done_cb:
                    try:
                        done_cb(ok)
                    except Exception:  # noqa: BLE001
                        pass

    _worker_thread = threading.Thread(
        target=_loop, daemon=True, name="jarvis-tts-worker")
    _worker_thread.start()


def _speak_blocking(text: str) -> bool:
    settings = get_settings()
    piper = _get_piper()
    if piper is not None:
        try:
            return _speak_piper(piper, text, settings)
        except Exception:  # noqa: BLE001
            log.exception("piper failed; falling back to SAPI")
    return _speak_sapi(text, settings)


def _speak_piper(piper, text: str, settings) -> bool:
    import wave
    out_dir = DATA_DIR / "tts"
    out_dir.mkdir(parents=True, exist_ok=True)
    wav_path = out_dir / f"utt_{int(time.time() * 1000)}.wav"
    length_scale = float(settings.get("tts.length_scale", 1.0))
    with wave.open(str(wav_path), "wb") as wav:
        piper.synthesize(text, wav, length_scale=length_scale)
    ok = _play_wav(str(wav_path), float(settings.get("tts.volume", 0.9)))
    try:
        wav_path.unlink(missing_ok=True)
    except OSError:
        pass
    return ok


def _speak_sapi(text: str, settings) -> bool:
    global _sapi_engine, _engine_thread_id
    # The engine must be created on, and used from, THIS thread. An engine
    # created in the probe/warmup thread belongs to that COM apartment and
    # hangs in startLoop when driven from here.
    if _sapi_engine is None or _engine_thread_id != threading.get_ident():
        if pyttsx3 is None:
            return False
        try:
            _sapi_engine = pyttsx3.init()
            _engine_thread_id = threading.get_ident()
            _voices_cache[:] = [{"id": v.id, "name": v.name}
                                for v in _sapi_engine.getProperty("voices")]
            log.info("SAPI engine created on TTS worker thread")
        except Exception:  # noqa: BLE001
            log.exception("SAPI engine init failed on worker thread")
            _sapi_engine = None
            return False
    _stop_flag.clear()
    with _engine_lock:
        _sapi_engine.setProperty("volume", float(settings.get("tts.volume", 0.9)))
        _sapi_engine.setProperty("rate", int(settings.get("tts.rate", 175)))
        _sapi_engine.say(text)
        _sapi_engine.runAndWait()
    return True


def _play_wav(path: str, volume: float) -> bool:
    try:
        import sounddevice as sd
        import soundfile as sf  # type: ignore
        data, sr = sf.read(path, dtype="float32")
        sd.play(data * volume, sr)
        sd.wait()
        return True
    except ImportError:
        pass
    except Exception:  # noqa: BLE001
        log.exception("wav playback failed")
    # last-resort Windows playback
    try:
        import winsound
        winsound.PlaySound(path, winsound.SND_FILENAME)
        return True
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def speak(text: str, wait: bool = False) -> bool:
    """Queue text for speech. Returns False only if no engine is available.

    The worker thread is the ONLY thread that touches the SAPI COM engine;
    wait=True queues the utterance and blocks until it has been spoken (or
    failed/cancelled) — it never touches the engine from the caller's thread.
    """
    global _last_spoken_ts
    if not text or not text.strip():
        return False
    settings = get_settings()
    if not settings.get("tts.enabled", True):
        return False
    _ensure_worker()
    _last_spoken_ts = time.time()
    if wait:
        done = threading.Event()
        holder = {"ok": False}

        def _done(ok: bool) -> None:
            holder["ok"] = ok
            done.set()

        _queue.put((text, _done))
        done.wait(timeout=120)
        return holder["ok"]
    _queue.put((text, None))
    return True


def available_engines() -> dict:
    """Engine availability for the UI. No COM in this call path — engines are
    created lazily by the worker on first utterance."""
    piper_ready = PiperVoice is not None and bool(get_settings().get("tts.piper_voice", ""))
    return {"sapi": _probe_sapi(), "piper": piper_ready}


def list_voices() -> list:
    return list(_voices_cache)


def is_speaking() -> bool:
    return _speaking.is_set()


def stop_speaking() -> None:
    """Cancel queued speech immediately (waiters receive False).

    The engine object is NEVER touched from outside the worker: calling
    engine.stop() on an idle pyttsx3/SAPI engine corrupts its internal loop
    and hangs the NEXT utterance. If speech is in flight it finishes (SAPI
    sentences are short); everything queued after it is discarded.
    """
    _stop_flag.set()
    while True:
        try:
            item = _queue.get_nowait()
        except queue.Empty:
            break
        if item and item[1]:
            try:
                item[1](False)
            except Exception:  # noqa: BLE001
                pass


def warmup() -> bool:
    """Start the speech worker. The SAPI engine itself is created lazily on
    first utterance (correct COM apartment = the worker thread)."""
    _ensure_worker()
    return True


def _release_engine() -> None:
    """Drop the SAPI engine and finalize its COM objects ON the worker thread.

    pyttsx3's SAPI driver advises a comtypes event sink per engine; if that
    sink is finalized during interpreter shutdown from the main thread, its
    __del__ calls Unadvise cross-apartment and the process hangs forever.
    """
    global _sapi_engine, _engine_thread_id
    engine = _sapi_engine
    _sapi_engine = None
    _engine_thread_id = None
    if engine is not None:
        del engine
        try:
            gc.collect()  # run the engine/sink __del__ chain here, now
        except Exception:  # noqa: BLE001
            pass
        log.info("SAPI engine released on TTS worker thread")


def shutdown_worker(timeout: float = 5.0) -> None:
    """Stop the worker and release the COM engine in its owner thread.
    Registered via atexit so the process always exits cleanly."""
    global _worker_thread
    if not _worker_started:
        return
    _queue.put(_SHUTDOWN)
    thread = _worker_thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=timeout)


atexit.register(shutdown_worker)
