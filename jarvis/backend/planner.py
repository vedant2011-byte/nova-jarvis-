"""Multi-step task planner.

The LLM (or offline planner) breaks a request into steps; the planner runs
each step through the agent loop with progress events for the task UI.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from jarvis.brain import llm
from jarvis.brain.agent import JarvisAgent
from jarvis.config.settings import get_settings
from jarvis.memory import manager as memory
from jarvis.memory.schemas import TaskRecord, TaskStep

log = logging.getLogger("jarvis.backend.planner")


class TaskPlan:
    def __init__(self, description: str, steps: List[TaskStep]):
        self.description = description
        self.steps = steps
        self.status = "running"
        self.task_id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "description": self.description,
            "status": self.status,
            "steps": [s.to_dict() for s in self.steps],
        }


class TaskPlanner:
    def __init__(self, agent: JarvisAgent):
        self.agent = agent

    # ------------------------------------------------------------------
    def _llm_plan(self, text: str) -> Optional[List[str]]:
        """Ask the LLM to produce a numbered plan for multi-step requests."""
        settings = get_settings()
        if settings.get("ai.runtime", "ollama") == "off" or not llm.is_online():
            return None
        prompt = (
            "Break this request into a short numbered plan of concrete computer actions. "
            "Each step must be a single actionable instruction. Reply with ONLY a JSON array "
            "of step strings, no prose.\n\n"
            f"Request: {text}\n\n"
            "Example: [\"Locate the project folder\", \"Start the dev server\", \"Open Chrome at localhost:3000\"]"
        )
        try:
            raw = llm.chat([{"role": "user", "content": prompt}], timeout=30)
            start = raw.find("[")
            end = raw.rfind("]")
            if start == -1 or end == -1:
                return None
            import json
            steps = json.loads(raw[start:end + 1])
            if isinstance(steps, list) and steps and all(isinstance(s, str) for s in steps):
                return steps[:8]
        except Exception:  # noqa: BLE001
            log.exception("llm plan failed")
        return None

    def _offline_plan(self, text: str) -> Optional[List[str]]:
        """Heuristic multi-step detection (offline)."""
        lowered = text.lower()
        steps: List[str] = []
        if " and " in lowered or " then " in lowered:
            parts = [p.strip(" .") for p in
                     lowered.replace(" then ", " and ").split(" and ") if p.strip()]
            if len(parts) >= 2:
                steps = parts
        return steps if steps else None

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
        """Plan and run a multi-step task with progress events."""
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

        results = []
        for idx, step in enumerate(steps):
            if self.agent.cancel_flag.cancelled:
                step.status = "skipped"
                plan.status = "stopped"
                break
            step.status = "active"
            self.agent.emit("task_progress", plan.to_dict())
            try:
                out = self.agent.process_text(step.title, source)
                ok = out.get("reply", "") != ""
                failed_words = ("failed", "not found", "couldn't", "could not",
                                "unavailable", "error")
                reply_l = out.get("reply", "").lower()
                step_failed = any(w in reply_l for w in failed_words)
                if step_failed:
                    step.status = "failed"
                    step.detail = out.get("reply", "")
                else:
                    step.status = "done"
                    step.detail = out.get("reply", "")
            except Exception as exc:  # noqa: BLE001
                step.status = "failed"
                step.detail = str(exc)
            results.append(step.to_dict())
            self.agent.emit("task_progress", plan.to_dict())

        if plan.status != "stopped":
            failed = [s for s in steps if s.status == "failed"]
            plan.status = "failed" if failed else "done"

        memory.finalize_task(plan.task_id, plan.status, [s.to_dict() for s in steps])
        self.agent.emit("task_done", plan.to_dict())

        done = sum(1 for s in steps if s.status == "done")
        reply = f"Completed {done} of {len(steps)} steps."
        if plan.status == "stopped":
            reply = "Task stopped."
        elif failed:
            first_fail = next(s for s in steps if s.status == "failed")
            reply = f"Task finished with issues: {first_fail.detail}"
        return {"reply": reply, "plan": plan.to_dict(), "llm_online": True}

    def stop(self) -> None:
        self.agent.cancel_current()
