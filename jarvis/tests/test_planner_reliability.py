from types import SimpleNamespace

import pytest

from jarvis.backend import planner as planner_module
from jarvis.backend.planner import TaskPlanner


class FakeAgent:
    def __init__(self, replies):
        self.replies = list(replies)
        self.cancel_flag = SimpleNamespace(cancelled=False)
        self.events = []

    def emit(self, event, data):
        self.events.append((event, data))

    def process_text(self, text, source):
        result = self.replies.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def cancel_current(self):
        self.cancel_flag.cancelled = True


@pytest.fixture
def fake_memory(monkeypatch):
    calls = {"recorded": None, "finalized": None}
    monkeypatch.setattr(planner_module.memory, "record_task",
                        lambda record: calls.update(recorded=record) or 42)
    monkeypatch.setattr(planner_module.memory, "finalize_task",
                        lambda task_id, status, steps: calls.update(
                            finalized=(task_id, status, steps)))
    return calls


def test_validate_steps_rejects_malformed_or_oversized_plans():
    assert TaskPlanner._validate_steps([" open browser ", "Search"]) == [
        "open browser", "Search"
    ]
    assert TaskPlanner._validate_steps([]) is None
    assert TaskPlanner._validate_steps(["ok", 3]) is None
    assert TaskPlanner._validate_steps(["x" * 301]) is None
    assert TaskPlanner._validate_steps(["x"] * 9) is None


def test_offline_plan_only_splits_explicit_sequence_and_preserves_case():
    planner = TaskPlanner(FakeAgent([]))
    assert planner._offline_plan("Open Chrome and then Search for Nova") == [
        "Open Chrome", "Search for Nova"
    ]
    assert planner._offline_plan("Open Chrome and search for Nova") is None


def test_plan_records_success_and_emits_progress(monkeypatch, fake_memory):
    agent = FakeAgent([{"reply": "Chrome opened"}, {"reply": "Search completed"}])
    planner = TaskPlanner(agent)
    monkeypatch.setattr(planner, "_llm_plan", lambda text: ["Open Chrome", "Search Nova"])

    result = planner.execute_plan("Open Chrome and then search Nova")

    assert result["plan"]["status"] == "done"
    assert [s["status"] for s in result["plan"]["steps"]] == ["done", "done"]
    assert fake_memory["finalized"][0:2] == (42, "done")
    assert any(event == "task_started" for event, _ in agent.events)
    assert any(event == "task_done" for event, _ in agent.events)


def test_plan_stops_after_empty_unverified_result(monkeypatch, fake_memory):
    agent = FakeAgent([{"reply": "First step complete"}, {"reply": ""}, {"reply": "must not run"}])
    planner = TaskPlanner(agent)
    monkeypatch.setattr(planner, "_llm_plan", lambda text: ["First", "Second", "Third"])

    result = planner.execute_plan("First then second then third")

    assert result["plan"]["status"] == "failed"
    assert [s["status"] for s in result["plan"]["steps"]] == ["done", "failed", "skipped"]
    assert len(agent.replies) == 1
    assert fake_memory["finalized"][1] == "failed"


def test_plan_stops_after_exception(monkeypatch, fake_memory):
    agent = FakeAgent([RuntimeError("tool failed"), {"reply": "must not run"}])
    planner = TaskPlanner(agent)
    monkeypatch.setattr(planner, "_llm_plan", lambda text: ["First", "Second"])

    result = planner.execute_plan("First then second")

    assert result["plan"]["status"] == "failed"
    assert [s["status"] for s in result["plan"]["steps"]] == ["failed", "skipped"]
    assert len(agent.replies) == 1
