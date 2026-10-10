"""Filesystem tools with strict path safety.

All paths go through jarvis.security.validator which enforces allowed roots,
protected paths and traversal protection. Deletions always require
confirmation via the permission manager (risk level HIGH_RISK).
"""
from __future__ import annotations

import fnmatch
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from jarvis.security.validator import PathValidationError, normalize, validate_delete, validate_write
from jarvis.tools.tool_registry import Tool, ToolRegistry

log = logging.getLogger("jarvis.tools.filesystem")

_IGNORED_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv",
                 "AppData", "$Recycle.Bin", "System Volume Information",
                 ".idea", ".vscode", "dist", "build", ".next"}
MAX_SEARCH_RESULTS = 40
MAX_SEARCH_SECONDS = 10.0


def _err(tool: str, message: str) -> Dict[str, Any]:
    return {"success": False, "tool": tool, "error": message}


# ---------------------------------------------------------------------------
# Implementations
# ---------------------------------------------------------------------------

def list_directory(path: str = "", show_hidden: bool = False) -> Dict[str, Any]:
    tool = "list_directory"
    try:
        target = validate_write(path or str(Path.home()), must_exist=True)
    except PathValidationError as exc:
        return _err(tool, str(exc))
    if not target.is_dir():
        return _err(tool, f"Not a directory: {target}")
    try:
        entries = list(target.iterdir())
    except OSError as exc:
        return _err(tool, f"Cannot read directory: {exc}")
    items: List[Dict[str, Any]] = []
    for entry in entries[:500]:
        name = entry.name
        if not show_hidden and name.startswith(".") and name != "..":
            continue
        try:
            is_dir = entry.is_dir()
            size = entry.stat().st_size if not is_dir else 0
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        items.append({"name": name, "type": "dir" if is_dir else "file", "size": size,
                      "modified": mtime})
    items.sort(key=lambda x: (x["type"] != "dir", x["name"].lower()))
    return {"success": True, "tool": tool,
            "result": {"path": str(target), "count": len(items), "entries": items[:200]}}


def search_files(query: str, root: str = "", pattern: str = "*") -> Dict[str, Any]:
    tool = "search_files"
    if not query and pattern == "*":
        return _err(tool, "Provide a file name query or pattern.")
    roots: List[Path] = []
    if root:
        try:
            base = normalize(root)
        except PathValidationError as exc:
            return _err(tool, str(exc))
        if not base.exists():
            return _err(tool, f"Root does not exist: {base}")
        roots = [base]
    else:
        from jarvis.security.validator import search_roots
        roots = search_roots()
    q = query.lower().strip()
    results: List[Dict[str, Any]] = []
    started = time.time()
    for base in roots:
        if len(results) >= MAX_SEARCH_RESULTS or time.time() - started > MAX_SEARCH_SECONDS:
            break
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS]
            if time.time() - started > MAX_SEARCH_SECONDS:
                break
            for fname in filenames:
                low = fname.lower()
                if q and q not in low:
                    continue
                if not fnmatch.fnmatch(low, pattern.lower()):
                    continue
                full = Path(dirpath) / fname
                try:
                    stat = full.stat()
                except OSError:
                    continue
                results.append({
                    "name": fname,
                    "path": str(full),
                    "size_kb": round(stat.st_size / 1024, 1),
                    "modified": stat.st_mtime,
                    "ext": full.suffix.lower(),
                })
                if len(results) >= MAX_SEARCH_RESULTS:
                    break
            if len(results) >= MAX_SEARCH_RESULTS:
                break
    return {"success": True, "tool": tool,
            "result": {"query": query, "matches": results, "truncated": len(results) >= MAX_SEARCH_RESULTS}}


