"""Audio capture via sounddevice with graceful degradation."""
from __future__ import annotations

import logging
import queue
import threading
from typing import Callable, List, Optional

import numpy as np

log = logging.getLogger("jarvis.voice.audio")

SAMPLE_RATE = 16000
FRAME_SIZE = 480          # 30 ms at 16 kHz
FRAME_BYTES = FRAME_SIZE * 2  # int16

# PortAudio on Windows deadlocks if a device is queried while a stream is
# open, so all queries are serialized AND skipped while a stream is active.
_sd_lock = threading.RLock()
_devices_cache: Optional[List[dict]] = None
_devices_ts: float = 0.0
_stream_active: bool = False


def list_input_devices(refresh: bool = False) -> List[dict]:
    """Return usable input devices (cached for 30 s to avoid PortAudio races)."""
    global _devices_cache, _devices_ts
    import time as _time
    with _sd_lock:
        if not refresh and _devices_cache is not None and _time.time() - _devices_ts < 30:
            return _devices_cache
        try:
            import sounddevice as sd
        except (ImportError, OSError) as exc:
            log.warning("sounddevice unavailable: %s", exc)
            return []
        if _stream_active:
            # Never touch PortAudio while a stream is open (deadlock risk).
            if _devices_cache is not None:
                return _devices_cache
            log.warning("cannot enumerate devices while microphone stream is open")
            return []
        devices = []
        try:
            default_out = sd.default.device[0]
            for idx, dev in enumerate(sd.query_devices()):
                if dev.get("max_input_channels", 0) > 0:
                    devices.append({
                        "index": idx,
                        "name": dev["name"],
                        "channels": dev["max_input_channels"],
                        "default": idx == default_out,
                    })
        except OSError as exc:
            log.warning("device query failed: %s", exc)
            return _devices_cache or []
        _devices_cache = devices
        _devices_ts = _time.time()
        return devices


class MicrophoneStream:
    """Continuous 16 kHz mono int16 microphone stream with a frame callback."""

    def __init__(self, device: Optional[int] = None,
                 on_frame: Optional[Callable[[bytes], None]] = None):
        self.device = device
        self.on_frame = on_frame
        self._q: "queue.Queue[bytes]" = queue.Queue(maxsize=200)
        self._stream = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> bool:
        try:
            import sounddevice as sd
        except (ImportError, OSError) as exc:
            log.error("cannot open audio: %s", exc)
            return False

        def _callback(indata, frames, time_info, status):  # noqa: ANN001
            if status:
                log.debug("audio status: %s", status)
            try:
                self._q.put_nowait(bytes(indata))
            except queue.Full:
                pass

        with _sd_lock:
            try:
                self._stream = sd.RawInputStream(
                    samplerate=SAMPLE_RATE, blocksize=FRAME_SIZE,
                    channels=1, dtype="int16", device=self.device, callback=_callback,
                )
                self._stream.start()
                global _stream_active
                _stream_active = True
            except OSError as exc:
                log.error("failed to open microphone: %s", exc)
                return False

        self._running = True

        def _pump() -> None:
            while self._running:
                try:
                    data = self._q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if self.on_frame:
                    try:
                        self.on_frame(data)
                    except Exception:  # noqa: BLE001
                        log.exception("audio frame callback failed")

        self._thread = threading.Thread(target=_pump, daemon=True, name="jarvis-mic")
        self._thread.start()
        log.info("microphone stream started (device=%s)", self.device)
        return True

    def stop(self) -> None:
        self._running = False
        with _sd_lock:
            global _stream_active
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except OSError:
                    pass
                self._stream = None
            _stream_active = False
        log.info("microphone stream stopped")

    @property
    def running(self) -> bool:
        return self._running


def measure_level(pcm: bytes) -> float:
    """Return RMS audio level 0..1 from a PCM16 frame."""
    if not pcm:
        return 0.0
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(samples ** 2)))
    return min(1.0, rms * 4.0)
