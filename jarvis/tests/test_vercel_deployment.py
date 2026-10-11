"""Deployment-shape tests for the Vercel `api` service.

These guard the root cause of the 2026-10-11 black-screen incident: in the
deployed function the bundle contains ONLY the service root's contents, so
vercel_entry.py sits at the bundle root and its old ``parent.parent`` sys.path
shim raised ``ModuleNotFoundError: No module named 'jarvis'`` — every
invocation returned 500 FUNCTION_INVOCATION_FAILED and the HUD boot screen
never received its ``boot_done`` event.
"""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

JARVIS_DIR = Path(__file__).resolve().parent.parent  # the service root (jarvis/)

# Directories that make up the importable `jarvis` package in the bundle
# (mirrors what survives excludeFiles in vercel.json).
_PACKAGE_DIRS = ("backend", "brain", "config", "memory", "security", "tools", "voice")


def test_vercel_entry_imports_in_repo_layout() -> None:
    """The entrypoint must import in the ordinary repo checkout."""
    sys.path.insert(0, str(JARVIS_DIR))
    try:
        mod = importlib.import_module("vercel_entry")
    finally:
        sys.path.remove(str(JARVIS_DIR))
    assert mod.app.title == "JARVIS"
    routes = {getattr(r, "path", None) for r in mod.app.routes}
    assert "/ws" in routes
    assert "/api/status" in routes


def test_vercel_entry_imports_in_flattened_bundle(tmp_path) -> None:
    """Simulate the deployed Vercel bundle: only the service root's contents.

    vercel_entry.py ends up at the bundle root with no `jarvis` directory
    above it. Importing it must still work (this exact scenario produced
    FUNCTION_INVOCATION_FAILED in production).
    """
    for d in _PACKAGE_DIRS:
        shutil.copytree(JARVIS_DIR / d, tmp_path / d,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(JARVIS_DIR / "vercel_entry.py", tmp_path / "vercel_entry.py")

    env = dict(os.environ)
    env["JARVIS_DATA_DIR"] = str(tmp_path / "data")
    env["JARVIS_LOG_DIR"] = str(tmp_path / "logs")
    env["JARVIS_DISABLE_DEFAULT_PROTECTED"] = "1"

    code = (
        "import vercel_entry\n"
        "app = vercel_entry.app\n"
        "routes = {getattr(r, 'path', None) for r in app.routes}\n"
        "assert app.title == 'JARVIS', app.title\n"
        "assert '/ws' in routes, routes\n"
        "assert '/api/status' in routes, routes\n"
        "print('FLATTENED-OK')\n"
    )
    p = subprocess.run([sys.executable, "-c", code], cwd=str(tmp_path),
                       env=env, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, f"stdout={p.stdout}\nstderr={p.stderr}"
    assert "FLATTENED-OK" in p.stdout


def test_settings_survives_unwritable_data_dir(tmp_path) -> None:
    """A read-only/uncreatable data dir must not crash settings at import.

    On serverless filesystems (everything except /tmp is read-only) the old
    ``mkdir`` in Settings.load raised OSError and killed the whole function.
    """
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"")  # an existing FILE: mkdir under it must fail
    from jarvis.config.settings import Settings

    s = Settings(path=blocker / "sub" / "settings.json")
    # defaults are loaded and usable in memory
    assert s.get("server.port") == 8765
    # saving must also fail loudly-but-safely (no exception escapes)
    s.set("general.assistant_name", "JARVIS-TEST")
