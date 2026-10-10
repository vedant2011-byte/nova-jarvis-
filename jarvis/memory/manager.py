"""High-level memory API used by the agent, tools and UI."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from jarvis.config.settings import get_settings
from jarvis.memory.database import Database
from jarvis.memory.schemas import ConversationEntry, TaskRecord, new_id

_DB: Optional[Database] = None


def get_db() -> Database:
    global _DB
    if _DB is None:
        get_settings()  # ensure data dirs exist
        _DB = Database()
    return _DB


def close_db() -> None:
    global _DB
    if _DB is not None:
        _DB.close()
        _DB = None


# ---------------------------------------------------------------------------
# Structured memory
# ---------------------------------------------------------------------------

def save_memory(category: str, key: str, value: str) -> Dict[str, Any]:
    item_id = get_db().memory_upsert(category, key, value)
    return {"id": item_id, "category": category, "key": key, "value": value}


def retrieve_memory(category: Optional[str] = None, key: Optional[str] = None,
                    query: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    db = get_db()
    if key and category:
        item = db.memory_get(category, key)
        return [item] if item else []
    return db.memory_search(query=query, category=category, limit=limit)


def update_memory(memory_id: int, value: str) -> bool:
    db = get_db()
    # fetch category/key so we can upsert
    found = None
    for item in db.memory_search(limit=10000):
        if item["id"] == memory_id:
            found = item
            break
    if not found:
        return False
    db.memory_upsert(found["category"], found["key"], value)
    return True


def delete_memory(memory_id: int) -> bool:
    return get_db().memory_delete(memory_id)


def search_memory(query: str, limit: int = 20, category: Optional[str] = None) -> List[Dict[str, Any]]:
    return get_db().memory_search(query=query, limit=limit, category=category)


def clear_memory(category: Optional[str] = None) -> int:
    return get_db().memory_clear(category)


# ---------------------------------------------------------------------------
# Conversation history
# ---------------------------------------------------------------------------

def remember_conversation(role: str, content: str, source: str = "text") -> None:
    if not get_settings().get("memory.enabled", True):
        return
    limit = int(get_settings().get("memory.max_conversation_history", 60))
    get_db().add_conversation(ConversationEntry(role=role, content=content, source=source))
    _trim_conversations(limit)


def get_history(limit: int = 100) -> List[Dict[str, Any]]:
    return [e.to_dict() for e in get_db().get_conversations(limit=limit)]


def context_entries(limit: int = 12) -> List[Dict[str, Any]]:
    return [e.to_dict() for e in get_db().recent_context(limit=limit)]


def clear_history() -> int:
    return get_db().clear_conversations()


def _trim_conversations(keep: int) -> None:
    db = get_db()
    with db._lock:  # noqa: SLF001 - intentional internal access within package
        db._conn.execute(
            "DELETE FROM conversations WHERE id NOT IN "
            "(SELECT id FROM conversations ORDER BY ts DESC LIMIT ?)", (keep * 4,)
        )
        db._conn.commit()


# ---------------------------------------------------------------------------
# Task history
# ---------------------------------------------------------------------------

def record_task(task: TaskRecord) -> int:
    return get_db().add_task(task)


def finalize_task(task_id: int, status: str, steps: List[Dict[str, Any]]) -> None:
    get_db().update_task(task_id, status, steps)


def recent_tasks(limit: int = 20) -> List[Dict[str, Any]]:
    return get_db().recent_tasks(limit)


# ---------------------------------------------------------------------------
# Natural-language memory capture ("remember that ...")
# ---------------------------------------------------------------------------

_PROJECT_HINTS = re.compile(
    r"(?:my\s+)?project\s+(?:called\s+)?[\"']?([\w\-. ]+)[\"']?"
    r"|(?:project\s+)?(?:named|called)\s+[\"']?([\w\-. ]+)[\"']?\s+(?:is\s+)?(?:project)?", re.I)
_LOCATION_HINTS = re.compile(
    r"\bon\s+my\s+(desktop|documents|downloads|pictures|music|videos)\b"
    r"|\bin\s+(?:the\s+)?(?:folder\s+)?([A-Za-z]:\\\\?[\w\\\\ \-.]*)"
    r"|\bat\s+([A-Za-z]:\\\\?[\w\\\\ \-.]*)", re.I)


def try_extract_memory(text: str) -> Optional[Dict[str, str]]:
    """Best-effort extraction of rememberable facts from a user sentence.

    Returns {"category", "key", "value"} or None. The LLM path is preferred;
    this keeps memory capture working offline.
    """
    lowered = text.lower().strip()
    if not any(w in lowered for w in ("remember", "note that", "keep in mind")):
        return None

    project = None
    # "my blackgrid project is on my desktop" / "the atlas project lives in C:\dev"
    m = re.search(r"(?:my|the)\s+([a-z0-9_\-.]+(?:\s+[a-z0-9_\-.]+)*?)\s+project\b", lowered)
    if m:
        project = m.group(1).strip()

    location = None
    m2 = re.search(r"on\s+my\s+(desktop|documents|downloads|pictures|music|videos)", lowered)
    if m2:
        location = m2.group(1)
    else:
        m3 = re.search(r"(?:in|at|under)\s+([A-Za-z]:[\\/][\w\\ /.:-]*)", lowered)
        if m3:
            location = m3.group(1).strip().rstrip(".!?")

    if project and location:
        return {"category": "project", "key": project, "value": location}
    if project:
        return {"category": "project", "key": project, "value": "unknown"}
    # generic fact: "remember that X"
    m4 = re.search(r"remember(?:\s+that)?\s+(.+)", lowered)
    if m4 and len(m4.group(1)) > 3:
        fact = text.strip()
        return {"category": "fact", "key": new_id(), "value": fact}
    return None


def find_project_location(name: str) -> Optional[str]:
    """Look up a stored project location by fuzzy name."""
    items = search_memory(query=name, category="project", limit=5)
    lowered = name.lower()
    for item in items:
        if lowered in item["key"].lower() or item["key"].lower() in lowered:
            loc = item["value"]
            if loc and loc != "unknown":
                return loc
    return None


def memory_summary_for_prompt(max_items: int = 25) -> str:
    """Compact memory digest injected into the LLM system prompt."""
    if not get_settings().get("memory.enabled", True):
        return ""
    items: List[Dict[str, Any]] = []
    for cat in ("project", "app", "preference", "instruction", "path"):
        items.extend(search_memory(query="", category=cat, limit=8))
    items = items[:max_items]
    if not items:
        return ""
    lines = [f"- {i['category']}/{i['key']}: {i['value'][:160]}" for i in items]
    return "\n".join(lines)


def dumps(obj: Any) -> str:
    return json.dumps(obj)
