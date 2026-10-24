"""Cross-layer acceptance with controlled weather and a mocked SMS provider.

These are pipeline checks, not labelled natural faults or real phone delivery.
Raw CIMIS-shaped fixtures pass through the actual live normalizer, frozen QC,
location estimator, persistent orchard monitor and SMS attempt journal.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
from urllib.parse import parse_qs

from fastapi.testclient import TestClient
import httpx
import pytest

from terra_monitoring.farm_ops import season
from terra_monitoring.farm_ops.service import Monitor
from terra_monitoring.farm_ops.source import LiveSource
from terra_monitoring.farm_ops.store import Store
from terra_monitoring.farm_ops.writer import Writer
from terra_monitoring.live import LiveQueryService
from terra_monitoring.runtime import create_runtime_app
from terra_monitoring.sms import SMSConfig, SMSDispatcher
from terra_qc.reference import REFERENCE_ID
from test_terra_live import Clock, HOURS, IDS, SITE, SavedRawSource, baseline_records, raw_record

ORIGIN = "http://127.0.0.1:8512"
HEADERS = {"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"}
SID = "SM" + "1" * 32


@pytest.fixture(autouse=True)
def no_private_configuration(monkeypatch):
    for key in list(os.environ):
        if key.startswith(("TWILIO_", "TERRA_SMS_", "OPENAI_")) or key in {
                "API_KEY_SID", "API_KEY_SECRET", "CIMIS_APP_KEY", "CIMIS_API_KEY"}:
            monkeypatch.delenv(key, raising=False)


class Provider:
    """A complete mock HTTP boundary; it never opens a network connection."""

    def __init__(self, first="accepted"):
        self.first = first
        self.requests = []
        self.provider_status = "delivered"

    @property
    def posts(self):
        return [r for r in self.requests if r.method == "POST"]

    @property
    def gets(self):
        return [r for r in self.requests if r.method == "GET"]

    def __call__(self, request):
        self.requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"sid": SID, "status": self.provider_status})
        if len(self.posts) == 1 and self.first == "timeout":
            raise httpx.ReadTimeout("controlled mock timeout", request=request)
        if len(self.posts) == 1 and self.first == "retry":
            return httpx.Response(429, json={"code": 20429})
        if self.first == "failed":
            return httpx.Response(400, json={"code": 21211})
        return httpx.Response(201, json={"sid": SID, "status": "queued"})


def assemble(tmp_path, *, available=False, first="accepted", clock=None):
    clock = clock or Clock()
    records = baseline_records(hours=HOURS[-1:])
    if not available:
        # Two measured sites cannot support the three-peer orchard estimator.
        records = [r for r in records if r["Station"] in {"139", "226"}]
    source = SavedRawSource(tmp_path / "raw", records)
    # Explicitly exercise current-mode gates with controlled data. This is not a
    # claim that any fixture was retrieved from a real current provider.
    source.data_mode = "current"
    live = LiveQueryService(app_key="", source=source, clock=clock,
                            data_dir=tmp_path / "live", ttl_seconds=0)
    provider = Provider(first)
    config = SMSConfig.from_env({
        "TWILIO_ACCOUNT_SID": "AC" + "1" * 32,
        "TWILIO_AUTH_TOKEN": "2" * 32,
        "TWILIO_FROM_NUMBER": "+15555550100",
        "TERRA_SMS_TO_NUMBER": "+15555550101",
        "TERRA_SMS_ENABLED": "true", "TERRA_SMS_MAX_PER_DAY": "100",
        "TERRA_SMS_DB_PATH": str(tmp_path / "sms.sqlite3"),
    })
    sms = SMSDispatcher(config, transport=httpx.MockTransport(provider), clock=clock)
    monitor = Monitor(Store(tmp_path / "farm.sqlite3"), source=LiveSource(live),
                      delivery=sms, writer=Writer(key="", model=""), clock=clock)
    return SimpleNamespace(clock=clock, source=source, live=live, provider=provider,
                           sms=sms, monitor=monitor, state=tmp_path / "runtime")


def app_for(pipeline):
    return create_runtime_app(live_service=pipeline.live, sms_dispatcher=pipeline.sms,
                              monitor=pipeline.monitor, state_dir=pipeline.state,
                              start_worker=False, web_root=pipeline.state / "no-build")


def client_for(pipeline):
    return TestClient(app_for(pipeline), base_url=ORIGIN)


def controls(client):
    token = client.get("/api/sms/status", headers=HEADERS).json()["control_token"]
    return {**HEADERS, "x-terra-sms-control": token,
            "X-Terra-Source-Context": client.get("/api/simulation/connection").json()["source_context"]}


def setup(client, *, consent=True):
    response = client.post("/api/orchards", json={"orchard_id": "controlled",
        "name": "Controlled test orchard", **{k: SITE[k] for k in ("lat", "lon", "elevation_ft")},
        "sms_opt_in": consent}, headers=controls(client))
    assert response.status_code == 200, response.text
    return response.json()


def feed(client):
    response = client.get("/api/orchards/controlled/messages")
    assert response.status_code == 200
    return response.json()["messages"]


def restart_monitor(pipeline):
    # Reopen both persistent journals, leaving no browser or in-memory monitor
    # state responsible for deduplication and provider receipt recovery.
    pipeline.sms = SMSDispatcher(pipeline.sms.config,
        transport=httpx.MockTransport(pipeline.provider), clock=pipeline.clock)
    pipeline.monitor = Monitor(Store(pipeline.monitor.store.path), source=LiveSource(pipeline.live),
        delivery=pipeline.sms, writer=Writer(key="", model=""), clock=pipeline.clock)


def test_read_only_assess_and_suspect_station_do_not_create_orchard_alert(tmp_path):
    p = assemble(tmp_path, available=True)
    for row in p.source.records:
        # Isolate temperature QC: the expanded source contract also requests
        # these channels. Omitting them legitimately opens data-health notices.
        row.update(HlyRelHum={"Value": "70", "Unit": "(%)", "Qc": " "},
                   HlyWindSpd={"Value": "4", "Unit": "(mph)", "Qc": " "},
                   HlySolRad={"Value": "0", "Unit": "(Ly/d)", "Qc": " "})
        if row["Station"] == "6" and row["Hour"] == "0800":
            row["HlyAirTmp"]["Value"] = "90.0"
    with client_for(p) as client:
        response = client.post("/api/assess", json=SITE, headers=controls(client))
        assert response.status_code == 200, response.text
        assessment = response.json()
        assert any(f["station_id"] == 6 for f in assessment["station_findings"])
        assert assessment["location_estimate"]["status"] == "ok"
        assert assessment["location_estimate"]["n_used"] >= 3
        assert p.provider.requests == [] and p.monitor.store.orchards() == []
        setup(client, consent=False)
        original = feed(client)[0]
        p.clock.advance(seconds=1)
        client.put("/api/orchards/controlled/sms-opt-in", json={"enabled": True}, headers=controls(client))
        p.monitor.check()
        assert [e["type"] for e in p.monitor.store.events("controlled")] == ["welcome"]
        assert len(feed(client)) == 1 and p.provider.requests == []
        details = client.get(f"/api/orchards/controlled/messages/{original['id']}/details").json()
        assert any(packet["station_findings"] for packet in details["assessments"])


def test_source_to_flag_message_receipt_and_restart_with_browser_closed(tmp_path):
    p = assemble(tmp_path)
    with client_for(p) as client:
        setup(client)
        [message] = feed(client)
        assert message["delivery"]["status"] == "provider_accepted"
        assert "cannot" in message["text"].lower() or "limited" in message["text"].lower()
        assert message["delivery"]["provider_message_id"] == SID
        assert len(p.provider.posts) == 1
        assert parse_qs(p.provider.posts[0].content.decode())["Body"] == [message["text"]]
    restart_monitor(p)
    p.monitor.check()  # Runs without an HTTP client or UI; reconciles first.
    with client_for(p) as client:
        [reloaded] = feed(client)
        assert reloaded["id"] == message["id"]
        receipt = reloaded["delivery"]
        assert receipt["status"] == "delivered" and receipt["provider_status"] == "delivered"
        assert receipt["source_event_id"] == message["id"] and receipt["attempt_count"] == 1
        assert receipt["provider_message_id"] == SID and receipt["updated_at_utc"]
        detail = client.get(f"/api/orchards/controlled/messages/{message['id']}/details").json()
        assert detail["transport_receipt"]["status"] == "delivered"
        assert any(e["type"] == "monitoring_limited" for e in detail["events"])
    restart_monitor(p)
    p.monitor.check()
    assert len(p.provider.posts) == 1 and len(p.monitor.store.messages("controlled")) == 1


def test_definitive_429_backoff_and_delivery_are_persistent(tmp_path):
    p = assemble(tmp_path, first="retry")
    with client_for(p) as client:
        setup(client)
        [message] = feed(client)
        assert message["delivery"]["status"] == "retry_wait"
        assert message["delivery"]["next_attempt_at_utc"]
    restart_monitor(p)
    p.monitor.check()
    assert len(p.provider.posts) == 1
    p.clock.advance(seconds=31)
    p.monitor.check()
    assert p.monitor.delivery_status(message)["status"] == "provider_accepted"
    assert len(p.provider.posts) == 2
    assert p.provider.posts[0].content == p.provider.posts[1].content
    p.monitor.check()
    assert p.monitor.delivery_status(message)["status"] == "delivered"
    assert p.monitor.delivery_status(message)["attempt_count"] == 2
    assert len(p.provider.posts) == 2


@pytest.mark.parametrize("first,expected", [("timeout", "uncertain"), ("failed", "failed")])
def test_uncertain_acceptance_and_permanent_rejection_never_resend_after_restart(tmp_path, first, expected):
    p = assemble(tmp_path, first=first)
    with client_for(p) as client:
        setup(client)
        [message] = feed(client)
        assert message["delivery"]["status"] == expected
    restart_monitor(p)
    p.clock.advance(minutes=1)
    p.monitor.check()
    p.monitor.drain_notifications()
    assert p.monitor.delivery_status(message)["status"] == expected
    assert len(p.provider.posts) == 1
    assert p.monitor.notifications_pending() == []


def test_opt_out_before_retry_and_answers_are_never_proactive_sms(tmp_path):
    p = assemble(tmp_path, first="retry")
    with client_for(p) as client:
        setup(client)
        response = client.post("/api/orchards/controlled/questions",
            json={"question": "How much chill?", "request_id": "question-1"}, headers=controls(client))
        assert response.status_code == 200 and response.json()["kind"] == "answer"
        assert len(p.provider.posts) == 1
        response = client.put("/api/orchards/controlled/sms-opt-in", json={"enabled": False}, headers=controls(client))
        assert response.status_code == 200
    restart_monitor(p)
    p.clock.advance(minutes=1)
    p.monitor.check()
    assert not p.monitor.notifications_pending() and len(p.provider.posts) == 1
    with client_for(p) as client:
        answers = [m for m in feed(client) if m["kind"] == "answer"]
        assert answers[0]["delivery"] == {"status": "not_requested", "reason": "local_question_answer"}


def test_provider_receipts_cannot_regress_delivered_in_persisted_feed(tmp_path):
    p = assemble(tmp_path)
    with client_for(p) as client:
        setup(client)
        [message] = feed(client)
        delayed = dict(message["delivery"])
    p.monitor.check()
    assert p.monitor.delivery_status(message)["status"] == "delivered"
    p.monitor.record_transport_receipt(message["id"], delayed)
    restart_monitor(p)
    with client_for(p) as client:
        [saved] = feed(client)
        assert saved["delivery"]["status"] == "delivered"
    assert len(p.provider.posts) == 1


def test_explicit_replay_stays_in_local_inbox_even_with_enabled_transport(tmp_path):
    p = assemble(tmp_path, available=True)
    profile = p.monitor.create_orchard(orchard_id="replay", mode="injected")
    assessment = p.live.assess(SITE)
    assessment["data_mode"] = "injected"
    p.monitor.apply(profile["id"], {"latest": assessment}, now=p.clock())
    p.monitor.flush(profile["id"])
    [message] = p.monitor.store.messages(profile["id"])
    assert message["text"].startswith("Demo replay:")
    assert p.monitor.delivery_status(message) == {"status": "not_requested", "reason": "demo_mode"}
    assert p.monitor.drain_notifications() == [] and p.provider.requests == []


def test_completed_day_chill_can_notify_when_hourly_temperature_is_stale(tmp_path):
    # Establish an existing data-health episode first. A later independent
    # completed-day chill event must not inherit the six-hour temperature gate.
    p = assemble(tmp_path, available=True, clock=Clock("2026-11-03T16:10:00Z"))
    p.source.records = [raw_record(sid, "2026-11-03T08:00:00Z") for sid in IDS]
    with client_for(p) as client:
        profile = setup(client, consent=False)
    p.clock.advance(hours=6)
    p.monitor.check()  # Existing missing-current-temperature episode escalates once.
    contract = p.monitor.store.get("watch:controlled")["method_version"]
    # Matching controlled orchard-estimate history, not station totals or a
    # substitute for real historical access. Three complete days and five
    # independently counted prior seasons make the daily comparison assessable.
    with p.monitor.store.connect(write=True) as db:
        for year in range(2021, 2027):
            start = datetime(year, 11, 1, tzinfo=season.PST).astimezone(timezone.utc)
            for hour in range(1, 73):
                p.monitor.store.put_hour(db, "controlled", {
                    "valid_utc": (start + timedelta(hours=hour)).isoformat(),
                    "temperature_f": 60.0 if year == 2026 else 40.0,
                    "usable": True, "method_version": contract, "qc_reference": REFERENCE_ID,
                    "geometry_key": profile["geometry_key"], "status_reason": "",
                    "status": "ok", "evidence_id": "controlled-history"})
    p.clock.advance(hours=18)
    p.monitor.set_sms_opt_in("controlled", True)
    p.source.records = [raw_record(sid, "2026-11-04T08:00:00Z") for sid in IDS]
    p.monitor.check()
    [posted] = p.provider.posts
    newest = p.monitor.store.messages("controlled")[0]
    assert p.monitor.delivery_status(newest)["status"] == "provider_accepted"
    assert "below" in parse_qs(posted.content.decode())["Body"][0].lower()
    details = p.monitor.store.details("controlled", newest["id"])
    assert [event["type"] for event in details["events"]] == ["chill_pace"]
    assert details["events"][0]["facts"]["reference_count"] == 5
    assert p.clock() - datetime.fromisoformat(p.monitor.store.get("watch:controlled")["last_observation"]) > timedelta(hours=6)
    assert not p.monitor.status("controlled")["comparison_available"]
