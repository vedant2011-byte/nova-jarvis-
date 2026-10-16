"""Multi-step task planner.

The LLM (or offline planner) breaks a request into steps; the planner runs
each step through the agent loop with progress events for the task UI.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from jarvis.brain import llm
from jarvis.brain.agent import JarvisAgent
from jarvis.config.settings import get_settings
from jarvis.memory import manager as memory
from jarvis.memory.schemas import TaskRecord, TaskStep

log = logging.getLogger("jarvis.backend.planner")

_MAX_STEPS = 8
_MAX_STEP_CHARS = 300


class TaskPlan:
    def __init__(self, description: str, steps: List[TaskStep]):
        self.description = description
        self.steps = steps
        self.status = "running"
        self.task_id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "description": self.description,
            "status": self.status,
            "steps": [s.to_dict() for s in self.steps],
        }


class TaskPlanner:
    def __init__(self, agent: JarvisAgent):
        self.agent = agent

    @staticmethod
    def _validate_steps(value: Any) -> Optional[List[str]]:
        """Accept only a small, bounded list of concrete, non-empty steps."""
        if not isinstance(value, list) or not value or len(value) > _MAX_STEPS:
            return None
        cleaned: List[str] = []
        for item in value:
            if not isinstance(item, str):
                return None
            step = item.strip()
            if not step or len(step) > _MAX_STEP_CHARS:
                return None
            cleaned.append(step)
        return cleaned

    # ------------------------------------------------------------------
    def _llm_plan(self, text: str) -> Optional[List[str]]:
        """Ask the LLM for a bounded plan; reject malformed or oversized output."""
        settings = get_settings()
        if settings.get("ai.runtime", "ollama") == "off" or not llm.is_online():
            return None
        prompt = (
            "Break this request into a short numbered plan of concrete computer actions. "
            f"Return a JSON array with 1 to {_MAX_STEPS} strings, one action per step. "
            f"Each step must be at most {_MAX_STEP_CHARS} characters. Return JSON only.\n\n"
            f"Request: {text}\n\n"
            "Example: [\"Locate the project folder\", \"Open the project in VS Code\"]"
        )
        try:
            raw = llm.chat([{"role": "user", "content": prompt}], timeout=30)
            start = raw.find("[")
            end = raw.rfind("]")
            if start == -1 or end < start:
                return None
            return self._validate_steps(json.loads(raw[start:end + 1]))
        except Exception:  # noqa: BLE001
            log.exception("llm plan failed")
            return None

    def _offline_plan(self, text: str) -> Optional[List[str]]:
        """Conservative offline split on explicit sequencing words only.

        Preserve original casing and avoid splitting every ordinary 'and',
        which can be part of a single instruction or a name.
        """
        parts = re.split(r"\s+(?:and then|then|after that)\s+", text.strip(), flags=re.I)
        if len(parts) < 2:
            return None
        return self._validate_steps([part.strip(" .") for part in parts])

    # ------------------------------------------------------------------
    def needs_planning(self, text: str) -> bool:
        lowered = text.lower()
        connectors = (" and then ", " then ", " after that", " and also ")
        if any(c in lowered for c in connectors):
            return True
        # "X, Y and Z" style
        if lowered.count(",") >= 1 and " and " in lowered:
            return True
        return False

    def execute_plan(self, text: str, source: str = "text") -> Dict[str, Any]:
        """Plan and run a multi-step task, stopping safely at the first failure."""
        steps_raw = self._llm_plan(text) or self._offline_plan(text)
        if not steps_raw:
            # fall back to single-pass agent
            return self.agent.process_text(text, source)

        steps = [TaskStep(title=s) for s in steps_raw]
        plan = TaskPlan(text, steps)
        record = TaskRecord(description=text, steps=steps)
        plan.task_id = memory.record_task(record)

        self.agent.emit("task_started", plan.to_dict())
        self.agent.emit("state", {"state": "executing", "action": plan.description})

        failed = False
        for idx, step in enumerate(steps):
            if self.agent.cancel_flag.cancelled:
                step.status = "skipped"
                plan.status = "stopped"
                for remaining in steps[idx + 1:]:
                    remaining.status = "skipped"
                break

            step.status = "active"
            self.agent.emit("task_progress", plan.to_dict())
            try:
                out = self.agent.process_text(step.title, source)
                reply = str(out.get("reply", "")).strip()
                # A missing reply is not a verified success.
                if not reply:
                    step.status = "failed"
                    step.detail = "No result returned; success could not be verified."
                else:
                    step.status = "done"
                    step.detail = reply
            except Exception as exc:  # noqa: BLE001
                step.status = "failed"
                step.detail = f"{type(exc).__name__}: {exc}"

            self.agent.emit("task_progress", plan.to_dict())
            if step.status == "failed":
                failed = True
                # Later actions may depend on this step. Do not execute them
                # blindly; leave an explicit record for the user to resume.
                for remaining in steps[idx + 1:]:
                    remaining.status = "skipped"
                    remaining.detail = "Skipped because an earlier step failed."
                break

        if plan.status != "stopped":
            plan.status = "failed" if failed else "done"

        memory.finalize_task(plan.task_id, plan.status, [s.to_dict() for s in steps])
        self.agent.emit("task_done", plan.to_dict())

        done = sum(1 for s in steps if s.status == "done")
        reply = f"Completed {done} of {len(steps)} steps."
        if plan.status == "stopped":
            reply = "Task stopped."
        elif failed:
            first_fail = next(s for s in steps if s.status == "failed")
            reply = f"Task stopped at a failed step: {first_fail.detail}"
        return {"reply": reply, "plan": plan.to_dict(), "llm_online": llm.is_online()}

    def stop(self) -> None:
        self.agent.cancel_current()
