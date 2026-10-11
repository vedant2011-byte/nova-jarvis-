"""Central configuration system for JARVIS.

Settings are stored as JSON on disk (data/settings.json) and deep-merged over
DEFAULTS so new keys added in upgrades appear automatically. Never store
secrets here; use environment variables (.env) for anything sensitive.
"""
from __future__ import annotations

import copy
import ctypes
import json
import os
import sys
import threading
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _known_dir(guid_str: str) -> Optional[Path]:
    """Resolve a Windows known folder via SHGetKnownFolderPath.

    This is the per-folder ground truth: OneDrive may redirect Desktop but
    leave Downloads/Documents local, so a single global OneDrive guess is
    wrong (e.g. C:\\Users\\X\\OneDrive\\Downloads may not exist at all).
    """
    if sys.platform != "win32":
        return None
    u = uuid.UUID(guid_str)
    guid = _GUID(u.time_low, u.time_mid, u.time_hi_version,
                 (ctypes.c_ubyte * 8)(*u.bytes[8:16]))
    buf = ctypes.c_wchar_p()
    try:
        if ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(guid), 0, None, ctypes.byref(buf)) != 0:
            return None
        return Path(buf.value)
    except Exception:  # noqa: BLE001
        return None
    finally:
        try:
            ctypes.windll.ole32.CoTaskMemFree(buf)
        except Exception:  # noqa: BLE001
            pass


def _user_dir(name: str, guid: str) -> Path:
    resolved = _known_dir(guid)
    if resolved is not None:
        return resolved
    # Cross-platform / failure fallback: prefer an existing local or OneDrive
    # copy of the folder, else keep the old heuristic.
    home = Path.home()
    if (home / name).exists():
        return home / name
    if (home / "OneDrive" / name).exists():
        return home / "OneDrive" / name
    base = home / "OneDrive" if (home / "OneDrive" / "Desktop").exists() and not (home / "Desktop").exists() else home
    return base / name


DESKTOP = _user_dir("Desktop", "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}")
DOCUMENTS = _user_dir("Documents", "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}")
DOWNLOADS = _user_dir("Downloads", "{374DE290-123F-4565-9164-39C4925E467B}")
PICTURES = _user_dir("Pictures", "{33E28130-4E1E-4676-835A-98395C3BC3BB}")
MUSIC = _user_dir("Music", "{4BD8D571-6D19-48D3-BE97-422220080E43}")
VIDEOS = _user_dir("Videos", "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}")

DATA_DIR = Path(os.getenv("JARVIS_DATA_DIR", str(PROJECT_ROOT / "data")))
LOG_DIR = Path(os.getenv("JARVIS_LOG_DIR", str(PROJECT_ROOT / "logs")))
SETTINGS_FILE = DATA_DIR / "settings.json"
DB_FILE = DATA_DIR / "jarvis.db"
LOG_FILE = LOG_DIR / "jarvis.log"

DEFAULT_ALLOWED_ROOTS: List[str] = [
    str(DESKTOP), str(DOCUMENTS), str(DOWNLOADS),
    str(PICTURES), str(MUSIC), str(VIDEOS),
    str(DATA_DIR),
]

# Locations that may never be written to / deleted from, even if under an
# allowed root. Reads are also blocked for these (they are sensitive).
# JARVIS_EXTRA_PROTECTED / JARVIS_DISABLE_DEFAULT_PROTECTED let advanced users
# adjust this list via environment (tests use the latter).
PROTECTED_PATHS: List[str] = (
    [] if os.getenv("JARVIS_DISABLE_DEFAULT_PROTECTED") == "1" else [
        r"C:\Windows",
        r"C:\Program Files",
        r"C:\Program Files (x86)",
        r"C:\ProgramData",
        str(Path.home() / "AppData"),  # covers Local, Roaming, LocalLow
    ]
)
if os.getenv("JARVIS_EXTRA_PROTECTED"):
    PROTECTED_PATHS = PROTECTED_PATHS + os.getenv("JARVIS_EXTRA_PROTECTED").split(";")