def read_file(path: str, max_bytes: int = 200_000) -> Dict[str, Any]:
    tool = "read_file"
    try:
        target = validate_write(path, must_exist=True)  # same rules as write for allowed roots
    except PathValidationError as exc:
        return _err(tool, str(exc))
    if not target.is_file():
        return _err(tool, f"Not a file: {target}")
    size = target.stat().st_size
    if size > max_bytes * 4:
        return _err(tool, f"File too large to read ({size // 1024} KB). Limit is {max_bytes * 4 // 1024} KB.")
    try:
        data = target.read_bytes()[:max_bytes]
    except OSError as exc:
        return _err(tool, f"Cannot read file: {exc}")
    try:
        text = data.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        text = data.decode("latin-1", "replace")
        encoding = "latin-1"
    preview = text[:20000]
    return {"success": True, "tool": tool,
            "result": {"path": str(target), "size": size, "encoding": encoding,
                       "content": preview, "truncated": size > len(preview)}}


def create_file(path: str, content: str = "") -> Dict[str, Any]:
    tool = "create_file"
    try:
        target = validate_write(path)
    except PathValidationError as exc:
        return _err(tool, str(exc))
    if target.exists():
        return _err(tool, f"File already exists: {target}. Use edit_file to change it.")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return _err(tool, f"Cannot create file: {exc}")
    return {"success": True, "tool": tool,
            "result": {"path": str(target), "bytes": len(content.encode("utf-8"))}}


def edit_file(path: str, content: str = "", append: bool = False) -> Dict[str, Any]:
    tool = "edit_file"
    try:
        target = validate_write(path)
    except PathValidationError as exc:
        return _err(tool, str(exc))
    if not target.exists():
        return _err(tool, f"File does not exist: {target}. Use create_file first.")
    try:
        if append:
            with open(target, "a", encoding="utf-8") as fh:
                fh.write(content)
        else:
            target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return _err(tool, f"Cannot edit file: {exc}")
    mode = "appended to" if append else "overwrote"
    return {"success": True, "tool": tool,
            "result": {"path": str(target), "action": mode, "bytes": len(content.encode("utf-8"))}}


def create_folder(path: str) -> Dict[str, Any]:
    tool = "create_folder"
    try:
        target = validate_write(path)
    except PathValidationError as exc:
        return _err(tool, str(exc))
    if target.exists():
        if target.is_dir():
            return {"success": True, "tool": tool,
                    "result": {"path": str(target), "already_existed": True}}
        return _err(tool, f"A file with that name exists: {target}")
    try:
        target.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        return _err(tool, f"Cannot create folder: {exc}")
    return {"success": True, "tool": tool, "result": {"path": str(target)}}


def delete_file(path: str) -> Dict[str, Any]:
    """Delete a file or folder (recursively). Always requires confirmation."""
    tool = "delete_file"
    try:
        target = validate_delete(path)
    except PathValidationError as exc:
        return _err(tool, str(exc))
    try:
        if target.is_dir():
            action = "folder (recursive)"
            try:
                shutil.rmtree(target)
            except PermissionError:
                # Windows: transient locks (AV/OneDrive) or read-only attrs.
                import stat as _stat
                import time as _time
                _time.sleep(0.5)
                def _unlock(f, p, _exc):
                    try:
                        os.chmod(p, _stat.S_IWRITE)
                        f(p)
                    except OSError:
                        pass
                try:
                    shutil.rmtree(target, onerror=_unlock)
                except TypeError:  # Python >=3.12 deprecation fallback
                    shutil.rmtree(target, onexc=_unlock)
                if target.exists():
                    raise PermissionError("target still locked after retry")
        else:
            target.unlink()
            action = "file"
    except OSError as exc:
        return _err(tool, f"Delete failed: {exc}")
    log.info("deleted %s %s", action, target)
    return {"success": True, "tool": tool,
            "result": {"path": str(target), "deleted": action}}


def copy_file(src: str, dst: str) -> Dict[str, Any]:
    tool = "copy_file"
    try:
        s = validate_write(src, must_exist=True)
        d = validate_write(dst)
    except PathValidationError as exc:
        return _err(tool, str(exc))
    if not s.is_file():
        return _err(tool, f"Source is not a file: {s}")
    try:
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(s), str(d))
    except OSError as exc:
        return _err(tool, f"Copy failed: {exc}")
    return {"success": True, "tool": tool, "result": {"from": str(s), "to": str(d)}}


