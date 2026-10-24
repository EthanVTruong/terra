"""HTTP contract tests without provider calls or the model/store pipeline."""
from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

from fastapi.testclient import TestClient
import pandas as pd
import pytest

from terra_monitoring.assessment import assess_location
from terra_monitoring.server import create_app

ROOT = Path(__file__).resolve().parents[1]
QUERY = {"lat": 38.6, "lon": -121.8, "elevation_ft": 60.0}


class FakeService:
    def __init__(self, result=None, error=None):
        self.calls = []
        self.context_calls = 0
        self.result = result if result is not None else {"schema_version": "terra-location-assessment-v1", "ok": True}
        self.error = error
        self.ran_on_event_loop = None

    def context(self):
        self.context_calls += 1
        return {"schema_version": "terra-live-context-v1", "configured": True, "source": "CIMIS",
                "station_presets": [], "max_age_hours": 6}

    def assess(self, query):
        self.calls.append(query)
        try:
            asyncio.get_running_loop()
            self.ran_on_event_loop = True
        except RuntimeError:
            self.ran_on_event_loop = False
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def service():
    return FakeService()


@pytest.fixture
def client(service, tmp_path):
    return TestClient(create_app(service=service, web_root=tmp_path / "no-build"))


def test_context_and_health_do_not_assess_or_start_a_fetch(client, service):
    response = client.get("/api/context")
    assert response.status_code == 200
    assert response.json()["schema_version"] == "terra-live-context-v1"
    assert response.headers["cache-control"] == "no-store"
    assert service.context_calls == 1 and service.calls == []
    assert client.get("/api/health").json() == {"status": "ok"}
    assert service.context_calls == 1 and service.calls == []


def test_assessment_uses_only_validated_query_and_threadpool(client, service):
    response = client.post("/api/assess", json=QUERY)
    assert response.status_code == 200 and response.json() == service.result
    assert response.headers["cache-control"] == "no-store"
    assert service.calls == [{**QUERY, "refresh": False}]
    assert service.ran_on_event_loop is False


def test_refresh_and_optional_target_are_explicit(client, service):
    query = {**QUERY, "target_station_id": 6, "location_id": "grower-site", "refresh": True}
    assert client.post("/api/assess", json=query).status_code == 200
    assert service.calls == [query]


@pytest.mark.parametrize("changes", [
    {"lat": 91}, {"lon": -181}, {"lat": "38.6"}, {"lat": True}, {"elevation_ft": "60"},
    {"elevation_ft": False}, {"refresh": "true"}, {"refresh": 1}, {"target_station_id": True},
    {"target_station_id": 6.0}, {"target_station_id": 0}, {"location_id": " "},
    {"location_id": 1}, {"as_of": "2026-10-24T08:00:00Z"}, {"snapshot_id": "client-value"},
    {"observations": []}, {"data_mode": "injected"},
])
def test_invalid_or_source_controlling_fields_never_reach_service(client, service, changes):
    response = client.post("/api/assess", json={**QUERY, **changes})
    assert response.status_code == 422
    assert response.json() == {"error": {"code": "invalid_query",
        "message": "Provide valid coordinates, elevation and supported query fields.", "retryable": False}}
    assert service.calls == []


