"""SMS HTTP authorization and side-effect boundaries; never contact Twilio."""
from __future__ import annotations

import copy
import sys
import types
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
import httpx
import pytest

from terra_monitoring.server import create_app

LOCAL = "http://127.0.0.1:8513"
QUERY = {"lat": 38.6, "lon": -121.8, "elevation_ft": 60.0}
PACKET = {"schema_version": "terra-location-assessment-v1", "data_mode": "current", "station_findings": []}
PRIVATE = "secret-token-and-phone-15551234567"


class FakeQuery:
    def __init__(self):
        self.packet = copy.deepcopy(PACKET)
        self.calls = []
        self.error = None

    def context(self):
        return {"configured": True}

    def assess(self, query):
        self.calls.append(query)
        if self.error:
            raise self.error
        return self.packet


class FakeSMS:
    def __init__(self):
        self.calls = []
        self.error = None

    def _result(self, operation, *args):
        self.calls.append((operation, args))
        if self.error:
            raise self.error
        if operation == "status":
            return {"transport": "twilio", "configured": True, "enabled": True,
                    "demo_enabled": True, "suppressed": False,
                    "recipient": "***4567", "sender": "***1111"}
        if operation == "disable":
            return {"suppressed": True}
        return {"event_id": args[0], "status": "queued", "mode": "demo"}

    def status(self):
        return self._result("status")

    def demo(self, event_id):
        return self._result("demo", event_id)

    def receipt(self, event_id):
        return self._result("receipt", event_id)

    def disable(self):
        return self._result("disable")

@pytest.fixture
def setup(tmp_path):
    query, sms = FakeQuery(), FakeSMS()
    app = create_app(service=query, sms_dispatcher=sms, web_root=tmp_path)
    with TestClient(app, base_url=LOCAL, raise_server_exceptions=False) as client:
        yield client, query, sms


def authorize(client):
    response = client.get("/api/sms/status")
    assert response.status_code == 200
    return {"X-Terra-SMS-Control": response.json()["control_token"], "Origin": LOCAL}


def test_shared_mutation_guard_raises_before_farm_side_effects_even_with_sms_disabled(tmp_path):
    app = create_app(service=FakeQuery(), sms_dispatcher=FakeSMS(), web_root=tmp_path)
    effects = []

    @app.post("/guarded", dependencies=[Depends(app.state.local_mutation_guard)])
    def guarded():
        effects.append("changed")
        return {"ok": True}

    with TestClient(app, base_url=LOCAL) as client:
        assert client.post("/guarded").status_code == 403
        headers = authorize(client)
        assert client.post("/guarded", headers={**headers, "Origin": "https://evil.example"}).status_code == 403
        assert effects == []
        assert client.post("/guarded", headers=headers).json() == {"ok": True}
        assert effects == ["changed"]


def test_optional_farm_router_precedes_fallback_and_uses_shared_raising_guard(tmp_path, monkeypatch):
    monitor, effects, received = object(), [], []

    def create_router(given_monitor, *, mutation_guard):
        received.append(given_monitor)
        router = APIRouter()

        @router.get("/api/orchards")
        def orchards():
            return [{"id": "example-orchard"}]

        @router.post("/api/monitor/check", dependencies=[Depends(mutation_guard)])
        def check():
            effects.append("checked")
            return {"checked": True}

        return router

    package = types.ModuleType("terra_monitoring.farm_ops")
    package.__path__ = []
    api = types.ModuleType("terra_monitoring.farm_ops.api")
    api.create_router = create_router
    monkeypatch.setitem(sys.modules, package.__name__, package)
    monkeypatch.setitem(sys.modules, api.__name__, api)
    app = create_app(service=FakeQuery(), sms_dispatcher=FakeSMS(), web_root=tmp_path, farm_monitor=monitor)
    assert received == [monitor] and app.state.farm_monitor is monitor and effects == []
    with TestClient(app, base_url=LOCAL) as client:
        assert client.get("/api/orchards").json() == [{"id": "example-orchard"}]
        assert client.post("/api/monitor/check").status_code == 403 and effects == []
        assert client.post("/api/monitor/check", headers=authorize(client)).json() == {"checked": True}
        assert effects == ["checked"]
        assert client.get("/api/unknown").status_code == 404


