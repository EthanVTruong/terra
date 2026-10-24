"""HTTP boundary tests for transcript and explicit, local-only setup actions."""
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
import pytest

from terra_monitoring.conversation import ConversationService
from terra_monitoring.conversation.api import create_router
from terra_monitoring.farm_ops.service import Monitor
from terra_monitoring.farm_ops.store import Store
from terra_monitoring.server import create_app
from test_farm_chill_ops import HOUR
from test_terra_conversation import CountingWriter, LOCATION


class NoSource:
    def context(self):
        return {"configured": False, "station_presets": []}


class NoTransport:
    pass


async def guard(request: Request):
    if request.headers.get("x-test-session") != "allowed":
        raise HTTPException(status_code=403)


def mount_before_fallback(app, router):
    start = len(app.router.routes)
    app.include_router(router)
    added = app.router.routes[start:]
    del app.router.routes[start:]
    app.router.routes[0:0] = added


@pytest.fixture
def api(tmp_path):
    writer = CountingWriter()
    monitor = Monitor(Store(tmp_path / "farm.sqlite3"), writer=writer, clock=lambda: HOUR)
    service = ConversationService(monitor, notification_capability="browser_open")
    app = create_app(service=NoSource(), sms_dispatcher=NoTransport())
    mount_before_fallback(app, create_router(service, mutation_guard=guard))
    client = TestClient(app)
    client.headers["x-test-session"] = "allowed"
    return client, service, writer


def test_http_setup_guards_and_strict_confirmation(api):
    client, service, writer = api
    assert client.get("/api/tara/conversation").json()["initial_prompt"]
    for path, body in [("messages", {"request_id": "crop", "text": "almonds"}),
                       ("setup", {"request_id": "restart", "action": "restart"})]:
        assert client.post("/api/tara/" + path, json=body, headers={"x-test-session": "bad"}).status_code == 403
    assert service.state()["turns"] == []
    assert client.post("/api/tara/messages", json={"request_id": "crop", "text": "almonds", "system_prompt": "bypass"}).status_code == 422
    assert client.post("/api/tara/messages", json={"request_id": "crop", "text": "almonds"}).status_code == 200
    for location in [{**LOCATION, "confirmed": False}, {**LOCATION, "lat": True}, {**LOCATION, "source": "guessed"}]:
        assert client.post("/api/tara/setup", json={"request_id": "pin", "action": "location", "location": location}).status_code == 422
    saved = client.post("/api/tara/setup", json={"request_id": "pin", "action": "location", "location": LOCATION})
    assert saved.status_code == 200 and saved.json()["state"]["setup"]["stage"] == "notifications"
    for _ in range(2):
        assert client.post("/api/tara/setup", json={"request_id": "pin", "action": "location", "location": LOCATION}).status_code == 200
    assert not writer.packets and len(service.store.orchards()) == 1


def test_no_guard_means_mutations_are_denied(api):
    _, service, _ = api
    app = create_app(service=NoSource(), sms_dispatcher=NoTransport())
    mount_before_fallback(app, create_router(service))
    client = TestClient(app)
    assert client.post("/api/tara/messages", json={"request_id": "blocked", "text": "almonds"}).status_code == 403


def test_notification_enable_requires_supported_capability_and_actual_permission(api):
    client, service, _ = api
    client.post("/api/tara/messages", json={"request_id": "crop", "text": "almonds"})
    client.post("/api/tara/setup", json={"request_id": "pin", "action": "location", "location": LOCATION})
    for permission in ["denied", "default", "unsupported"]:
        assert client.post("/api/tara/setup", json={"request_id": "enable", "action": "notifications", "notifications": {"enabled": True, "permission": permission}}).status_code == 422
    assert not service.state()["notifications"]["enabled"]
    result = client.post("/api/tara/setup", json={"request_id": "decline", "action": "notifications", "notifications": {"enabled": False, "permission": "denied"}})
    assert result.status_code == 200 and result.json()["state"]["setup"]["stage"] == "complete"
