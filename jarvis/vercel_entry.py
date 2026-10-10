"""Vercel entrypoint for the JARVIS FastAPI backend service.

Vercel loads the Python entrypoint as a module relative to the service root
(jarvis/). The `jarvis` package itself lives HERE, so its parent directory
(the repository root) must be on sys.path for `from jarvis.backend.server
import app` to resolve. Locally you avoid this by running uvicorn from the
directory above jarvis/; this shim gives the Vercel loader the same layout
without touching any application code.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from jarvis.backend.server import app  # noqa: E402  (import after path fix)

__all__ = ["app"]