def test_demo_http_composes_real_detector_durable_dispatch_and_receipt(tmp_path):
    from terra_monitoring.sms import SMSDispatcher
    calls = []
    sid = "SM" + "1" * 32

    def provider(request):
        calls.append(request)
        if request.method == "POST":
            form = parse_qs(request.content.decode())
            assert form["To"] == ["+15555550100"]
            assert "TERRA DEMO - simulated flag" in form["Body"][0]
            assert "120.0 F" in form["Body"][0] and "40.5 F" in form["Body"][0]
            assert form["ValidityPeriod"] == ["300"]
            return httpx.Response(201, json={"sid": sid, "status": "queued"})
        return httpx.Response(200, json={"sid": sid, "status": "delivered"})

    settings = {"TWILIO_ACCOUNT_SID": "AC" + "0" * 32,
                "TWILIO_AUTH_TOKEN": "2" * 32, "TWILIO_FROM_NUMBER": "+15555550101",
                "TERRA_SMS_TO_NUMBER": "+15555550100", "TERRA_SMS_ENABLED": "true",
                "TERRA_SMS_DEMO_ENABLED": "true", "TERRA_SMS_DB_PATH": str(tmp_path / "sms.sqlite3")}
    dispatcher = SMSDispatcher.from_env(settings, transport=httpx.MockTransport(provider))
    app = create_app(service=FakeQuery(), sms_dispatcher=dispatcher, web_root=tmp_path)
    with TestClient(app, base_url=LOCAL) as client:
        headers = authorize(client)
        sent = client.post("/api/sms/demo", json={"event_id": "http-demo"}, headers=headers)
        assert sent.status_code == 200
        body = sent.json()
        assert body["status"] == "provider_accepted" and body["provider_status"] == "queued"
        assert body["assessment"]["data_mode"] == "synthetic"
        assert body["assessment"]["station_findings"][0]["reason"] == "extreme_departure"
        assert "+15555550100" not in sent.text and settings["TWILIO_AUTH_TOKEN"] not in sent.text
        duplicate = client.post("/api/sms/demo", json={"event_id": "http-demo"}, headers=headers).json()
        assert duplicate["duplicate"] is True and len(calls) == 1
        receipt = client.get("/api/sms/receipt/http-demo", headers=headers).json()
        assert receipt["status"] == "delivered" and len(calls) == 2
        assert client.post("/api/sms/disable", headers=headers).json()["suppressed"] is True
    restarted = SMSDispatcher.from_env(settings, transport=httpx.MockTransport(provider))
    assert restarted.status()["suppressed"] is True
    assert restarted.receipt("http-demo")["status"] == "delivered"
    assert len(calls) == 2


def test_status_exposes_only_masked_dispatcher_configuration_and_ephemeral_token(setup):
    client, query, sms = setup
    response = client.get("/api/sms/status", headers={"Origin": LOCAL})
    body = response.json()
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert body["recipient"] == "***4567" and body["sender"] == "***1111"
    assert len(body["control_token"]) >= 32 and PRIVATE not in response.text
    assert sms.calls == [("status", ())] and query.calls == []


