"""Voice activity detection + silence-based utterance segmentation.

Energy-based VAD with adaptive noise floor. Zero dependencies beyond numpy.
"""
from __future__ import annotations

from collections import deque
from typing import Optional

import numpy as np

from jarvis.voice.audio import FRAME_SIZE, SAMPLE_RATE


class UtteranceDetector:
    """Accumulates speech frames into utterances separated by silence.

    sensitivity: 0 (least) .. 3 (most sensitive)
    """

    def __init__(self, silence_ms: int = 1100, min_audio_ms: int = 400,
                 sensitivity: int = 2):
        self.silence_frames = max(1, (silence_ms * SAMPLE_RATE) // 1000 // FRAME_SIZE)
        self.min_audio_frames = max(1, (min_audio_ms * SAMPLE_RATE) // 1000 // FRAME_SIZE)
        self.sensitivity = max(0, min(3, sensitivity))
        self.reset()
        # adaptive noise floor
        self._noise = deque(maxlen=100)
        self._threshold = 0.012

    def reset(self) -> None:
        self._buffer = []
        self._speech_frames = 0
        self._silence_frames = 0
        self._in_speech = False

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def _update_threshold(self, level: float) -> None:
        if not self._in_speech:
            self._noise.append(level)
            if len(self._noise) >= 30:
                base = float(np.median(self._noise))
                offset = (0.008, 0.006, 0.004, 0.0025)[self.sensitivity]
                self._threshold = max(0.006, base + offset)

    def feed(self, pcm: bytes) -> Optional[bytes]:
        """Feed a PCM frame. Returns the complete utterance bytes when done."""
        import jarvis.voice.audio as audio
        level = audio.measure_level(pcm)
        self._update_threshold(level)

        if level > self._threshold:
            if not self._in_speech:
                self._in_speech = True
                self._buffer = []
            self._buffer.append(pcm)
            self._speech_frames += 1
            self._silence_frames = 0
        elif self._in_speech:
            self._buffer.append(pcm)  # keep brief quiet inside words
            self._silence_frames += 1
            if self._silence_frames >= self.silence_frames:
                if self._speech_frames >= self.min_audio_frames:
                    utterance = b"".join(self._buffer)
                    self.reset()
                    return utterance
                self.reset()
        return None