DEFAULTS: Dict[str, Any] = {
    "general": {
        "assistant_name": "JARVIS",
        "language": "en",
        "debug_mode": False,
        "startup_animation": True,
    },
    "server": {
        "host": "127.0.0.1",
        "port": 8765,
    },
    "ai": {
        "runtime": "ollama",              # ollama | off (intent engine only)
        "host": "http://127.0.0.1:11434",
        "model": "llama3.2:3b",
        "temperature": 0.2,
        "num_ctx": 4096,
        "request_timeout": 60,
        "response_length": "concise",     # concise | normal | detailed
        "max_tool_iterations": 6,
        "fallback_to_intent_engine": True,
    },
    "voice": {
        "input_device": None,             # None = system default
        "output_device": None,
        "stt_engine": "faster-whisper",
        "stt_model": "base.en",
        "stt_compute": "int8",
        "language": "en",
        "mode": "push-to-talk",           # push-to-talk | continuous
        "silence_ms": 1100,
        "min_audio_ms": 400,
        "vad_sensitivity": 2,             # 0-3 (higher = more sensitive)
    },
    "wake_word": {
        "enabled": True,
        "word": "jarvis",
        "sensitivity": "medium",          # low | medium | high
    },
    "tts": {
        "enabled": True,
        "engine": "auto",                 # auto | piper | sapi
        "piper_voice": "",                # path to .onnx voice model
        "volume": 0.9,
        "rate": 175,                      # words per minute (SAPI)
        "length_scale": 1.0,              # piper speed (1.0 = normal)
        "speak_startup": True,
    },
    "permissions": {
        "auto_approve_safe": True,
        "confirm_file_deletes": True,
        "confirm_edits": True,
        "auto_approve_low_risk_terminal": False,
        "allowed_roots": DEFAULT_ALLOWED_ROOTS,
        "custom_app_paths": {},
        "allow_browser_automation": True,
    },
    "memory": {
        "enabled": True,
        "max_conversation_history": 60,
    },
    "appearance": {
        "animation_intensity": 1.0,       # 0 .. 1.5
        "hud_density": "normal",          # compact | normal | roomy
        "brightness": 1.0,
        "accent": "cyan",                 # cyan | blue | violet
    },
    "startup": {
        "launch_with_windows": False,
        "start_minimized": False,
    },
    "terminal": {
        "default_wait_seconds": 30,
        "max_wait_seconds": 120,
    },
}

_LOCK = threading.RLock()
_cache: Optional["Settings"] = None


class Settings:
    """Thread-safe, JSON-backed settings with dot-key access."""

    def __init__(self, path: Path = SETTINGS_FILE):
        self.path = path
        self._data: Dict[str, Any] = copy.deepcopy(DEFAULTS)
        self._lock = threading.RLock()
        self.load()

    # -- persistence -------------------------------------------------------
    def load(self) -> None:
        with self._lock:
            self._data = copy.deepcopy(DEFAULTS)
            if self.path.exists():
                try:
                    disk = json.loads(self.path.read_text(encoding="utf-8"))
                    self._deep_merge(self._data, disk)
                except (json.JSONDecodeError, OSError) as exc:
                    print(f"[settings] failed to load {self.path}: {exc}; using defaults", file=sys.stderr)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                # read-only filesystem (e.g. serverless functions): keep
                # settings in memory instead of crashing at import time
                print(f"[settings] cannot create {self.path.parent}: {exc}; settings will not persist", file=sys.stderr)

    def save(self) -> None:
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            except OSError as exc:
                print(f"[settings] failed to save {self.path}: {exc}", file=sys.stderr)

    def reset(self) -> None:
        with self._lock:
            self._data = copy.deepcopy(DEFAULTS)
            self.save()

    # -- access ------------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            node: Any = self._data
            for part in key.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return node

    def set(self, key: str, value: Any, save: bool = True) -> None:
        with self._lock:
            parts = key.split(".")
            node = self._data
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value
            if save:
                self.save()

    def as_dict(self) -> Dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def update(self, patch: Dict[str, Any]) -> None:
        """Merge a nested dict patch into settings and persist."""
        with self._lock:
            self._deep_merge(self._data, patch)
            self.save()

    @staticmethod
    def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> None:
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                Settings._deep_merge(base[key], value)
            else:
                base[key] = value


def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    global _cache
    with _LOCK:
        if _cache is None:
            _cache = Settings()
        return _cache


def reset_settings_singleton() -> None:
    global _cache
    with _LOCK:
        _cache = None