@pytest.mark.parametrize("path", ["/", "/api/health", "/api/sms/status", "/api/sms/unknown"])
def test_local_page_and_api_block_framing_and_content_type_sniffing(setup, path):
    client, _, _ = setup
    response = client.get(path)
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "base-uri 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("headers", [
    {"Host": "evil.example"}, {"Host": "127.0.0.1.evil.example"},
    {"Host": "localhost@evil.example"}, {"Host": "evil.example@localhost"},
    {"Host": "localhost/evil"}, {"Host": "localhost?evil"},
    {"Origin": "https://evil.example"}, {"Origin": "null"},
    {"Origin": "http://localhost:8513"}, {"Origin": "http://127.0.0.1:8514"},
    {"Sec-Fetch-Site": "cross-site"},
])
def test_status_rejects_rebinding_cross_origin_and_cross_site_requests(setup, headers):
    client, query, sms = setup
    response = client.get("/api/sms/status", headers=headers)
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert "control_token" not in response.text and sms.calls == [] and query.calls == []


@pytest.mark.parametrize("host", ["localhost:8513", "127.0.0.1:8513", "[::1]:8513"])
def test_status_allows_literal_loopback_hosts(setup, host):
    client, _, _ = setup
    assert client.get("/api/sms/status", headers={"Host": host, "Origin": f"http://{host}"}).status_code == 200


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/api/sms/demo", {"event_id": "demo-one"}),
    ("GET", "/api/sms/receipt/demo-one", None),
    ("POST", "/api/sms/disable", None),
])
def test_sms_operations_require_current_token_and_local_origin(setup, method, path, body):
    client, query, sms = setup
    headers = authorize(client)
    sms.calls.clear()
    for untrusted in [{}, {"X-Terra-SMS-Control": "wrong"},
                      {**headers, "Origin": "https://evil.example"},
                      {**headers, "Host": "evil.example"},
                      {**headers, "Sec-Fetch-Site": "cross-site"}]:
        response = client.request(method, path, json=body, headers=untrusted)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "sms_unauthorized"
    assert sms.calls == [] and query.calls == []


def test_authorized_demo_receipt_and_disable_call_only_expected_operation(setup):
    client, query, sms = setup
    headers = authorize(client)
    sms.calls.clear()
    demo = client.post("/api/sms/demo", headers=headers, json={"event_id": "demo-1_ab"})
    assert demo.status_code == 200 and demo.json()["event_id"] == "demo-1_ab"
    receipt = client.get("/api/sms/receipt/demo-1_ab", headers=headers)
    assert receipt.status_code == 200
    stopped = client.post("/api/sms/disable", headers=headers)
    assert stopped.status_code == 200 and stopped.json() == {"suppressed": True}
    assert sms.calls == [("demo", ("demo-1_ab",)), ("receipt", ("demo-1_ab",)), ("disable", ())]
    assert query.calls == []
    assert all(r.headers["cache-control"] == "no-store" for r in [demo, receipt, stopped])


