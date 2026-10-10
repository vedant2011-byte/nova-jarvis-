"""SQLite persistence for memory, conversations and task history."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from jarvis.config.settings import DB_FILE
from jarvis.memory.schemas import ConversationEntry, TaskRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(category, key)
);
CREATE INDEX IF NOT EXISTS idx_memory_cat ON memory(category);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'text'
);
CREATE INDEX IF NOT EXISTS idx_conv_ts ON conversations(ts);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL,
    steps TEXT NOT NULL DEFAULT '[]'
);
"""


class Database:
    """Thread-safe wrapper around a single SQLite connection."""

    def __init__(self, db_path: Path = DB_FILE):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    # -- memory ------------------------------------------------------------
    def memory_upsert(self, category: str, key: str, value: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO memory(category, key, value, created_at, updated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(category, key)
                   DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (category, key, value, _now(), _now()),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT id FROM memory WHERE category=? AND key=?", (category, key)
            ).fetchone()
            return int(row[0]) if row else int(cur.lastrowid or 0)

    def memory_get(self, category: str, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, category, key, value, created_at, updated_at FROM memory WHERE category=? AND key=?",
                (category, key),
            ).fetchone()
        return _memory_row(row) if row else None

    def memory_search(
        self,
        query: str = "",
        category: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        sql = "SELECT id, category, key, value, created_at, updated_at FROM memory"
        params: List[Any] = []
        clauses: List[str] = []
        if category:
            clauses.append("category=?")
            params.append(category)
        if query:
            clauses.append("(key LIKE ? OR value LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like])
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_memory_row(r) for r in rows]

    def memory_delete(self, memory_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM memory WHERE id=?", (memory_id,))
            self._conn.commit()
        return cur.rowcount > 0

    def memory_clear(self, category: Optional[str] = None) -> int:
        with self._lock:
            if category:
                cur = self._conn.execute("DELETE FROM memory WHERE category=?", (category,))
            else:
                cur = self._conn.execute("DELETE FROM memory")
            self._conn.commit()
        return cur.rowcount

    # -- conversations -------------------------------------------------------
    def add_conversation(self, entry: ConversationEntry) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO conversations(ts, role, content, source) VALUES(?,?,?,?)",
                (entry.ts, entry.role, entry.content, entry.source),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def get_conversations(self, limit: int = 100) -> List[ConversationEntry]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts, role, content, source FROM conversations ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            ConversationEntry(id=r[0], ts=r[1], role=r[2], content=r[3], source=r[4])
            for r in reversed(rows)
        ]

    def recent_context(self, limit: int = 12) -> List[ConversationEntry]:
        return self.get_conversations(limit=limit)

    def clear_conversations(self) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM conversations")
            self._conn.commit()
        return cur.rowcount

    # -- tasks -----------------------------------------------------------------
    def add_task(self, task: TaskRecord) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO tasks(ts, description, status, steps) VALUES(?,?,?,?)",
                (task.ts, task.description, task.status, json.dumps([s.to_dict() for s in task.steps])),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def update_task(self, task_id: int, status: str, steps: Iterable[Dict[str, Any]]) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE tasks SET status=?, steps=? WHERE id=?",
                (status, json.dumps(list(steps)), task_id),
            )
            self._conn.commit()

    def recent_tasks(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts, description, status, steps FROM tasks ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": r[0], "ts": r[1], "description": r[2], "status": r[3],
                "steps": json.loads(r[4] or "[]"),
            }
            for r in rows
        ]


def _now() -> float:
    import time
    return time.time()


def _memory_row(row: tuple) -> Dict[str, Any]:
    return {
        "id": row[0], "category": row[1], "key": row[2], "value": row[3],
        "created_at": row[4], "updated_at": row[5],
    }
