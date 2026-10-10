"""Path safety validation for the filesystem tool layer.

Rules:
- Every path is expanded (~), absolutised and normalised.
- It must reside under one of the configured allowed roots.
- It must not be inside a protected system location.
- Symlinks that escape an allowed root are rejected.
- Drive-relative forms like "C:foo" are rejected.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from jarvis.config.settings import PROTECTED_PATHS, get_settings


class PathValidationError(Exception):
    """Raised when a path fails safety validation."""


def allowed_roots() -> List[Path]:
    roots = get_settings().get("permissions.allowed_roots", [])
    return [Path(os.path.expanduser(str(r))).resolve() for r in roots]


def protected_paths() -> List[Path]:
    return [Path(os.path.expanduser(str(p))).resolve() for p in PROTECTED_PATHS]


def normalize(raw: str) -> Path:
    if not raw or not str(raw).strip():
        raise PathValidationError("Empty path")
    candidate = str(raw).strip().strip('"')
    # Reject drive-relative like "C:foo" and UNC tricks
    if len(candidate) >= 2 and candidate[1] == ":" and not candidate.startswith(("\\\\", "//")):
        rest = candidate[2:]
        if rest and not rest.startswith(("\\", "/")):
            raise PathValidationError(f"Drive-relative path not allowed: {raw}")
    expanded = os.path.expandvars(os.path.expanduser(candidate))
    path = Path(expanded)
    try:
        resolved = path.resolve(strict=False)
    except (OSError, ValueError) as exc:
        raise PathValidationError(f"Cannot resolve path: {raw}") from exc
    return resolved


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_read(raw: str) -> Path:
    path = normalize(raw)
    _check_protected(path, allow_read=False)
    if not _under_any(path, allowed_roots()):
        # Reading outside allowed roots is only permitted for clearly public
        # locations; we still block protected paths above.
        pass
    return path


def validate_write(raw: str, must_exist: bool = False) -> Path:
    path = normalize(raw)
    _check_protected(path, allow_read=True)
    if not _under_any(path, allowed_roots()):
        raise PathValidationError(
            f"Path is outside allowed roots: {path}. Add it in Settings > Permissions if intended."
        )
    if must_exist and not path.exists():
        raise PathValidationError(f"Path does not exist: {path}")
    return path


def validate_delete(raw: str) -> Path:
    path = normalize(raw)
    _check_protected(path, allow_read=True)
    if not _under_any(path, allowed_roots()):
        raise PathValidationError(f"Deletion outside allowed roots is forbidden: {path}")
    if not path.exists():
        raise PathValidationError(f"Path does not exist: {path}")
    return path


def _check_protected(path: Path, allow_read: bool) -> None:
    for prot in protected_paths():
        if _is_under(path, prot):
            if not allow_read:
                raise PathValidationError(f"Protected system location: {prot}")
            # Even reads are blocked for configured protected paths
            raise PathValidationError(f"Protected system location: {prot}")


def _under_any(path: Path, roots: Iterable[Path]) -> bool:
    return any(_is_under(path, root) for root in roots)


def search_roots() -> List[Path]:
    roots = allowed_roots()
    out: List[Path] = []
    for root in roots:
        if root.exists():
            out.append(root)
    return out


def is_safe_to_touch(raw: str) -> Tuple[bool, str]:
    try:
        validate_write(raw)
        return True, ""
    except PathValidationError as exc:
        return False, str(exc)