def test_malformed_non_ascii_token_is_rejected_without_server_exception(setup):
    client, query, sms = setup
    response = client.post("/api/sms/demo", headers={b"X-Terra-SMS-Control": b"\xc3\xa9"},
                           json={"event_id": "demo-one"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "sms_unauthorized"
    assert sms.calls == [] and query.calls == []


def test_control_token_is_unique_per_server_instance_and_old_token_cannot_send(setup, tmp_path):
    client, _, _ = setup
    previous = authorize(client)
    other_sms, other_query = FakeSMS(), FakeQuery()
    other_app = create_app(service=other_query, sms_dispatcher=other_sms, web_root=tmp_path)
    with TestClient(other_app, base_url=LOCAL) as other:
        current = authorize(other)
        assert previous["X-Terra-SMS-Control"] != current["X-Terra-SMS-Control"]
        other_sms.calls.clear()
        response = other.post("/api/sms/demo", json={"event_id": "demo-one"}, headers=previous)
        assert response.status_code == 403 and other_sms.calls == []


@pytest.mark.parametrize("event_id", ["x" * 81, "has%20space", "unicode-%C3%A9", "has%2Eperiod"])
def test_invalid_receipt_identifier_never_reaches_provider(setup, event_id):
    client, _, sms = setup
    headers = authorize(client)
    sms.calls.clear()
    response = client.get(f"/api/sms/receipt/{event_id}", headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_sms_request" and sms.calls == []


@pytest.mark.parametrize("body", [
    {}, {"event_id": ""}, {"event_id": "../secret"}, {"event_id": "x" * 81},
    {"event_id": 123}, {"event_id": "demo", "to": "+15551234567"},
    {"event_id": "demo", "body": PRIVATE}, {"event_id": "demo", "assessment": PACKET},
])
def test_demo_rejects_invalid_ids_arbitrary_phone_body_and_packet(setup, body):
    client, query, sms = setup
    headers = authorize(client)
    sms.calls.clear()
    response = client.post("/api/sms/demo", headers=headers, json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_sms_request"
    assert PRIVATE not in response.text and "+15551234567" not in response.text
    assert sms.calls == [] and query.calls == []


@pytest.mark.parametrize("include_sms_authorization", [False, True])
def test_pure_assess_never_dispatches_even_with_sms_enabled(setup, include_sms_authorization):
    client, query, sms = setup
    headers = authorize(client) if include_sms_authorization else {}
    sms.calls.clear()
    response = client.post("/api/assess", json=QUERY, headers=headers)
    assert response.status_code == 200 and response.json() == PACKET
    assert query.calls == [{**QUERY, "refresh": False}] and sms.calls == []


@pytest.mark.parametrize("headers_present", [False, True])
def test_raw_live_dispatch_route_does_not_exist_or_bypass_farm_policy(setup, headers_present):
    client, query, sms = setup
    headers = authorize(client) if headers_present else {}
    sms.calls.clear()
    response = client.post("/api/sms/check", json=QUERY, headers=headers)
    assert response.status_code == 404 and response.json()["error"]["code"] == "not_found"
    assert query.calls == [] and sms.calls == []


def test_source_error_returns_safe_error_and_never_dispatches(setup):
    client, query, sms = setup
    headers = authorize(client)
    sms.calls.clear()
    query.error = RuntimeError(PRIVATE)
    response = client.post("/api/assess", json=QUERY, headers=headers)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert PRIVATE not in response.text and sms.calls == []


@pytest.mark.parametrize("packet", [None, [], {"private": float("nan")}, {"private": object()}])
def test_invalid_service_response_is_safe_json_without_dispatch(setup, packet):
    client, query, sms = setup
    headers = authorize(client)
    sms.calls.clear()
    query.packet = packet
    response = client.post("/api/assess", json=QUERY, headers=headers)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "invalid_service_response"
    assert sms.calls == []


def test_sms_transport_failure_cannot_break_pure_assessment(setup):
    client, query, sms = setup
    headers = authorize(client)
    sms.calls.clear()
    sms.error = RuntimeError(PRIVATE)
    response = client.post("/api/assess", json=QUERY, headers=headers)
    assert response.status_code == 200
    assert response.json() == PACKET and sms.calls == []
    assert PRIVATE not in response.text


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/sms/status", None),
    ("POST", "/api/sms/demo", {"event_id": "demo-one"}),
    ("GET", "/api/sms/receipt/demo-one", None),
    ("POST", "/api/sms/disable", None),
])
def test_provider_or_storage_errors_never_echo_sensitive_exception(setup, method, path, body):
    client, _, sms = setup
    headers = authorize(client)
    sms.error = RuntimeError(PRIVATE)
    response = client.request(method, path, json=body, headers=headers)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "sms_unavailable"
    assert PRIVATE not in response.text


@pytest.mark.parametrize("method,path", [("GET", "/api/sms/demo"), ("POST", "/api/sms/status"),
                                        ("GET", "/api/sms/disable"),
                                        ("POST", "/api/sms/receipt/demo-one")])
def test_wrong_sms_methods_are_json_405_and_unknown_route_is_404(setup, method, path):
    client, _, sms = setup
    response = client.request(method, path)
    assert response.status_code == 405 and response.json()["error"]["code"] == "method_not_allowed"
    missing = client.get("/api/sms/unknown")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
    assert sms.calls == []