@pytest.mark.parametrize("body", [
    '{"lat":NaN,"lon":-121.8,"elevation_ft":60}',
    '{"lat":38.6,"lon":Infinity,"elevation_ft":60}',
    '{"lat":38.6,"lon":-121.8,"elevation_ft":1e400}',
    '{"lat":38.6,"lon":-121.8,"elevation_ft":60,"private_key":"do-not-echo-me"}',
    '{"private_key":"do-not-echo-me",',
    'null', '[]',
])
def test_nonstandard_or_malformed_json_is_safe_validation_error(client, service, body):
    response = client.post("/api/assess", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_query"
    assert "do-not-echo-me" not in response.text
    assert "NaN" not in response.text and "Infinity" not in response.text
    assert service.calls == []


def test_unexpected_source_error_never_echoes_exception_or_credentials(tmp_path):
    service = FakeService(error=RuntimeError("https://source.invalid/?appKey=do-not-echo-me"))
    client = TestClient(create_app(service=service, web_root=tmp_path / "no-build"))
    response = client.post("/api/assess", json=QUERY)
    assert response.status_code == 500
    assert response.json()["error"] == {"code": "internal_error",
        "message": "Unable to complete the location query.", "retryable": False}
    assert "do-not-echo-me" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_nonfinite_service_output_is_explicit_error_instead_of_invalid_json(tmp_path):
    service = FakeService(result={"temperature": float("nan")})
    client = TestClient(create_app(service=service, web_root=tmp_path / "no-build"))
    response = client.post("/api/assess", json=QUERY)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "invalid_service_response"
    assert "NaN" not in response.text


def test_unknown_api_is_404_even_when_static_index_exists(tmp_path):
    (tmp_path / "index.html").write_text("<html>Terra preview</html>", encoding="utf-8")
    asset = tmp_path / "api" / "unknown"
    asset.parent.mkdir()
    asset.write_text("this must not be served as an API response", encoding="utf-8")
    client = TestClient(create_app(service=FakeService(), web_root=tmp_path))
    assert client.get("/").status_code == 200
    assert "Terra preview" in client.get("/").text
    for method in ("get", "post"):
        response = getattr(client, method)("/api/unknown")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
    assert client.get("/api").status_code == 404
    assert client.get("/api/assess").status_code == 405


def test_missing_web_build_keeps_api_available(client):
    assert client.get("/").status_code == 503
    assert client.get("/").json()["error"]["code"] == "web_build_missing"
    assert client.post("/api/assess", json=QUERY).status_code == 200


class FixtureAdapterService(FakeService):
    """Controlled test source: real adapter, retained injected request snapshot."""

    def __init__(self, request):
        super().__init__()
        self.request = request

    def assess(self, query):
        request = self.request
        self.calls.append(query)
        args = dict(request["query"])
        args.update({key: value for key, value in query.items() if key != "refresh"})
        return assess_location(**args, observations=pd.DataFrame(request["observations"]),
                               stations=pd.DataFrame(request["stations"]),
                               snapshot_id=request["snapshot_id"], data_mode=request["data_mode"])


@pytest.mark.parametrize("name", ["location", "measured", "missing"])
def test_http_runs_actual_adapter_and_preserves_measured_and_unavailable_semantics(name, tmp_path):
    request = json.loads((ROOT / "docs/terra/examples" / f"{name}.request.json").read_text(encoding="utf-8"))
    source = FixtureAdapterService(request)
    query = {key: request["query"][key] for key in
             ("lat", "lon", "elevation_ft", "target_station_id", "location_id") if key in request["query"]}
    client = TestClient(create_app(service=source, web_root=tmp_path / "no-build"))
    response = client.post("/api/assess", json=query)
    assert response.status_code == 200
    packet = response.json()
    assert packet["schema_version"] == "terra-location-assessment-v1"
    assert packet["provenance"]["qc_reference_id"] == "terra-qc-v4-e1e59ac0ea395d80"
    assert packet["data_mode"] == request["data_mode"]
    if name == "location":
        assert packet["target_comparison"]["status"] == "unassessable"
        assert packet["target_comparison"]["observed_f"] is None
        assert packet["location_estimate"]["status"] == "ok"
    elif name == "measured":
        assert packet["target_comparison"]["classification"] == "suspected_anomaly"
        assert packet["target_comparison"]["observed_f"] == 61.0
        assert packet["target_comparison"]["departure_f"] == pytest.approx(23.7)
    else:
        assert packet["location_estimate"]["status"] == "insufficient_data"
        assert packet["location_estimate"]["temperature"] is None
    json.dumps(packet, allow_nan=False)


class PublicServiceError(Exception):
    """The source layer's agreed public exception contract, without a provider."""

    def __init__(self, code, message, *, status_code=503, retryable=True):
        super().__init__(message)
        self.code, self.message = code, message
        self.status_code, self.retryable = status_code, retryable


@pytest.mark.parametrize("code,status,retryable", [("source_not_configured", 503, False),
                                                   ("provider_unavailable", 503, True)])
def test_public_service_error_contract_preserves_safe_status_and_retry(code, status, retryable, monkeypatch, tmp_path):
    # Test the HTTP boundary against the agreed service interface; the live
    # module's provider parsing/sanitization belongs to its separate tests.
    module = types.ModuleType("terra_monitoring.live")
    module.LiveQueryError = PublicServiceError
    monkeypatch.setitem(sys.modules, "terra_monitoring.live", module)
    service = FakeService(error=PublicServiceError(code, "Safe public source message.",
                                                 status_code=status, retryable=retryable))
    client = TestClient(create_app(service=service, web_root=tmp_path / "no-build"))
    response = client.post("/api/assess", json=QUERY)
    assert response.status_code == status
    assert response.json() == {"error": {"code": code, "message": "Safe public source message.",
                                         "retryable": retryable}}
    assert response.headers["cache-control"] == "no-store"


def test_default_service_construction_does_not_assess_or_fetch(monkeypatch, tmp_path):
    module = types.ModuleType("terra_monitoring.live")
    source = FakeService()
    created = []

    def constructor():
        created.append(True)
        return source

    module.LiveQueryService = constructor
    monkeypatch.setitem(sys.modules, "terra_monitoring.live", module)
    app = create_app(web_root=tmp_path / "no-build")
    assert created == [True] and source.calls == [] and source.context_calls == 0
    client = TestClient(app)
    assert client.get("/api/health").status_code == 200
    assert source.calls == [] and source.context_calls == 0


def test_integer_numeric_coordinates_are_valid_without_accepting_booleans(client, service):
    response = client.post("/api/assess", json={"lat": 0, "lon": 0, "elevation_ft": 0})
    assert response.status_code == 200
    assert service.calls == [{"lat": 0.0, "lon": 0.0, "elevation_ft": 0.0, "refresh": False}]
