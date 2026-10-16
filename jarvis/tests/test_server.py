"""HTTP API smoke tests."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from jarvis.backend.server import app
    with TestClient(app) as c:  # runs startup events
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_status(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    data = r.json()
    assert "llm" in data and "voice" in data and "tts" in data


def test_tools_endpoint(client):
    r = client.get("/api/tools")
    assert r.status_code == 200
    tools = r.json()["tools"]
    names = {t["name"] for t in tools}
    assert "open_application" in names
    assert "run_terminal_command" in names


def test_settings_roundtrip(client):
    r = client.post("/api/settings", json={"patch": {"appearance": {"animation_intensity": 1.2}}})
    assert r.status_code == 200
    r2 = client.get("/api/settings")
    assert r2.json()["settings"]["appearance"]["animation_intensity"] == 1.2
    # restore
    client.post("/api/settings", json={"patch": {"appearance": {"animation_intensity": 1.0}}})


def test_memory_endpoints(client):
    r = client.post("/api/memory", json={"category": "fact", "key": "test-key-xyz", "value": "hello"})
    assert r.status_code == 200
    r2 = client.get("/api/memory?query=test-key-xyz")
    items = r2.json()["items"]
    assert any(i["key"] == "test-key-xyz" for i in items)
    item_id = next(i["id"] for i in items if i["key"] == "test-key-xyz")
    r3 = client.delete(f"/api/memory/{item_id}")
    assert r3.json()["deleted"] is True


def test_history_endpoint(client):
    r = client.get("/api/history?limit=10")
    assert r.status_code == 200
    assert "entries" in r.json()


def test_confirmation_flow(client):
    from jarvis.security.confirmation import get_confirmation_manager
    import asyncio

    cm = get_confirmation_manager()
    loop = asyncio.new_event_loop()
    cm.set_loop(loop)

    # register a pending request synchronously (bypassing the async waiter)
    from jarvis.security.confirmation import ConfirmationRequest
    req = ConfirmationRequest(
        id="test-confirm-1", tool="delete_file", title="t", description="d",
        detail={}, risk="high-risk",
    )
    req.future = loop.create_future()
    cm._pending["test-confirm-1"] = req
    r = client.post("/api/confirm", json={"id": "test-confirm-1", "approved": False})
    assert r.json()["resolved"] is True
    cm._pending.pop("test-confirm-1", None)
    loop.close()


def test_stop(client):
    r = client.post("/api/stop")
    assert r.status_code == 200
    assert r.json()["stopped"] is True


# ---------------------------------------------------------------------------
# Provider reporting + request validation (Gemini integration)
# ---------------------------------------------------------------------------
def test_status_reports_provider_fields(client, monkeypatch):
    """Status distinguishes provider health from generic availability."""
    import jarvis.backend.server as server_mod
    monkeypatch.setattr(server_mod, "_llm_probe",
                        lambda: (True, ["gemini-3.5-flash-lite"]))
    monkeypatch.setenv("GEMINI_API_KEY", "shape-only-key-for-this-test")
    r = client.get("/api/status")
    assert r.status_code == 200
    block = r.json()["llm"]
    assert block["online"] is True
    assert block["provider"] == "gemini"
    assert block["model"] == "gemini-3.5-flash-lite"
    assert block["error"] == ""
    # key must never appear in any API response
    assert "shape-only-key-for-this-test" not in r.text


def test_status_offline_reports_reason(client, monkeypatch):
    import jarvis.backend.server as server_mod
    monkeypatch.setattr(server_mod, "_llm_probe", lambda: (False, []))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    r = client.get("/api/status")
    assert r.status_code == 200
    block = r.json()["llm"]
    assert block["online"] is False
    assert isinstance(block["error"], str) and block["error"]


def test_command_rejects_oversized_and_malformed(client):
    assert client.post("/api/command", json={"text": "x" * 4001}).status_code == 422
    assert client.post("/api/command", json={"text": ""}).status_code == 422
    assert client.post("/api/command", json={}).status_code == 422
    assert client.post("/api/command", content=b"not json",
                       headers={"Content-Type": "application/json"}).status_code == 422


def test_command_accepts_normal_message(client):
    r = client.post("/api/command", json={"text": "hello", "source": "text"})
    assert r.status_code == 200
    assert r.json()["accepted"] is True
