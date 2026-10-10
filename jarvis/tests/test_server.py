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
