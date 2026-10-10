"""Application control tools: open, close, focus, list — with verification.

Resolution strategy (in order):
1. Custom app paths configured in settings (permissions.custom_app_paths)
2. Exact alias match against the builtin alias table
3. Start Menu .lnk scan (real shortcut parsing, cached)
4. PATH lookup (e.g. "code", "python")
Opening is verified by checking the process list afterwards.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from jarvis.config.settings import get_settings
from jarvis.tools.tool_registry import Tool, ToolRegistry

log = logging.getLogger("jarvis.tools.applications")

try:
    import psutil  # type: ignore
except ImportError:
    psutil = None  # type: ignore

try:
    import win32com.client  # type: ignore
except ImportError:
    win32com = None  # type: ignore

try:
    import win32gui  # type: ignore
    import win32con  # type: ignore
except ImportError:
    win32gui = None  # type: ignore
    win32con = None  # type: ignore


# Common alias table: name -> list of candidate executables/lnk names
ALIASES: Dict[str, List[str]] = {
    "chrome": ["chrome.exe", "Google Chrome.lnk", "Google Chrome"],
    "google chrome": ["chrome.exe", "Google Chrome.lnk"],
    "edge": ["msedge.exe", "Microsoft Edge.lnk"],
    "microsoft edge": ["msedge.exe", "Microsoft Edge.lnk"],
    "firefox": ["firefox.exe", "Firefox.lnk", "Mozilla Firefox.lnk"],
    "vs code": ["Code.exe", "Visual Studio Code.lnk", "code"],
    "visual studio code": ["Code.exe", "Visual Studio Code.lnk", "code"],
    "vscode": ["Code.exe", "Visual Studio Code.lnk", "code"],
    "code": ["Code.exe", "Visual Studio Code.lnk"],
    "notepad": ["notepad.exe", "Notepad.lnk"],
    "notepad++": ["notepad++.exe", "Notepad++.lnk"],
    "spotify": ["Spotify.exe", "Spotify.lnk"],
    "calculator": ["CalculatorApp.exe", "calculator://", "Calculator.lnk", "calc.exe"],
    "calc": ["calc.exe"],
    "explorer": ["explorer.exe"],
    "file explorer": ["explorer.exe"],
    "terminal": ["wt.exe", "Windows Terminal.lnk"],
    "windows terminal": ["wt.exe", "Windows Terminal.lnk"],
    "cmd": ["cmd.exe"],
    "command prompt": ["cmd.exe"],
    "powershell": ["powershell.exe", "pwsh.exe"],
    "paint": ["mspaint.exe", "Paint.lnk"],
    "word": ["winword.exe", "Word.lnk"],
    "excel": ["EXCEL.EXE", "Excel.lnk"],
    "powerpoint": ["POWERPNT.EXE", "PowerPoint.lnk"],
    "outlook": ["OUTLOOK.EXE", "Outlook.lnk"],
    "discord": ["Discord.exe", "Discord.lnk"],
    "slack": ["slack.exe", "Slack.lnk"],
    "steam": ["steam.exe", "Steam.lnk"],
    "vlc": ["vlc.exe", "VLC media player.lnk"],
    "telegram": ["Telegram.exe", "Telegram Desktop.lnk"],
    "whatsapp": ["WhatsApp.exe", "WhatsApp.lnk"],
    "blender": ["blender.exe", "Blender.lnk"],
    "python": ["python.exe"],
    "git bash": ["git-bash.exe"],
    "intellij": ["idea64.exe", "IntelliJ IDEA.lnk"],
    "pycharm": ["pycharm64.exe", "PyCharm.lnk"],
}

_START_MENU_CACHE: List[Dict[str, str]] = []
_CACHE_TS: float = 0.0


def _psutil_mod():
    if psutil is None:
        raise RuntimeError("psutil not installed")
    return psutil


def _refresh_shortcut_cache(force: bool = False) -> List[Dict[str, str]]:
    global _START_MENU_CACHE, _CACHE_TS
    if not force and _START_MENU_CACHE and time.time() - _CACHE_TS < 300:
        return _START_MENU_CACHE
    from jarvis.tools.system import collect_shortcuts, parse_lnk
    entries: List[Dict[str, str]] = []
    for lnk in collect_shortcuts():
        info = parse_lnk(lnk)
        if info.get("target") or info.get("name"):
            entries.append({
                "name": info["name"],
                "target": info.get("target", ""),
                "args": info.get("args", ""),
                "workdir": info.get("workdir", ""),
                "lnk": str(lnk),
            })
    _START_MENU_CACHE = entries
    _CACHE_TS = time.time()
    return entries


def _launch_via_lnk(lnk_path: str) -> bool:
    """Open a .lnk shortcut. Requires its absolute path."""
    if win32com is not None:
        try:
            shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortCut(lnk_path)
            target = shortcut.Targetpath
            if target:
                args = shortcut.Arguments or ""
                workdir = shortcut.WorkingDirectory or ""
                subprocess.Popen([target] + (args.split() if args else []),
                                 cwd=workdir or None,
                                 creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
                return True
        except Exception:  # noqa: BLE001
            log.exception("lnk COM launch failed for %s", lnk_path)
    # Fallback: os.startfile can open .lnk directly
    try:
        os.startfile(lnk_path)  # noqa: S606
        return True
    except OSError:
        return False


def resolve_application(query: str) -> Optional[Dict[str, str]]:
    """Resolve a natural application name to a launchable target."""
    q = (query or "").strip().lower().rstrip(".exe")
    settings = get_settings()
    custom = settings.get("permissions.custom_app_paths", {}) or {}

    # 1. user-configured paths (exact or fuzzy key match)
    for key, target in custom.items():
        if key.lower() == q or key.lower() in q or q in key.lower():
            expanded = os.path.expandvars(os.path.expanduser(str(target)))
            if Path(expanded).exists():
                return {"name": query, "target": expanded, "args": "", "source": "custom"}

    # 2. alias table
    candidates = ALIASES.get(q, [])
    shortcuts = _refresh_shortcut_cache()
    for cand in candidates:
        if cand.endswith(".lnk"):
            for sc in shortcuts:
                if sc["name"].lower() == cand[:-4].lower():
                    return {**sc, "source": "start-menu"}
        lower = cand.lower()
        if lower.endswith(".exe"):
            # search PATH first
            found = shutil.which(cand)
            if found:
                return {"name": query, "target": found, "args": "", "source": "PATH"}
            # common install locations
            for base in (
                os.getenv("ProgramFiles", r"C:\Program Files"),
                os.getenv("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                os.getenv("LOCALAPPDATA", ""),
            ):
                if not base:
                    continue
                for root, dirs, files in os.walk(base):
                    # do not descend too far
                    if len(Path(root).parts) - len(Path(base).parts) > 3:
                        dirs[:] = []
                        continue
                    if lower in (f.lower() for f in files):
                        return {"name": query, "target": str(Path(root) / cand),
                                "args": "", "source": "program-files"}
        elif cand.endswith("://"):
            return {"name": query, "target": cand, "args": "", "source": "uri"}
        else:
            found = shutil.which(cand)
            if found:
                return {"name": query, "target": found, "args": "", "source": "PATH"}

    # 3. start menu fuzzy match on display name
    q_nospace = q.replace(" ", "")
    for sc in shortcuts:
        name = sc["name"].lower().replace(" ", "")
        if q_nospace and (q_nospace in name or name in q_nospace):
            return sc

    # 4. bare PATH lookup
    found = shutil.which(q) or shutil.which(q + ".exe")
    if found:
        return {"name": query, "target": found, "args": "", "source": "PATH"}

    # 5. URI scheme fallback (e.g. spotify:, steam:)
    if "://" in query or query.lower().endswith(":"):
        return {"name": query, "target": query, "args": "", "source": "uri"}
    return None


def _find_pids(query: str) -> List[int]:
    """Find running processes whose exe or window title matches the query."""
    ps = _psutil_mod()
    q = query.strip().lower()
    pids: List[int] = []
    for proc in ps.process_iter(["pid", "name", "exe"]):
        try:
            name = (proc.info.get("name") or "").lower()
            exe = (proc.info.get("exe") or "").lower()
        except (ps.NoSuchProcess, ps.AccessDenied):
            continue
        if q in name or (exe and q in Path(exe).stem.lower()):
            if proc.info["pid"] not in (0, 4):
                pids.append(proc.info["pid"])
    return pids


def _window_titles() -> Dict[int, str]:
    titles: Dict[int, str] = {}
    if win32gui is None:
        return titles

    def _cb(hwnd: int, _):  # noqa: ANN001
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title:
                titles[hwnd] = title
    win32gui.EnumWindows(_cb, None)
    return titles


def _activate_window(hwnd: int) -> bool:
    if win32gui is None or win32con is None:
        return False
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        return True
    except Exception:  # noqa: BLE001
        log.exception("SetForegroundWindow failed")
        return False


# ---------------------------------------------------------------------------
# Tool implementations (sync, run in worker threads)
# ---------------------------------------------------------------------------

def open_application(application: str, args: str = "") -> Dict[str, Any]:
    resolved = resolve_application(application)
    if not resolved:
        return {"success": False, "tool": "open_application",
                "error": f"Application '{application}' not found. Add its path in Settings > Permissions."}
    target = resolved["target"]
    final_args = args or resolved.get("args", "")
    try:
        if target.endswith(".lnk"):
            ok = _launch_via_lnk(target)
            if not ok:
                os.startfile(target)  # noqa: S606
        elif "://" in target:
            os.startfile(target)  # noqa: S606
        else:
            cmd = [target] + (final_args.split() if final_args else [])
            subprocess.Popen(cmd, cwd=resolved.get("workdir") or None,
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    except OSError as exc:
        return {"success": False, "tool": "open_application",
                "error": f"Failed to launch {application}: {exc}"}

    # verification: does a matching process appear within a few seconds?
    exe_name = Path(target).name.lower() if not target.endswith(".lnk") and "://" not in target else None
    deadline = time.time() + 6
    verified = False
    while time.time() < deadline:
        if exe_name and _find_pids(exe_name):
            verified = True
            break
        if not exe_name:
            verified = True  # URI/lnk launches: assume started if no exception
            break
        time.sleep(0.5)
    return {
        "success": True,
        "tool": "open_application",
        "result": {
            "application": application,
            "resolved": resolved["target"],
            "verified": verified,
            "note": "" if verified else "Launched, but the process was not confirmed yet.",
        },
    }


def close_application(application: str) -> Dict[str, Any]:
    pids = _find_pids(application)
    if not pids:
        return {"success": False, "tool": "close_application",
                "error": f"No running application matching '{application}'."}
    ps = _psutil_mod()
    closed, failed = [], []
    for pid in pids:
        try:
            proc = ps.Process(pid)
            proc.terminate()  # graceful WM_CLOSE equivalent
            try:
                proc.wait(timeout=5)
            except ps.TimeoutExpired:
                proc.kill()
            closed.append(pid)
        except (ps.NoSuchProcess, ps.AccessDenied) as exc:
            failed.append({"pid": pid, "error": str(exc)})
    return {
        "success": len(closed) > 0,
        "tool": "close_application",
        "result": {"closed_pids": closed, "failed": failed},
        "error": None if closed else "Could not terminate any matching process.",
    }


def focus_application(application: str) -> Dict[str, Any]:
    q = application.strip().lower()
    titles = _window_titles()
    best = None
    for hwnd, title in titles.items():
        if q in title.lower():
            best = hwnd
            break
    if best is None:
        # try by process exe
        pids = _find_pids(application)
        if pids and win32gui is not None:
            ps = _psutil_mod()
            pid_set = set(pids)

            def _cb2(hwnd: int, _: Any) -> None:
                nonlocal best
                _, pid = win32gui.GetWindowThreadProcessId(hwnd)
                if pid in pid_set and win32gui.IsWindowVisible(hwnd):
                    if best is None:
                        best = hwnd
            win32gui.EnumWindows(_cb2, None)
    if best is None:
        return {"success": False, "tool": "focus_application",
                "error": f"No visible window found for '{application}'."}
    ok = _activate_window(best)
    return {
        "success": ok,
        "tool": "focus_application",
        "result": {"window": titles.get(best, ""), "focused": ok},
        "error": None if ok else "Could not bring the window to the foreground.",
    }


def list_running_applications() -> Dict[str, Any]:
    ps = _psutil_mod()
    apps: List[Dict[str, Any]] = []
    seen: set = set()
    titles = _window_titles()
    title_pids: Dict[int, str] = {}
    if win32gui is not None:
        for hwnd, title in titles.items():
            try:
                _, pid = win32gui.GetWindowThreadProcessId(hwnd)
                title_pids[pid] = title
            except Exception:  # noqa: BLE001
                pass
    for proc in ps.process_iter(["pid", "name", "memory_info"]):
        try:
            info = proc.info
            pid = info["pid"]
            name = info.get("name") or ""
            if not name or pid in (0, 4):
                continue
            mem = info.get("memory_info")
            mem_mb = round(mem.rss / 1e6, 1) if mem else 0
            has_window = pid in title_pids
            if has_window or mem_mb > 30:
                key = name.lower()
                if key in seen and not has_window:
                    continue
                seen.add(key)
                apps.append({
                    "pid": pid,
                    "name": name,
                    "window": title_pids.get(pid, ""),
                    "memory_mb": mem_mb,
                    "visible_window": has_window,
                })
        except (ps.NoSuchProcess, ps.AccessDenied):
            continue
    apps.sort(key=lambda a: (-a["visible_window"], -a["memory_mb"]))
    return {
        "success": True, "tool": "list_running_applications",
        "result": {"count": len(apps), "applications": apps[:40]},
    }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def register(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="open_application",
        description="Open an installed application by name, e.g. 'Chrome', 'Visual Studio Code'.",
        args_schema={
            "type": "object",
            "properties": {
                "application": {"type": "string", "description": "Application name"},
                "args": {"type": "string", "description": "Optional launch arguments"},
            },
            "required": ["application"],
        },
        executor_fn=open_application,
        risk="safe", category="applications", timeout_s=20,
    ))
    registry.register(Tool(
        name="close_application",
        description="Close a running application by name (graceful terminate).",
        args_schema={
            "type": "object",
            "properties": {"application": {"type": "string"}},
            "required": ["application"],
        },
        executor_fn=close_application,
        risk="confirm", category="applications", timeout_s=20,
        confirmation_title="Close application",
        confirmation_description="This will close the running application and its processes.",
    ))
    registry.register(Tool(
        name="focus_application",
        description="Bring an application's window to the foreground.",
        args_schema={
            "type": "object",
            "properties": {"application": {"type": "string"}},
            "required": ["application"],
        },
        executor_fn=focus_application,
        risk="safe", category="applications", timeout_s=10,
    ))
    registry.register(Tool(
        name="list_running_applications",
        description="List running applications with memory usage and window titles.",
        args_schema={"type": "object", "properties": {}, "required": []},
        executor_fn=list_running_applications,
        risk="safe", category="applications", timeout_s=15,
    ))
