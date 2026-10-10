"""Rule-based offline intent engine.

When the local LLM runtime is unavailable, this module maps natural language
to tool calls so core computer control still works fully offline.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Patterns mapping user phrasings -> (tool, arguments)
# ---------------------------------------------------------------------------

NEED_APP_PATTERNS = [
    (re.compile(r"\b(?:i need|i want|can you open|could you open|get me|pull up)\b.*\b(chrome|edge|firefox|spotify|notepad(?:\+\+)?|vs ?code|vscode|calculator|terminal|cmd|powershell)\b", re.I),
     "open_application", {"application": "APP_EXTRACT"}),
]

APP_PATTERNS = [
    (re.compile(r"\b(?:open|launch|start|run)\b.*\b(chrome|google chrome)\b", re.I),
     "open_application", {"application": "chrome"}),
    (re.compile(r"\b(?:open|launch|start|run)\b.*\b(vs ?code|visual studio code|vscode)\b", re.I),
     "open_application", {"application": "Visual Studio Code"}),
    (re.compile(r"\b(?:open|launch|start|run)\b.*\bedge\b", re.I),
     "open_application", {"application": "edge"}),
    (re.compile(r"\b(?:open|launch|start|run)\b.*\bfirefox\b",  re.I),
     "open_application", {"application": "firefox"}),
    (re.compile(r"\b(?:open|launch|start)\b.*\bspotify\b", re.I),
     "open_application", {"application": "spotify"}),
    (re.compile(r"\b(?:open|launch|start)\b.*\b(?:calculator|calc)\b", re.I),
     "open_application", {"application": "calculator"}),
    (re.compile(r"\b(?:open|launch|start)\b.*\bnotepad\+\+?\b", re.I),
     "open_application", {"application": "notepad++"}),
    (re.compile(r"\b(?:open|launch|start)\b.*\bnotepad\b", re.I),
     "open_application", {"application": "notepad"}),
    (re.compile(r"\b(?:open|launch|start)\b.*\b(?:file explorer|explorer|my files)\b", re.I),
     "open_application", {"application": "explorer"}),
    (re.compile(r"\b(?:open|launch|start)\b.*\b(?:terminal|command prompt|cmd|powershell)\b", re.I),
     "open_application", {"application": "terminal"}),
    (re.compile(r"\bclose\b.*\b(chrome|spotify|notepad|vs ?code|vscode)\b", re.I),
     "close_application", {"application": "APP_EXTRACT"}),
    (re.compile(r"\b(?:focus|switch to|bring)\b.*\b(chrome|spotify|notepad|vs ?code|vscode)\b", re.I),
     "focus_application", {"application": "APP_EXTRACT"}),
    (re.compile(r"\b(?:what|which).*(?:apps|applications).*(?:running|open)\b|\blist.*(?:apps|applications)\b", re.I),
     "list_running_applications", {}),
]

GENERIC_OPEN_APP = re.compile(r"\b(?:open|launch|start)\s+(?:the\s+)?(?:app|application|program)\s+(?:called\s+)?[\"']?([\w .+-]+)[\"']?", re.I)

SYSTEM_PATTERNS = [
    (re.compile(r"\b(what('| i)?s|how much|current|tell me)\b.*\bram\b|\bram\b.*\busage\b", re.I),
     "get_memory_usage", {}),
    (re.compile(r"\bcpu\b", re.I),
     "get_cpu_usage", {}),
    (re.compile(r"\bdisk\b.*\bspace\b|\bstorage\b", re.I), "get_disk_usage", {}),
    (re.compile(r"\bbattery\b", re.I), "get_battery_status", {}),
    (re.compile(r"\b(?:am i|is there)\b.*\b(?:online|internet)\b|\binternet\b|\bnetwork\b", re.I),
     "get_network_status", {}),
    (re.compile(r"\b(?:what|which|current)\b.*\btime\b|\btime is it\b", re.I), "get_current_time", {}),
    (re.compile(r"\bscreenshot\b", re.I), "take_screenshot", {}),
]

FILE_PATTERNS = [
    (re.compile(r"\b(?:show|list|what)\b.*\bfiles\b.*\b(?:in|from|on)\b.*\b(downloads?|documents?|desktop|pictures?|videos?|music)\b", re.I),
     "list_directory", {"path": "KNOWN_FOLDER"}),
    (re.compile(r"\b(?:find|search|locate|look for)\b.*\b(?:my\s+)?(resume|cv)\b", re.I),
     "search_files", {"query": "resume"}),
    (re.compile(r"\b(?:find|search|locate|look for)\b.*\b(?:my\s+)?(?:file|files|documents?)\b(?:\s+(?:called|named)\s+([\w .-]+))?", re.I),
     "search_files", {"query": "QUERY_EXTRACT"}),
    (re.compile(r"\b(?:find|search|locate)\s+(?:for\s+)?[\"']?([\w .-]{2,60})[\"']?\s*(?:file|document)", re.I),
     "search_files", {"query": "GROUP1"}),
    (re.compile(r"\b(?:create|make)\b.*\bfolder\b(?:\s+(?:called|named)\s+[\"']?([A-Za-z0-9_ .-]+?)[\"']?\s*(?:\bon\b|\bin\b|\bunder\b|$))?", re.I),
     "create_folder", {"path": "FOLDER_ON_DESKTOP"}),
    (re.compile(r"\b(?:create|make)\b.*\bfile\b(?:\s+(?:called|named)\s+[\"']?([A-Za-z0-9_ .-]+?)[\"']?\s*(?:\bon\b|\bin\b|\bunder\b|\bwith\b|$))?", re.I),
     "create_file", {"path": "FILE_EXTRACT", "content": ""}),
]

DELETE_PATTERNS = [
    (re.compile(r"\b(?:delete|remove|trash)\b\s+(?:the\s+)?folder\s+(?:called\s+|named\s+)?[\"']?([A-Za-z0-9_ .-]+?)[\"']?\s*(?:\bon\b|\bin\b|\bunder\b|$)", re.I),
     "delete_file", {"path": "DELETE_TARGET"}),
    (re.compile(r"\b(?:delete|remove|trash)\b\s+(?:the\s+)?(?:file|files)\s+(?:called\s+|named\s+)?[\"']?([A-Za-z0-9_ .-]+?)[\"']?\s*(?:\bon\b|\bin\b|\bunder\b|$)", re.I),
     "delete_file", {"path": "DELETE_TARGET"}),
    # "delete the VoiceTest folder" / "delete report.txt file" (name before noun)
    (re.compile(r"\b(?:delete|remove|trash)\b\s+(?:the\s+)?[\"']?([A-Za-z0-9_ .-]+?)[\"']?\s+folder\b", re.I),
     "delete_file", {"path": "DELETE_TARGET"}),
    (re.compile(r"\b(?:delete|remove|trash)\b\s+(?:the\s+)?[\"']?([A-Za-z0-9_.-]+?)[\"']?\s+(?:file|files)\b", re.I),
     "delete_file", {"path": "DELETE_TARGET"}),
]

BROWSER_PATTERNS = [
    (re.compile(r"\b(?:open|go to|visit)\b\s+(?:the\s+)?(?:website\s+)?([a-z0-9-]+(?:\.[a-z]{2,})+)", re.I),
     "open_url", {"url": "GROUP1"}),
    (re.compile(r"\b(?:search|google|look up)\b\s+(?:the web\s+)?(?:for\s+)?[\"']?(.+?)[\"']?\s*$", re.I),
     "search_web", {"query": "GROUP1"}),
]

MEMORY_PATTERNS = [
    (re.compile(r"\bwhat do you (?:know|remember)\b|\b(?:show|list).*(?:memory|remember)", re.I),
     "retrieve_memory", {}),
]

STOP_WORDS = r"(?:please|now|for me|jarvis)"


def _clean_query(q: str) -> str:
    q = re.sub(STOP_WORDS, "", q, flags=re.I)
    return q.strip(" \"'?.!")


def _desktop_path(name: str) -> str:
    from jarvis.config.settings import DESKTOP
    return str(DESKTOP / name)


def _known_folder_path(spoken: str) -> str:
    """Resolve a spoken folder name (downloads/desktop/...) to its real path."""
    from jarvis.config import settings as _s
    key = spoken.lower().strip()
    key = {"download": "DOWNLOADS", "document": "DOCUMENTS", "desktop": "DESKTOP",
           "picture": "PICTURES", "video": "VIDEOS", "music": "MUSIC"}.get(key.rstrip("s"), key.upper())
    return str(getattr(_s, key, _s.DESKTOP))


def match_intent(text: str) -> Optional[Dict[str, Any]]:
    """Map a user command to a tool call. Returns {tool, arguments} or None."""
    if not text or not text.strip():
        return None
    t = text.strip()

    for rx, tool, args in SYSTEM_PATTERNS:
        if rx.search(t):
            return {"tool": tool, "arguments": dict(args)}

    for rx, tool, args in NEED_APP_PATTERNS:
        if rx.search(t):
            m2 = re.search(r"\b(chrome|edge|firefox|spotify|notepad(?:\+\+)?|vs ?code|vscode|calculator|terminal|cmd|powershell)\b", t, re.I)
            if m2:
                return {"tool": tool, "arguments": {"application": m2.group(1)}}

    for rx, tool, args in APP_PATTERNS:
        m = rx.search(t)
        if m:
            resolved = dict(args)
            if resolved.get("application") == "APP_EXTRACT":
                m2 = re.search(r"\b(chrome|spotify|notepad(?:\+\+)?|vs ?code|vscode|edge|firefox)\b", t, re.I)
                if not m2:
                    continue
                resolved["application"] = m2.group(1)
            return {"tool": tool, "arguments": resolved}

    m = GENERIC_OPEN_APP.search(t)
    if m:
        return {"tool": "open_application", "arguments": {"application": m.group(1).strip()}}

    for rx, tool, args in BROWSER_PATTERNS:
        m = rx.search(t)
        if m:
            resolved = dict(args)
            for key, value in resolved.items():
                if value == "GROUP1":
                    resolved[key] = m.group(1).strip(" \"'?!")
            if resolved.get("url") and "." not in resolved["url"]:
                continue
            return {"tool": tool, "arguments": resolved}

    for rx, tool, args in DELETE_PATTERNS:
        m = rx.search(t)
        if m:
            name = (m.group(1) or "").strip().strip("\"'")
            if not name or re.match(r"^(?:on|in|under|inside|from|to|all)\b", name, re.I):
                continue  # unresolvable target: needs the LLM when online
            return {"tool": tool, "arguments": {"path": _desktop_path(name)}}

    for rx, tool, args in FILE_PATTERNS:
        m = rx.search(t)
        if m:
            resolved = dict(args)
            if resolved.get("query") == "QUERY_EXTRACT":
                m2 = re.search(r"(?:called|named)\s+([\w .-]+)", t, re.I)
                resolved["query"] = _clean_query(m2.group(1)) if m2 else ""
            elif resolved.get("query") == "GROUP1":
                resolved["query"] = _clean_query(m.group(1))
            if resolved.get("path") == "KNOWN_FOLDER":
                m2 = re.search(r"\b(downloads?|documents?|desktop|pictures?|videos?|music)\b", t, re.I)
                if not m2:
                    continue
                resolved["path"] = _known_folder_path(m2.group(1))
            if resolved.get("path") == "FOLDER_ON_DESKTOP":
                m2 = re.search(r"(?:called|named)\s+[\"']?([A-Za-z0-9_ .-]+?)[\"']?\s*(?:\bon\b|\bin\b|\bunder\b|$)", t, re.I)
                if not m2:
                    continue
                resolved["path"] = _desktop_path(m2.group(1).strip())
            if resolved.get("path") == "FILE_EXTRACT":
                m2 = re.search(r"(?:called|named)\s+[\"']?([A-Za-z0-9_ .-]+?)[\"']?\s*(?:\bon\b|\bin\b|\bunder\b|\bwith\b|$)", t, re.I)
                if not m2:
                    continue
                resolved["path"] = _desktop_path(m2.group(1).strip())
            if tool == "search_files" and not resolved.get("query"):
                continue
            return {"tool": tool, "arguments": resolved}

    for rx, tool, args in MEMORY_PATTERNS:
        if rx.search(t):
            return {"tool": tool, "arguments": dict(args)}
    return None


def respond_offline(text: str) -> str:
    """Small canned-response table for pure chat when the LLM is offline."""
    t = text.lower().strip()
    if re.search(r"\b(hello|hi|hey|good (morning|evening|afternoon))\b", t):
        return "Hello. Local AI engine is offline, but computer control still works. What do you need?"
    if re.search(r"\b(who are you|what are you)\b", t):
        return "I am JARVIS, your local assistant. My language model is currently offline, so I am running in reduced mode."
    if re.search(r"\b(thank you|thanks)\b", t):
        return "You're welcome."
    if re.search(r"\b(status|are you there|you there)\b", t):
        return "Systems are running in local reduced mode. The AI engine is offline."
    return "My language model is offline, so I can only run direct computer commands. Try: open Chrome, RAM usage, or take a screenshot."
