"""Device permission uses actual USGS terrain and leaves safe retry boundaries."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import threading

import httpx
import pytest

from terra_monitoring.conversation import ConversationService
from terra_monitoring.conversation.location import EPQS_URL, GroundElevationResolver, LocationLookupError
from terra_monitoring.farm_ops.store import fingerprint
from test_farm_chill_ops import HOUR
from test_terra_conversation import LOCATION
from test_terra_conversation_api import api


DEVICE = {"lat": 38.6, "lon": -121.84, "accuracy_m": 14.5}
TERRAIN = {"location": {"x": -121.84, "y": 38.6, "spatialReference": {"wkid": 4326, "latestWkid": 4326}},
           "value": 76.88043870361227, "rasterId": 59391, "resolution": 1, "attributes": {"AcquisitionDate": "11/2/2019"}}


def prepare(api, handler=None):
    client, service, writer = api
    calls = []
    def respond(request):
        calls.append(request)
        return handler(request) if handler else httpx.Response(200, json=deepcopy(TERRAIN))
    service.location_resolver = GroundElevationResolver(client=httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=True))
    crop = client.post("/api/tara/messages", json={"request_id": "crop", "text": "Peaches"})
    assert crop.status_code == 200
    assert crop.json()["assistant_turn"]["text"] == "Where are you growing? Share your location."
    return client, service, writer, calls


def body(request_id="device"):
    return {"request_id": request_id, "action": "device_location", "device_location": dict(DEVICE)}


def test_native_device_action_resolves_actual_ground_and_persists_provenance(api):
    client, service, writer, calls = prepare(api)
    result = client.post("/api/tara/setup", json=body())
    assert result.status_code == 200, result.text
    data = result.json()
    p = service.store.orchard(data["orchard_id"])
    assert p["name"] == "Shared location" and p["elevation_ft"] == TERRAIN["value"]
    assert p["lat"] == DEVICE["lat"] and p["lon"] == DEVICE["lon"]
    assert p["location_accuracy_m"] == 14.5 and p["elevation_source"]["provider"] == "USGS EPQS"
    assert p["elevation_source"]["raster_id"] == 59391 and p["elevation_source"]["acquisition_date"] == "11/2/2019"
    assert p["elevation_source"]["retrieved_at"] == HOUR.isoformat()
    assert "device" in p["location_source"] and "USGS" in p["location_source"]
    assert p["variety"] is None and p["target"]["hours"] is None and not p["demo_location"]
    assert "target_station_id" not in p and "target_station_id" not in data["event"]["data"]
    assert data["event"]["text"] == "Location shared" and data["event"]["data"]["saved"]
    assert data["state"]["setup"]["stage"] == "notifications" and data["state"]["monitoring"]["status"] == "pending"
    assert not service.store.get("sms-opt-in:" + p["id"])["enabled"] and not data["state"]["notifications"]["enabled"]
    assert data["event"]["created_at"] == HOUR.isoformat()
    assert len(calls) == 1 and str(calls[0].url).startswith(EPQS_URL + "?")
    assert dict(calls[0].url.params) == {"x": "-121.84", "y": "38.6", "wkid": "4326", "units": "Feet", "includeDate": "true"}
    assert set(calls[0].extensions["timeout"].values()) == {10.0}
    assert "authorization" not in calls[0].headers and not writer.packets


def test_completed_device_retry_and_restart_make_zero_additional_lookups(api):
    client, service, _, calls = prepare(api)
    first = client.post("/api/tara/setup", json=body()).json()
    second = client.post("/api/tara/setup", json=body()).json()
    assert first["event"] == second["event"] and len(calls) == 1
    class NoLookup:
        def resolve(self, *args, **kwargs):
            raise AssertionError("A completed request must not resolve elevation again")
    restarted = ConversationService(service.monitor, location_resolver=NoLookup())
    third = restarted.action(**body())
    assert third["event"] == first["event"] and len(service.store.orchards()) == 1


def test_lookup_timeout_is_retryable_without_profile_or_reservation(api):
    attempts = 0
    def respond(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("private provider details must not leak", request=request)
        return httpx.Response(200, json=TERRAIN)
    client, service, writer, calls = prepare(api, respond)
    failed = client.post("/api/tara/setup", json=body())
    assert failed.status_code == 503
    assert failed.json() == {"error": {"code": "location_lookup_failed", "message": "Couldn’t get your location details. Try again.", "retryable": True}}
    assert not service.store.orchards() and not service.state()["pending_requests"]
    assert service.state()["setup"]["crops"] == ["peach"] and service.state()["setup"]["stage"] == "location"
    assert not [turn for turn in service.state()["turns"] if turn["role"] == "event"]
    assert client.post("/api/tara/setup", json=body()).status_code == 200
    assert client.post("/api/tara/setup", json=body()).status_code == 200
    assert len(calls) == 2 and not writer.packets


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(value=-1000000), lambda d: d.update(value=None), lambda d: d.update(value=True),
    lambda d: d.update(value="76.8"), lambda d: d.update(value=float("nan")), lambda d: d.update(value=20001),
    lambda d: d["location"].update(x=-120.0), lambda d: d["location"].update(y=39.0),
    lambda d: d["location"]["spatialReference"].update(wkid=3857), lambda d: d.pop("location"),
])
def test_unusable_terrain_never_saves_partial_location(api, mutation):
    payload = deepcopy(TERRAIN)
    mutation(payload)
    # json.dumps from the transport accepts NaN only as an intentionally malformed provider fixture.
    import json
    client, service, writer, _ = prepare(api, lambda _: httpx.Response(200, content=json.dumps(payload)))
    assert client.post("/api/tara/setup", json=body()).status_code == 503
    assert not service.store.orchards() and not service.state()["pending_requests"] and not writer.packets


@pytest.mark.parametrize("response", [httpx.Response(500, text="provider failure"),
    httpx.Response(302, headers={"Location": "http://127.0.0.1/private"}), httpx.Response(200, text="not JSON"),
    httpx.Response(200, content=b"x" * 65537)])
def test_provider_errors_redirects_and_unbounded_payloads_fail_closed(api, response):
    client, service, _, calls = prepare(api, lambda _: response)
    assert client.post("/api/tara/setup", json=body()).status_code == 503
    assert len(calls) == 1 and not service.store.orchards() and not service.state()["pending_requests"]


@pytest.mark.parametrize("bad", [{"lat": True}, {"lat": 91}, {"lon": -181}, {"accuracy_m": -1},
    {"altitude": 9999}, {"elevation_ft": 0}, {"name": "Invented farm"}, {"url": "http://127.0.0.1"}])
def test_strict_guarded_device_input_cannot_override_name_ground_or_endpoint(api, bad):
    client, service, writer, calls = prepare(api)
    payload = body()
    payload["device_location"].update(bad)
    assert client.post("/api/tara/setup", json=payload).status_code == 422
    assert not calls and not service.store.orchards() and not writer.packets


def test_actual_guard_runs_before_device_lookup(api):
    client, service, _, calls = prepare(api)
    assert client.post("/api/tara/setup", json=body(), headers={"x-test-session": "bad"}).status_code == 403
    assert not calls and not service.store.orchards()


def test_service_ignores_browser_altitude_and_name_and_accepts_real_sea_level(api):
    terrain = {**TERRAIN, "value": 0}
    _, service, writer, calls = prepare(api, lambda _: httpx.Response(200, json=terrain))
    supplied = {**DEVICE, "altitude": 10000, "elevation_ft": 10000, "name": "Guessed farm"}
    result = service.action(request_id="ignored-device-extras", action="device_location", device_location=supplied)
    profile = service.store.orchard(result["orchard_id"])
    assert profile["name"] == "Shared location" and profile["elevation_ft"] == 0
    assert profile["elevation_source"]["provider"] == "USGS EPQS" and len(calls) == 1 and not writer.packets


def test_legacy_manual_signature_and_retry_remain_byte_for_byte_compatible(api):
    client, service, _, calls = prepare(api)
    manual = {"request_id": "legacy-manual", "action": "location", "location": LOCATION}
    saved = client.post("/api/tara/setup", json=manual)
    assert saved.status_code == 200 and saved.json()["event"]["text"] == "Peach farm"
    with service.store.connect() as db:
        signature = db.execute("SELECT signature FROM tara_requests WHERE id='legacy-manual'").fetchone()[0]
    assert signature == fingerprint({"action": "location", "location": LOCATION, "notifications": None, "orchard_id": None})
    assert client.post("/api/tara/setup", json=manual).json()["event"] == saved.json()["event"]
    assert not calls


@pytest.mark.parametrize("next_crop", ["Walnuts", "Peaches"])
def test_late_lookup_cannot_overwrite_restarted_setup_even_if_crops_match(api, next_crop):
    client, service, writer, calls = prepare(api)
    base = service.location_resolver
    entered, release = threading.Event(), threading.Event()
    class DelayedResolver:
        def resolve(self, position, *, now):
            entered.set()
            assert release.wait(5)
            return base.resolve(position, now=now)
    service.location_resolver = DelayedResolver()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, "/api/tara/setup", json=body())
        assert entered.wait(5)
        assert client.post("/api/tara/setup", json={"request_id": "restart-during-lookup", "action": "restart"}).status_code == 200
        assert client.post("/api/tara/messages", json={"request_id": "new-crop", "text": next_crop}).status_code == 200
        release.set()
        stale = pending.result(5)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "setup_changed"
    assert stale.json()["error"]["retryable"] is False
    assert not service.store.orchards() and not service.state()["pending_requests"]
    assert service.state()["setup"]["crops"] == (["walnut"] if next_crop == "Walnuts" else ["peach"])
    assert service.state()["setup"]["stage"] == "location" and len(calls) == 1 and not writer.packets
