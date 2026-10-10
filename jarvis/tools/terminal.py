"""Controlled terminal execution.

Pipeline: LLM proposes command -> command_policy.classify -> permission
decision (auto / confirm / blocked) -> subprocess with timeout and output cap
-> result verification (exit code, server URL detection).
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from jarvis.config.settings import get_settings
from jarvis.tools.tool_registry import Tool, ToolRegistry

log = logging.getLogger("jarvis.tools.terminal")

_URL_RE = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|[\w.-]+)(?::\d+)?[^\s\"']*", re.I)
_LOCAL_URL_RE = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0)(?::(\d+))?")
_ACTIVE_SERVERS: Dict[str, "ManagedProcess"] = {}


class ManagedProcess:
    """A long-running process owned by the terminal tool (e.g. dev server)."""

    def __init__(self, cmd: List[str], cwd: str, purpose: str):
        self.cmd = cmd
        self.cwd = cwd
        self.purpose = purpose
        self.process: Optional[subprocess.Popen] = None
        self.output: List[str] = []
        self.lock = threading.Lock()
        self.started_at = time.time()

    def start(self) -> None:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.process = subprocess.Popen(
            self.cmd, cwd=self.cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", creationflags=flags,
        )
        reader = threading.Thread(target=self._read_output, daemon=True)
        reader.start()

    def _read_output(self) -> None:
        if not self.process or not self.process.stdout:
            return
        for line in self.process.stdout:
            with self.lock:
                self.output.append(line.rstrip())
                if len(self.output) > 400:
                    del self.output[:200]

    def recent_output(self, n: int = 20) -> List[str]:
        with self.lock:
            return list(self.output[-n:])

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()


def wait_for_http(url: str, timeout: float = 20.0) -> bool:
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status < 500:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    return False


def find_local_urls(text: str) -> List[str]:
    """Extract localhost URLs from process output."""
    urls = _URL_RE.findall(text)
    seen: List[str] = []
    for u in urls:
        if u not in seen and _LOCAL_URL_RE.search(u):
            seen.append(u)
    return seen


def run_terminal_command(
    command: str,
    cwd: str = "",
    wait_seconds: Optional[float] = None,
    keep_running: bool = False,
    purpose: str = "",
) -> Dict[str, Any]:
    tool = "run_terminal_command"
    cmd = (command or "").strip()
    if not cmd:
        return {"success": False, "tool": tool, "error": "Empty command"}

    settings = get_settings()
    if wait_seconds is None:
        wait_seconds = float(settings.get("terminal.default_wait_seconds", 30))
    wait_seconds = min(wait_seconds, float(settings.get("terminal.max_wait_seconds", 120)))

    workdir = cwd or str(Path.home())
    if not Path(workdir).exists():
        return {"success": False, "tool": tool, "error": f"Working directory does not exist: {workdir}"}

    # detect server-style commands: npm run dev / npm start / python -m http.server etc
    lowered = cmd.lower()
    server_like = any(tok in lowered for tok in
                      ("npm run dev", "npm start", "vite", "next dev", "flask run",
                       "uvicorn", "http.server", "serve", "ng serve", "hugo server"))

    started = time.perf_counter()
    try:
        if server_like or keep_running:
            proc = ManagedProcess(cmd.split() if os.name == "nt" else cmd.split(), workdir,
                                  purpose or cmd)
            try:
                proc.start()
            except OSError as exc:
                return {"success": False, "tool": tool, "error": f"Failed to start: {exc}"}
            _ACTIVE_SERVERS[cmd] = proc

            # give the process time to boot, watch for a local URL
            wait_s = min(wait_seconds, 30.0)
            deadline = time.time() + wait_s
            url: Optional[str] = None
            while time.time() < deadline:
                url = find_local_urls("\n".join(proc.recent_output(50)))
                if url:
                    break
                if not proc.alive():
                    break
                time.sleep(0.5)

            out = proc.recent_output(40)
            if url:
                full_url = url[0]
                port_ok = wait_for_http(full_url, timeout=10)
                return {
                    "success": bool(port_ok and proc.alive()),
                    "tool": tool,
                    "result": {
                        "command": cmd,
                        "mode": "managed-server",
                        "url": full_url if port_ok else None,
                        "url_verified": port_ok,
                        "server_running": proc.alive(),
                        "pid": proc.process.pid if proc.process else None,
                        "output": out,
                    },
                    "error": None if (port_ok and proc.alive()) else
                             ("Server process exited unexpectedly" if not proc.alive()
                              else f"Started but {full_url} did not respond"),
                }
            # no URL yet — server may still be OK (non-http servers)
            still = proc.alive()
            return {
                "success": still,
                "tool": tool,
                "result": {
                    "command": cmd,
                    "mode": "managed-process",
                    "server_running": still,
                    "pid": proc.process.pid if proc.process else None,
                    "output": out,
                },
                "error": None if still else "Process exited immediately; check output.",
            }

        # one-shot command with timeout
        shell = os.name == "nt"
        completed = subprocess.run(
            cmd, cwd=workdir, shell=shell, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=wait_seconds,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        stdout = (completed.stdout or "")[-8000:]
        stderr = (completed.stderr or "")[-4000:]
        success = completed.returncode == 0
        return {
            "success": success,
            "tool": tool,
            "result": {
                "command": cmd,
                "mode": "one-shot",
                "exit_code": completed.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "output": stdout or stderr,
            },
            "error": None if success else f"Command exited with code {completed.returncode}",
            "duration_ms": duration_ms,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "tool": tool,
                "error": f"Command timed out after {wait_seconds:.0f}s"}
    except OSError as exc:
        return {"success": False, "tool": tool, "error": f"OS error: {exc}"}


def list_managed_servers() -> Dict[str, Any]:
    procs = []
    for key, proc in _ACTIVE_SERVERS.items():
        procs.append({
            "command": key,
            "purpose": proc.purpose,
            "alive": proc.alive(),
            "pid": proc.process.pid if proc.process else None,
            "recent_output": proc.recent_output(10),
        })
    return {"success": True, "tool": "list_managed_servers", "result": {"servers": procs}}


def stop_managed_server(command: str = "") -> Dict[str, Any]:
    if command and command in _ACTIVE_SERVERS:
        _ACTIVE_SERVERS.pop(command).stop()
        return {"success": True, "tool": "stop_managed_server", "result": {"stopped": command}}
    for key, proc in list(_ACTIVE_SERVERS.items()):
        proc.stop()
    _ACTIVE_SERVERS.clear()
    return {"success": True, "tool": "stop_managed_server", "result": {"stopped": "all"}}


def register(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="run_terminal_command",
        description="Run a shell command in a given directory. Risk-classified; dangerous "
                    "commands require approval. Long-running dev-server style commands are "
                    "kept alive and their localhost URL is detected and verified.",
        args_schema={"type": "object", "properties": {
            "command": {"type": "string", "description": "The command to run"},
            "cwd": {"type": "string", "description": "Working directory"},
            "wait_seconds": {"type": "number", "description": "Max seconds to wait for output"},
            "keep_running": {"type": "boolean", "description": "Keep long-running process alive (dev server)"},
            "purpose": {"type": "string", "description": "Human-readable purpose"},
        }, "required": ["command"]},
        executor_fn=run_terminal_command,
        risk="dynamic", category="terminal", timeout_s=150,
    ))
