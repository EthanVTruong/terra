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
    assert client.get("/api/tara/conversation").json()["initial_prompt"] == "Hey, I'm Terra. What are you growing?"
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


def test_reconciliation_requires_guard_confirmation_and_returns_saved_notice(api):
    from test_terra_conversation_recovery import spawn_owner
    from test_terra_conversation import setup_farm
    client, service, writer = api
    orchard = setup_farm(service)
    child = spawn_owner(service, orchard)
    child.communicate(timeout=15)
    assert child.returncode == 23
    body = {"request_id": "http-ack", "target_request_id": "crashed-question", "confirm_uncertain": True, "orchard_id": orchard}
    assert client.post("/api/tara/reconcile", json=body, headers={"x-test-session": "bad"}).status_code == 403
    assert client.post("/api/tara/reconcile", json={**body, "confirm_uncertain": False}).status_code == 422
    assert client.get("/api/tara/conversation", params={"orchard_id": orchard}).json()["pending_requests"][0]["reconcilable"]
    for _ in range(2):
        response = client.post("/api/tara/reconcile", json=body)
        assert response.status_code == 200 and response.json()["request_id"] == "http-ack"
        assert response.json()["event"]["data"]["target_request_id"] == "crashed-question"
        assert response.json()["state"]["pending_requests"] == []
    assert not writer.packets


def test_completed_request_reconcile_race_has_distinct_safe_error(api):
    from test_terra_conversation import setup_farm
    client, service, writer = api
    orchard = setup_farm(service)
    service.message(request_id="real-complete", text="Thanks!", orchard_id=orchard)
    response = client.post("/api/tara/reconcile", json={"request_id": "unused-ack", "target_request_id": "real-complete", "confirm_uncertain": True, "orchard_id": orchard})
    assert response.status_code == 409 and response.json()["error"]["code"] == "request_already_completed"
    assert "real-complete" not in response.text and not writer.packets


def test_http_here_question_returns_saved_walnuts_without_model_usage(api):
    client, service, writer = api
    assert client.post("/api/tara/messages", json={"request_id": "walnut-crops", "text": "I grow walnuts"}).status_code == 200
    saved = client.post("/api/tara/setup", json={"request_id": "walnut-pin", "action": "location", "location": {**LOCATION, "name": "Walnut farm"}})
    assert saved.status_code == 200
    orchard = saved.json()["orchard_id"]
    question = {"request_id": "saved-walnut-question", "text": "What are we growing here?", "orchard_id": orchard}
    for _ in range(2):
        response = client.post("/api/tara/messages", json=question)
        assert response.status_code == 200
        answer = response.json()["assistant_turn"]
        assert answer["data"]["intent"] == "profile_crops"
        assert answer["data"]["local_facts"]["crops"] == ["walnut"]
        assert answer["text"] == "Your saved crops are walnut."
    assert service.store.orchard(orchard)["crops"] == ["walnut"]
    assert not writer.packets