def move_file(src: str, dst: str) -> Dict[str, Any]:
    tool = "move_file"
    try:
        s = validate_write(src, must_exist=True)
        d = validate_write(dst)
    except PathValidationError as exc:
        return _err(tool, str(exc))
    if s == d:
        return _err(tool, "Source and destination are the same.")
    try:
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(s), str(d))
    except OSError as exc:
        return _err(tool, f"Move failed: {exc}")
    return {"success": True, "tool": tool, "result": {"from": str(s), "to": str(d)}}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def register(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="list_directory",
        description="List the contents of a directory.",
        args_schema={"type": "object", "properties": {
            "path": {"type": "string", "description": "Directory path (default: home)"},
            "show_hidden": {"type": "boolean"},
        }, "required": []},
        executor_fn=list_directory, risk="safe", category="filesystem", timeout_s=15,
    ))
    registry.register(Tool(
        name="search_files",
        description="Search files by name across allowed locations (Desktop, Documents, Downloads...).",
        args_schema={"type": "object", "properties": {
            "query": {"type": "string", "description": "Filename substring to search for"},
            "root": {"type": "string", "description": "Optional root to search under"},
            "pattern": {"type": "string", "description": "Optional glob pattern, e.g. *.pdf"},
        }, "required": []},
        executor_fn=search_files, risk="safe", category="filesystem", timeout_s=25,
    ))
    registry.register(Tool(
        name="read_file",
        description="Read a text file's content.",
        args_schema={"type": "object", "properties": {
            "path": {"type": "string", "required": True},
        }, "required": ["path"]},
        executor_fn=read_file, risk="safe", category="filesystem", timeout_s=15,
    ))
    registry.register(Tool(
        name="create_file",
        description="Create a new text file with content.",
        args_schema={"type": "object", "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        }, "required": ["path"]},
        executor_fn=create_file, risk="confirm", category="filesystem", timeout_s=15,
        confirmation_title="Create file",
        confirmation_description="A new file will be created on disk.",
    ))
    registry.register(Tool(
        name="edit_file",
        description="Overwrite or append to an existing text file.",
        args_schema={"type": "object", "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
            "append": {"type": "boolean"},
        }, "required": ["path", "content"]},
        executor_fn=edit_file, risk="confirm", category="filesystem", timeout_s=15,
        confirmation_title="Edit file",
        confirmation_description="An existing file will be modified.",
    ))
    registry.register(Tool(
        name="create_folder",
        description="Create a new folder.",
        args_schema={"type": "object", "properties": {
            "path": {"type": "string"},
        }, "required": ["path"]},
        executor_fn=create_folder, risk="safe", category="filesystem", timeout_s=15,
    ))
    registry.register(Tool(
        name="delete_file",
        description="Delete a file or folder (recursive). ALWAYS requires user approval.",
        args_schema={"type": "object", "properties": {
            "path": {"type": "string"},
        }, "required": ["path"]},
        executor_fn=delete_file, risk="high-risk", category="filesystem", timeout_s=30,
        confirmation_title="Delete file/folder",
        confirmation_description="Files will be permanently deleted. This cannot be undone.",
    ))
    registry.register(Tool(
        name="copy_file",
        description="Copy a file to a new location.",
        args_schema={"type": "object", "properties": {
            "src": {"type": "string"}, "dst": {"type": "string"},
        }, "required": ["src", "dst"]},
        executor_fn=copy_file, risk="confirm", category="filesystem", timeout_s=30,
        confirmation_title="Copy file",
        confirmation_description="A file will be copied to a new location.",
    ))
    registry.register(Tool(
        name="move_file",
        description="Move/rename a file.",
        args_schema={"type": "object", "properties": {
            "src": {"type": "string"}, "dst": {"type": "string"},
        }, "required": ["src", "dst"]},
        executor_fn=move_file, risk="confirm", category="filesystem", timeout_s=30,
        confirmation_title="Move file",
        confirmation_description="A file will be moved or renamed.",
    ))
