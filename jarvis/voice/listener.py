"""Voice listener: microphone → VAD → wake word → speech-to-text.

Supports two modes:
- push-to-talk: an explicit trigger starts capture; the utterance ends on
  silence (or the trigger release).
- continuous: always listening; utterances must start with the wake word.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, Optional

from jarvis.config.settings import get_settings
from jarvis.voice import speech_to_text, wakeword
from jarvis.voice.audio import MicrophoneStream
from jarvis.voice.vad import UtteranceDetector

log = logging.getLogger("jarvis.voice.listener")


class VoiceListener:
    def __init__(self, on_command: Callable[[str, str], None],
                 on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None):
        self.on_command = on_command          # (text, source) -> None
        self.on_event = on_event or (lambda t, d: None)
        self._mic: Optional[MicrophoneStream] = None
        self._detector: Optional[UtteranceDetector] = None
        self._lock = threading.Lock()
        self._ptt_active = False
        self._level = 0.0
        self._level_ts = 0.0
        self._last_error = ""
        self._level_emit_ts = 0.0
        self._test_peak = 0.0
        self._test_until = 0.0
        self._device_used: Optional[int] = None

    # ------------------------------------------------------------------
    def start(self) -> bool:
        settings = get_settings()
        # Enumerate devices BEFORE opening the stream (PortAudio deadlock avoidance)
        try:
            from jarvis.voice.audio import list_input_devices
            list_input_devices(refresh=True)
        except Exception:  # noqa: BLE001
            pass
        device = settings.get("voice.input_device")
        if device is not None:
            device = int(device)
        self._device_used = device
        self._mic = MicrophoneStream(device=device, on_frame=self._on_frame)
        if not self._mic.start():
            self._last_error = "Microphone unavailable. Check Settings > Voice."
            self.on_event("voice_error", {"error": self._last_error})
            return False
        self._rebuild_detector()
        self.on_event("voice_state", {"listening": True, "mode": settings.get("voice.mode")})
        return True

    def stop(self) -> None:
        if self._mic:
            self._mic.stop()
            self._mic = None
        self.on_event("voice_state", {"listening": False})

    @property
    def running(self) -> bool:
        return self._mic is not None and self._mic.running

    def _rebuild_detector(self) -> None:
        settings = get_settings()
        self._detector = UtteranceDetector(
            silence_ms=int(settings.get("voice.silence_ms", 1100)),
            min_audio_ms=int(settings.get("voice.min_audio_ms", 400)),
            sensitivity=int(settings.get("voice.vad_sensitivity", 2)),
        )

    def apply_settings(self) -> None:
        self._rebuild_detector()
        # restart the stream if the selected device changed
        settings = get_settings()
        device = settings.get("voice.input_device")
        device = int(device) if device is not None else None
        if device != self._device_used and self._mic is not None:
            log.info("input device changed %s -> %s; restarting stream", self._device_used, device)
            self._mic.stop()
            self._device_used = device
            self._mic = MicrophoneStream(device=device, on_frame=self._on_frame)
            if not self._mic.start():
                self._last_error = "Microphone unavailable after device change."
                self.on_event("voice_error", {"error": self._last_error})

    # ------------------------------------------------------------------
    # Mic frame pump
    # ------------------------------------------------------------------
    def _on_frame(self, pcm: bytes) -> None:
        from jarvis.voice.audio import measure_level
        level = measure_level(pcm)
        self._level = level
        self._level_ts = time.time()

        now = time.time()
        if self._test_until > now:
            self._test_peak = max(self._test_peak, level)
        if now - self._level_emit_ts > 0.15:
            self._level_emit_ts = now
            self.on_event("voice_level", {"level": round(level, 3)})

        if self._ptt_active:
            self._feed_utterance(pcm)
            return

        settings = get_settings()
        if settings.get("voice.mode") != "continuous":
            return

        # continuous mode: detect wake word in speech
        assert self._detector is not None
        utterance = self._detector.feed(pcm)
        if utterance is not None:
            self._process_continuous_utterance(utterance)

    def _feed_utterance(self, pcm: bytes) -> None:
        """Push-to-talk buffering."""
        if self._ptt_buffer is None:
            self._ptt_buffer = bytearray()
        self._ptt_buffer.extend(pcm)

    _ptt_buffer: Optional[bytearray] = None

    # ------------------------------------------------------------------
    # Push to talk API
    # ------------------------------------------------------------------
    def start_ptt(self) -> None:
        with self._lock:
            self._ptt_active = True
            self._ptt_buffer = bytearray()
            self.on_event("ptt_state", {"active": True})
            self.on_event("state", {"state": "listening"})

    def end_ptt(self) -> None:
        with self._lock:
            if not self._ptt_active:
                return
            self._ptt_active = False
            buffer = bytes(self._ptt_buffer or b"")
            self._ptt_buffer = None
            self.on_event("ptt_state", {"active": False})
        if buffer:
            self.on_event("state", {"state": "transcribing"})
            threading.Thread(target=self._transcribe_ptt, args=(buffer,),
                             daemon=True, name="ptt-transcribe").start()

    def discard_ptt(self) -> None:
        """Emergency stop: throw away any in-progress push-to-talk capture."""
        with self._lock:
            self._ptt_active = False
            self._ptt_buffer = None
        self.on_event("ptt_state", {"active": False})

    def test_mic(self, seconds: float = 1.5) -> Dict[str, Any]:
        """Measure the live input level for a moment. Honest result or error."""
        if not self.running:
            return {"success": False, "level": 0.0, "detected": False,
                    "error": self._last_error or "Microphone is not active"}
        self._test_peak = 0.0
        self._test_until = time.time() + max(0.3, seconds)
        while time.time() < self._test_until:
            time.sleep(0.05)
        peak = round(self._test_peak, 3)
        detected = peak > 0.01
        return {"success": True, "level": peak, "detected": detected,
                "error": None if detected else "No audio detected — check the selected device"}

    def transcribe_audio(self, pcm16: bytes) -> Dict[str, Any]:
        """Structured transcription API: always returns
        {success, text, language, duration, error} — never raises."""
        settings = get_settings()
        result = speech_to_text.transcribe_structured(
            pcm16,
            language=str(settings.get("voice.language", "en")),
            model=str(settings.get("voice.stt_model", "base.en")),
            compute=str(settings.get("voice.stt_compute", "int8")),
        )
        if result.get("error"):
            self.on_event("voice_error", {"error": result["error"]})
        return result

    def _transcribe_ptt(self, audio: bytes) -> None:
        result = self.transcribe_audio(audio)
        if not result.get("success"):
            self.on_event("state", {"state": "idle"})
            return  # voice_error already emitted by transcribe_audio
        text = result.get("text", "").strip()
        self.on_event("stt_result", {"text": text, "source": "push-to-talk",
                                     "language": result.get("language"),
                                     "duration": result.get("duration")})
        if text:
            self.on_command(text, "voice")
        else:
            self.on_event("state", {"state": "idle"})
            self.on_event("voice_error", {"error": "I didn't catch any speech — try again."})

    # ------------------------------------------------------------------
    # Continuous mode with wake word
    # ------------------------------------------------------------------
    def _process_continuous_utterance(self, audio: bytes) -> None:
        result = self.transcribe_audio(audio)
        if not result.get("success"):
            return
        text = result.get("text", "").strip()
        if not text:
            return
        ww_enabled = settings.get("wake_word.enabled", True)
        word = settings.get("wake_word.word", "jarvis")
        sensitivity = settings.get("wake_word.sensitivity", "medium")

        if ww_enabled:
            hit = wakeword.contains_wake_word(text, word, sensitivity)
            if not hit:
                self.on_event("stt_ignored", {"text": text})
                return
            command = wakeword.strip_wake_word(text, word)
        else:
            command = text
        command = command.strip(" ,.:;!")
        if not command:
            # bare wake word → just wake the UI
            self.on_event("wake_detected", {})
            return
        self.on_event("wake_detected", {"text": text})
        self.on_command(command, "voice")

    # ------------------------------------------------------------------
    def current_level(self) -> float:
        if time.time() - self._level_ts > 0.5:
            return 0.0
        return self._level

    @property
    def last_error(self) -> str:
        return self._last_error
