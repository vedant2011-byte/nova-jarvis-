"""Speech recognition via faster-whisper (local, offline).

The model is loaded lazily on first use so the server can start quickly, and
runs transcriptions in a worker thread to avoid blocking the event loop.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

import numpy as np

from jarvis.config.settings import get_settings

log = logging.getLogger("jarvis.voice.stt")

_model = None
_model_name: Optional[str] = None
_lock = threading.Lock()


class STTUnavailable(Exception):
    pass


def _get_model(name: str, compute: str):
    global _model, _model_name
    with _lock:
        if _model is not None and _model_name == name:
            return _model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise STTUnavailable(
                "faster-whisper is not installed. Run: pip install faster-whisper") from exc
        log.info("loading whisper model %s (%s)...", name, compute)
        _model = WhisperModel(name, device="cpu", compute_type=compute)
        _model_name = name
        log.info("whisper model ready")
        return _model


def is_available() -> bool:
    """True if faster-whisper can be imported (model may still need loading)."""
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def model_info() -> Dict[str, Any]:
    """Honest STT status for the UI: engine, configured model, loaded state."""
    settings = get_settings()
    return {
        "engine": "faster-whisper" if is_available() else "unavailable",
        "available": is_available(),
        "model": settings.get("voice.stt_model", "base.en"),
        "loaded": _model is not None,
        "install_hint": None if is_available() else "pip install faster-whisper",
    }


def transcribe_structured(pcm16: bytes, language: str = "en",
                          model: str = "base.en", compute: str = "int8") -> Dict[str, Any]:
    """Transcribe and ALWAYS return a structured result (never raises).

    Shape: {success, text, language, duration, error}
    """
    if not pcm16 or len(pcm16) < 1600:  # < 0.1 s of audio: nothing to decode
        return {"success": True, "text": "", "language": language,
                "duration": round(len(pcm16) / 32000.0, 2) if pcm16 else 0.0,
                "error": None}
    try:
        result = transcribe(pcm16, language=language, model=model, compute=compute)
        return {"success": True, "text": result.get("text", ""),
                "language": result.get("language", language),
                "duration": result.get("duration", 0.0), "error": None}
    except STTUnavailable as exc:
        return {"success": False, "text": "", "language": language,
                "duration": 0.0, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        log.exception("transcription failed")
        return {"success": False, "text": "", "language": language,
                "duration": 0.0, "error": f"Transcription failed: {exc}"}


def transcribe(pcm16: bytes, language: str = "en",
               model: str = "base.en", compute: str = "int8") -> Dict[str, Any]:
    """Transcribe 16 kHz mono PCM16 bytes to text."""
    try:
        model_obj = _get_model(model, compute)
    except STTUnavailable:
        raise
    audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
    if audio.size == 0:
        return {"text": "", "language": language}
    segments, info = model_obj.transcribe(
        audio, language=language if language != "auto" else None,
        beam_size=2, vad_filter=True,
    )
    text = " ".join(seg.text.strip() for seg in segments).strip()
    return {"text": text, "language": info.language, "duration": round(audio.size / 16000, 2)}


def warmup(model: str = "base.en", compute: str = "int8") -> bool:
    """Preload the model in a background thread; returns success."""
    def _load():
        try:
            _get_model(model, compute)
        except Exception:  # noqa: BLE001
            log.exception("whisper warmup failed")
    t = threading.Thread(target=_load, daemon=True, name="whisper-warmup")
    t.start()
    return True
