"""Wake word detection.

Strategy: energy-gated buffered transcription. When continuous listening is
enabled, speech segments are transcribed and matched against the wake word
using fuzzy matching. Transcription happens only for speech-length audio, so
CPU cost stays acceptable on an 8 GB laptop.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

log = logging.getLogger("jarvis.voice.wakeword")

_WAKE_VARIANTS = [
    "jarvis", "jervis", "jarvic", "jarvez", "jarvas", "jervis", "javis",
    "service", "services", "jerva", "jarvy", "jarwis", "garvis",
]

_SENSITIVITY_WINDOW = {"low": 0.86, "medium": 0.72, "high": 0.55}


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0 or lb == 0:
        return max(la, lb)
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        ca = a[i - 1]
        for j in range(1, lb + 1):
            cost = 0 if ca == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[lb]


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return 1.0 - _levenshtein(a, b) / max(len(a), len(b))


def contains_wake_word(text: str, word: str = "jarvis",
                       sensitivity: str = "medium") -> Optional[str]:
    """Return the wake word position in text, or None.

    Uses exact word match first, then fuzzy matching against common
    mis-hearings. 'high' sensitivity accepts weaker matches.
    """
    if not text:
        return None
    lowered = text.lower().strip()
    word_l = word.lower().strip()

    # exact token match
    tokens = re.findall(r"[a-z']+", lowered)
    for token in tokens:
        if token == word_l:
            return lowered

    # fuzzy match against variants
    threshold = _SENSITIVITY_WINDOW.get(sensitivity, 0.72)
    candidates = [word_l] + [v for v in _WAKE_VARIANTS if v != word_l]
    for token in tokens:
        for cand in candidates:
            if _ratio(token, cand) >= threshold:
                return lowered
    return None


def strip_wake_word(text: str, word: str = "jarvis") -> str:
    """Remove a leading wake word and punctuation from a command."""
    lowered = text.lower().strip()
    pattern = re.compile(
        r"^\s*" + re.escape(word.lower()) + r"[\s,.:;!]*", re.I)
    stripped = pattern.sub("", lowered, count=1)
    if stripped == lowered:
        # try fuzzy variants
        for variant in _WAKE_VARIANTS:
            pat = re.compile(r"^\s*" + re.escape(variant) + r"[\s,.:;!]*", re.I)
            new = pat.sub("", lowered, count=1)
            if new != lowered:
                return new.strip()
    return stripped.strip()
