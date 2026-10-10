"""System information tools. All values are measured from the actual system."""
from __future__ import annotations

import datetime as _dt
import logging
import os
import platform
import socket
from pathlib import Path
from typing import Any, Dict, List

from jarvis.tools.tool_registry import Tool, ToolRegistry

log = logging.getLogger("jarvis.tools.system")

try:
    import psutil  # type: ignore
except ImportError:      # pragma: no cover
    psutil = None  # type: ignore


def _psutil():
    if psutil is None:
        raise RuntimeError("psutil is not installed. Run: pip install psutil")
    return psutil


# ---------------------------------------------------------------------------
# Real system metrics
# ---------------------------------------------------------------------------

def get_cpu_usage() -> Dict[str, Any]:
    p = _psutil()
    per_core = p.cpu_percent(interval=0.15, percpu=True)
    overall = sum(per_core) / max(len(per_core), 1)
    return {
        "success": True,
        "tool": "get_cpu_usage",
        "result": {
            "percent": round(overall, 1),
            "per_core": [round(x, 1) for x in per_core],
            "cores": len(per_core),
        },
    }


def get_memory_usage() -> Dict[str, Any]:
    p = _psutil()
    vm = p.virtual_memory()
    return {
        "success": True,
        "tool": "get_memory_usage",
        "result": {
            "percent": round(vm.percent, 1),
            "used_gb": round(vm.used / 1e9, 2),
            "total_gb": round(vm.total / 1e9, 2),
            "available_gb": round(vm.available / 1e9, 2),
        },
    }


def get_disk_usage() -> Dict[str, Any]:
    p = _psutil()
    disks: List[Dict[str, Any]] = []
    for part in p.disk_partitions(all=False):
        usage = None
        if part.fstype:
            try:
                u = p.disk_usage(part.mountpoint)
                usage = {
                    "total_gb": round(u.total / 1e9, 1),
                    "used_gb": round(u.used / 1e9, 1),
                    "percent": round(u.percent, 1),
                }
            except (PermissionError, OSError):
                usage = None
        disks.append({
            "device": part.device,
            "mountpoint": part.mountpoint,
            "fstype": part.fstype,
            "usage": usage,
        })
    return {"success": True, "tool": "get_disk_usage", "result": {"disks": disks}}


def get_battery_status() -> Dict[str, Any]:
    p = _psutil()
    batt = p.sensors_battery()
    if batt is None:
        return {"success": True, "tool": "get_battery_status",
                "result": {"present": False,
                           "detail": "No battery detected (desktop or AC-only)."}}
    return {
        "success": True,
        "tool": "get_battery_status",
        "result": {
            "present": True,
            "percent": round(batt.percent, 0),
            "plugged_in": bool(batt.power_plugged),
        },
    }


def get_network_status() -> Dict[str, Any]:
    p = _psutil()
    internet = False
    try:
        sock = socket.create_connection(("1.1.1.1", 53), timeout=1.5)
        sock.close()
        internet = True
    except OSError:
        internet = False
    interfaces = [name for name, addrs in p.net_if_addrs().items() if addrs]
    return {
        "success": True, "tool": "get_network_status",
        "result": {
            "internet": internet,
            "interfaces": interfaces,
            "hostname": socket.gethostname(),
        },
    }


def get_current_time() -> Dict[str, Any]:
    now = _dt.datetime.now()
    return {
        "success": True, "tool": "get_current_time",
        "result": {
            "iso": now.isoformat(timespec="seconds"),
            "human": now.strftime("%A, %d %B %Y at %H:%M"),
            "timezone": now.astimezone().tzname() or "local",
        },
    }


