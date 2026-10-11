"""Vercel entrypoint for the JARVIS FastAPI backend service.

Vercel builds the `api` service from the service root (jarvis/) and bundles
only the service's own files, so in the DEPLOYED function this file sits at
the bundle root and its parent directory contains no `jarvis` package.
Locally (repo checkout) the package lives one directory up. This shim
resolves both layouts so `from jarvis.backend.server import app` works
identically in either environment.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

_HERE = Path(__file__).resolve().parent  # service root (contents of jarvis/)


def _bootstrap() -> None:
    # Layout 1: repo checkout — .../nova/jarvis/vercel_entry.py; the package
    # `jarvis` is a directory next to (or one level above) this file's parent.
    for parent in (_HERE.parent, _HERE.parent.parent):
        pkg = parent / "jarvis"
        if (pkg / "backend" / "server.py").is_file():
            if str(parent) not in sys.path:
                sys.path.insert(0, str(parent))
            return
    # Layout 2: deployed Vercel bundle — the service root's contents ARE the
    # `jarvis` package. Register this directory as that package so its
    # subpackages (backend, config, memory, ...) resolve from here.
    pkg_mod = types.ModuleType("jarvis")
    pkg_mod.__path__ = [str(_HERE)]  # marks it as a package for the importer
    sys.modules["jarvis"] = pkg_mod


_bootstrap()

from jarvis.backend.server import app  # noqa: E402  (import after path fix)

__all__ = ["app"]
