"""Typed records for the memory layer."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class MemoryCategory(str, Enum):
    PREFERENCE = "preference"
    PROJECT = "project"          # project locations
    PATH = "path"                # frequently used paths
    APP = "app"                  # custom application locations
    INSTRUCTION = "instruction"  # custom user instructions
    FACT = "fact"                # useful user-defined information
    TASK = "task"                # recent task outcomes


@dataclass
class MemoryItem:
    category: str
    key: str
    value: str
    id: Optional[int] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "key": self.key,
            "value": self.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class ConversationEntry:
    role: str          # "user" | "jarvis" | "system"
    content: str
    ts: float = field(default_factory=time.time)
    source: str = "text"   # voice | text | system
    id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "ts": self.ts,
            "source": self.source,
        }


@dataclass
class TaskStep:
    title: str
    status: str = "pending"   # pending | active | done | failed | skipped
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"title": self.title, "status": self.status, "detail": self.detail}


@dataclass
class TaskRecord:
    description: str
    steps: List[TaskStep] = field(default_factory=list)
    status: str = "running"   # running | done | failed | stopped
    id: Optional[int] = None
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "description": self.description,
            "status": self.status,
            "steps": [s.to_dict() for s in self.steps],
        }


def new_id() -> str:
    return uuid.uuid4().hex[:12]
