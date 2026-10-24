"""API, independent worker and real saved-assessment composition checks."""
from __future__ import annotations

from datetime import timedelta
import hashlib
import json
from pathlib import Path
import threading

from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
import pytest

from terra_monitoring.farm_ops.api import create_app
from terra_monitoring.farm_ops.__main__ import run_loop
from terra_monitoring.farm_ops.demo import run_demo
from terra_monitoring.farm_ops.service import Monitor
from terra_monitoring.farm_ops.store import Store
from terra_monitoring.farm_ops.writer import Writer
from test_farm_chill_ops import HOUR, Transport, packet


async def guard(request: Request):
    if request.headers.get("x-test-session") != "authorized-test":
        raise HTTPException(status_code=403)


@pytest.fixture
def api(tmp_path):
    store = Store(tmp_path / "shared.sqlite3")
    monitor = Monitor(store, writer=Writer(key="", model=""), clock=lambda: HOUR)
    client = TestClient(create_app(monitor, mutation_guard=guard))
    client.headers["x-test-session"] = "authorized-test"
    return monitor, client


def test_create_status_question_and_evidence_across_api_restart(api):
    monitor, client = api
    response = client.post("/api/orchards", json={"orchard_id": "west", "name": "West Orchard"})
    assert response.status_code == 200 and response.json()["demo_location"]
    p = response.json()
    monitor.apply(p["id"], {"latest": packet(p)}, now=HOUR)
    monitor.flush(p["id"], now=HOUR)
    feed = client.get("/api/orchards/west/messages").json()
    assert feed["schema_version"] == "terra-farm-feed-v1" and len(feed["messages"]) == 1
    message = feed["messages"][0]
    assert message["kind"] == "proactive" and message["status"] == "published"
    assert message["delivery"]["status"] == "not_requested"
    detail = client.get(f"/api/orchards/west/messages/{message['id']}/details").json()
    assert detail["message"]["llm_input"] == message["llm_input"] and detail["assessments"]
    answer = client.post("/api/orchards/west/questions", json={"question": "How much chill?", "request_id": "q1"})
    assert answer.status_code == 200 and answer.json()["kind"] == "answer"
    restarted = TestClient(create_app(Monitor(Store(monitor.store.path), clock=lambda: HOUR), mutation_guard=guard))
    assert len(restarted.get("/api/orchards").json()["orchards"]) == 1
    assert len(restarted.get("/api/orchards/west/messages").json()["messages"]) == 2


@pytest.mark.parametrize("path,method,body", [
    ("/api/orchards", "post", {}), ("/api/monitor/check", "post", {}),
    ("/api/orchards/west/questions", "post", {"question": "status"}),
    ("/api/orchards/west/sms-opt-in", "put", {"enabled": True})])
def test_mutations_require_the_shared_guard_hook(api, path, method, body):
    monitor, client = api
    assert getattr(client, method)(path, json=body, headers={"x-test-session": "bad"}).status_code == 403
    unassembled = TestClient(create_app(monitor))
    assert getattr(unassembled, method)(path, json=body).status_code == 403
    assert not monitor.store.orchards()


@pytest.mark.parametrize("extra", [{"lat": True}, {"lat": "38.6"}, {"mode": "injected"},
    {"observations": []}, {"as_of": HOUR.isoformat()}, {"target": 1}, {"name": "\n"},
    {"private_key": "never-echo"}, {"elevation_ft": float("inf")}])