def get_system_info() -> Dict[str, Any]:
    p = _psutil()
    vm = p.virtual_memory()
    cpu = p.cpu_percent(interval=0.1)
    boot = _dt.datetime.fromtimestamp(p.boot_time())
    return {
        "success": True, "tool": "get_system_info",
        "result": {
            "os": f"{platform.system()} {platform.release()}",
            "host": socket.gethostname(),
            "user": os.getenv("USERNAME", "unknown"),
            "cpu_percent": round(cpu, 1),
            "ram_percent": round(vm.percent, 1),
            "ram_used_gb": round(vm.used / 1e9, 2),
            "ram_total_gb": round(vm.total / 1e9, 2),
            "uptime_hours": round((_dt.datetime.now() - boot).total_seconds() / 3600, 1),
        },
    }


# ---------------------------------------------------------------------------
# Installed application discovery (Start Menu .lnk parsing)
# ---------------------------------------------------------------------------

def _start_menu_dirs() -> List[Path]:
    dirs = []
    appdata = os.getenv("APPDATA")
    programdata = os.getenv("PROGRAMDATA", r"C:\ProgramData")
    if appdata:
        dirs.append(Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    dirs.append(Path(programdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return [d for d in dirs if d.exists()]


def collect_shortcuts(max_depth: int = 4) -> List[Path]:
    """Recursively find .lnk files in the Start Menu."""
    found: List[Path] = []
    for base in _start_menu_dirs():
        for root, dirnames, filenames in os.walk(base):
            depth = len(Path(root).relative_to(base).parts)
            if depth >= max_depth:
                dirnames[:] = []
            for fname in filenames:
                if fname.lower().endswith(".lnk"):
                    found.append(Path(root) / fname)
    return found


def parse_lnk(path: Path) -> Dict[str, str]:
    """Minimal real Windows .lnk (Shell Link) binary parser.

    Extracts name, target path, arguments and working dir. Pure Python,
    no external dependencies. Handles the common cases (local targets with
    LinkInfo and/or relative link strings).
    """
    out = {"name": path.stem, "target": "", "args": "", "workdir": ""}
    try:
        data = path.read_bytes()
    except OSError:
        return out
    if len(data) < 0x4C or data[:4] != b"\x4C\x00\x00\x00":
        return out

    flags = int.from_bytes(data[0x14:0x18], "little")

    # -- LinkTargetIDList (present when flags & 1) --
    offset = 0x4C
    if flags & 0x00000001:
        if len(data) < offset + 2:
            return out
        id_list_size = int.from_bytes(data[offset:offset + 2], "little")
        offset += 2 + id_list_size

    # -- LinkInfo (present when flags & 2): holds the local target path --
    if flags & 0x00000002:
        if len(data) < offset + 4:
            return out
        link_info_size = int.from_bytes(data[offset:offset + 4], "little")
        if link_info_size > 0 and offset + link_info_size <= len(data):
            link_info = data[offset:offset + link_info_size]
            if len(link_info) >= 0x10:
                local_base = int.from_bytes(link_info[0x10:0x14], "little")
                common_path_off = int.from_bytes(link_info[0x18:0x1C], "little")
                # LocalBasePath is ANSI
                if 0x14 <= local_base < len(link_info):
                    end = link_info.find(b"\x00", local_base)
                    if end == -1:
                        end = len(link_info)
                    base = link_info[local_base:end].decode("mbcs", "replace")
                    common = ""
                    if 0x1C <= common_path_off < len(link_info):
                        end2 = link_info.find(b"\x00", common_path_off)
                        if end2 == -1:
                            end2 = len(link_info)
                        common = link_info[common_path_off:end2].decode("mbcs", "replace")
                    target = base.rstrip("\\")
                    if common:
                        target = target + "\\" + common if not base.endswith(common) else target
                    out["target"] = target
        offset += link_info_size if link_info_size else 0
    else:
        # No LinkInfo: try to find an embedded relative path string later
        pass

    # -- StringData: NAME, RELATIVE_PATH, WORKING_DIR, COMMAND_LINE_ARGS --
    string_flags = flags >> 7  # bits: 0x80 name, 0x100 relative, 0x200 workdir, 0x400 args
    has_name = flags & 0x00000080
    has_rel = flags & 0x00000100
    has_work = flags & 0x00000200
    has_args = flags & 0x00000400

    def read_string(off: int, unicode_flag: bool):
        if len(data) < off + 2:
            return "", off
        cc = int.from_bytes(data[off:off + 2], "little")
        off += 2
        if unicode_flag:
            size = cc * 2
            raw = data[off:off + size]
            val = raw.decode("utf-16-le", "replace")
        else:
            size = cc
            raw = data[off:off + size]
            val = raw.decode("mbcs", "replace")
        return val.rstrip("\x00"), off + size

    # IsUnicode flag lives in bit 7 of header flags (0x80) — but that is the
    # HasName flag. The unicode flag is bit 7 of LinkFlags per spec: 0x80.
    # In practice most .lnk created by modern Windows use unicode strings
    # when the IS_UNICODE flag (0x80 of LinkFlags) is set. We detect via a
    # heuristic: try unicode first if the count looks plausible.
    def read_smart(off: int):
        # try unicode, fall back to mbcs
        if len(data) < off + 2:
            return "", off
        cc = int.from_bytes(data[off:off + 2], "little")
        size_u = cc * 2
        if off + 2 + size_u <= len(data):
            val = data[off + 2:off + 2 + size_u].decode("utf-16-le", "replace")
            if val.rstrip("\x00").isprintable() and len(val.rstrip("\x00")) > 0:
                return val.rstrip("\x00"), off + 2 + size_u
        size_a = cc
        if off + 2 + size_a <= len(data):
            val = data[off + 2:off + 2 + size_a].decode("mbcs", "replace")
            return val.rstrip("\x00"), off + 2 + size_a
        return "", off

    cursor = offset
    if has_name:
        _, cursor = read_smart(cursor)
    if has_rel:
        rel, cursor = read_smart(cursor)
        if rel and not out["target"]:
            out["target"] = rel
    if has_work:
        work, cursor = read_smart(cursor)
        out["workdir"] = work
    if has_args:
        args, _cursor = read_smart(cursor)
        out["args"] = args
    return out


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def register(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="get_cpu_usage",
        description="Get current CPU usage percentage (real measurement).",
        args_schema={"type": "object", "properties": {}, "required": []},
        executor_fn=get_cpu_usage,
        risk="safe", category="system", timeout_s=10,
    ))
    registry.register(Tool(
        name="get_memory_usage",
        description="Get current RAM usage: percent used, used GB, total GB.",
        args_schema={"type": "object", "properties": {}, "required": []},
        executor_fn=get_memory_usage,
        risk="safe", category="system", timeout_s=10,
    ))
    registry.register(Tool(
        name="get_disk_usage",
        description="Get disk space usage for all drives.",
        args_schema={"type": "object", "properties": {}, "required": []},
        executor_fn=get_disk_usage,
        risk="safe", category="system", timeout_s=10,
    ))
    registry.register(Tool(
        name="get_battery_status",
        description="Get battery percentage and charging status.",
        args_schema={"type": "object", "properties": {}, "required": []},
        executor_fn=get_battery_status,
        risk="safe", category="system", timeout_s=10,
    ))
    registry.register(Tool(
        name="get_network_status",
        description="Check internet connectivity and list network interfaces.",
        args_schema={"type": "object", "properties": {}, "required": []},
        executor_fn=get_network_status,
        risk="safe", category="system", timeout_s=10,
    ))
    registry.register(Tool(
        name="get_current_time",
        description="Get the current date and time.",
        args_schema={"type": "object", "properties": {}, "required": []},
        executor_fn=get_current_time,
        risk="safe", category="system", timeout_s=5,
    ))
    registry.register(Tool(
        name="get_system_info",
        description="Get a system overview: OS, CPU, RAM, uptime, user.",
        args_schema={"type": "object", "properties": {}, "required": []},
        executor_fn=get_system_info,
        risk="safe", category="system", timeout_s=10,
    ))