def test_invalid_or_source_controlling_profile_input_is_rejected_without_echo(api, extra):
    monitor, client = api
    body = json.dumps({"orchard_id": "west", **extra})
    response = client.post("/api/orchards", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 422
    assert "never-echo" not in response.text and not monitor.store.orchards()


def test_gets_are_read_only_and_do_not_dispatch_or_pull(api):
    monitor, client = api
    class NoActions:
        def collect(self, *args): raise AssertionError("GET cannot pull")
        def dispatch_event(self, *args): raise AssertionError("GET cannot text")
    monitor.source = monitor.delivery = NoActions()
    p = monitor.create_orchard(orchard_id="west")
    for path in ["/api/orchards", "/api/orchards/west", "/api/orchards/west/messages", "/api/monitor/status", "/api/orchards/west/sms-opt-in"]:
        response = client.get(path)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert not monitor.store.events(p["id"])


def test_explicit_setup_kicks_one_bounded_first_check(api):
    monitor, client = api
    class Source:
        calls = 0
        def collect(self, p):
            self.calls += 1
            return {"latest": packet(p)}
    monitor.source = Source()
    assert client.post("/api/orchards", json={"orchard_id": "west"}).status_code == 200
    assert monitor.source.calls == 1 and len(monitor.store.messages("west")) == 1
    assert client.get("/api/monitor/status").json()["worker"]["last_cycle"]


def test_setup_opt_in_precedes_first_worker_observation(api):
    monitor, client = api
    class Source:
        def collect(self, p):
            assert monitor.store.get("sms-opt-in:" + p["id"])["enabled"] is True
            return {"latest": packet(p)}
    monitor.source, monitor.delivery = Source(), Transport()
    response = client.post("/api/orchards", json={"orchard_id": "west", "sms_opt_in": True})
    assert response.status_code == 200 and len(monitor.delivery.calls) == 1


def test_sms_opt_in_and_disable_persist_without_phone_or_secrets(api):
    monitor, client = api
    client.post("/api/orchards", json={"orchard_id": "west"})
    assert client.get("/api/orchards/west/sms-opt-in").json()["enabled"] is False
    assert client.put("/api/orchards/west/sms-opt-in", json={"enabled": True}).json()["enabled"] is True
    assert client.put("/api/orchards/west/sms-opt-in", json={"enabled": False}).json()["enabled"] is False
    assert not Store(monitor.store.path).get("sms-opt-in:west")["enabled"]
    assert "phone" not in client.get("/api/orchards/west/sms-opt-in").text


def test_background_worker_runs_with_no_browser_and_updates_only_meaningful_changes(api):
    monitor, _ = api
    p = monitor.create_orchard(orchard_id="west")
    stop = threading.Event()
    ticks = []
    class Source:
        def collect(self, profile):
            ticks.append(1)
            if len(ticks) >= 2:
                stop.set()
            return {"latest": packet(profile)}
    monitor.source = Source()
    run_loop(monitor, interval=1, stop=stop, max_checks=2)
    assert len(ticks) == 2 and len(monitor.store.messages(p["id"])) == 1
    assert monitor.worker_status()["runtime"]["running"] is False
    assert monitor.worker_status()["heartbeat_status"] == "stale_or_stopped"


def test_real_saved_qc_and_nathan_estimates_flow_to_persistent_messages(tmp_path):
    store = Store(tmp_path / "demo.sqlite3")
    result = run_demo(store)
    assert result["status"] == "completed" and result["mode"] == "injected"
    assert all(m["text"].startswith("Demo replay:") for m in result["messages"])
    kinds = [event["type"] for event in store.events(result["orchard_id"])]
    assert kinds.count("monitoring_limited") == 1 and kinds.count("monitoring_recovered") == 1
    assert len(result["messages"]) == 3
    again = run_demo(Store(store.path))
    assert again["status"] == "already_saved" and len(again["messages"]) == 3


def test_bounded_snapshot_catchup_survives_retry_and_verifies_immutable_source(tmp_path):
    import pandas as pd
    from terra_monitoring.assessment import assess_location
    from terra_monitoring.farm_ops.source import LiveSource
    from terra_monitoring.farm_ops.service import orchard_profile
    from terra_qc.reference import load_reference
    root = Path(__file__).resolve().parents[1]
    saved = json.loads((root / "docs/terra/examples/location.request.json").read_text())
    times = sorted({r["timestamp"] for r in saved["observations"]})[-4:]
    observations = [r for r in saved["observations"] if r["timestamp"] in times]
    snapshot = {"data_mode": "current", "stations": saved["stations"], "observations": observations}
    raw = json.dumps(snapshot, sort_keys=True).encode()
    sha = hashlib.sha256(raw).hexdigest()
    path = tmp_path / "snapshots" / (sha + ".json")
    path.parent.mkdir(); path.write_bytes(raw)
    class Service:
        data_dir = tmp_path
        reference = load_reference()
        def assess(self, query):
            params = {**saved["query"], "location_id": query["location_id"]}
            return assess_location(**params, observations=pd.DataFrame(observations), stations=pd.DataFrame(saved["stations"]),
                                   snapshot_id="sha256:" + sha, data_mode="current", reference=self.reference)
    source = LiveSource(Service())
    profile = orchard_profile(orchard_id="west")
    first = source.collect(profile)
    second = source.collect(profile)
    assert len(first["history"]) == len(second["history"]) == 4
    assert first["history"] == second["history"]
    # A corrupt immutable file must not be hidden by the in-memory recent cache.
    path.write_bytes(b'{"corrupt":true}')
    with pytest.raises(ValueError, match="integrity"):
        source.collect(profile)
