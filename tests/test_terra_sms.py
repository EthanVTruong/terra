"""SMS tests use Twilio-shaped local transports; no external message is sent."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from threading import Event
from urllib.parse import parse_qs

import httpx
import pytest

from terra_monitoring.sms import SMSConfig, SMSDispatcher, build_demo_assessment
from terra_monitoring import sms

NOW = datetime(2026, 1, 11, 16, 5, tzinfo=timezone.utc)
MESSAGE_SID = "SM" + "a" * 32


@pytest.fixture
def environment(tmp_path):
    return {"TERRA_SMS_ENABLED": "true", "TERRA_SMS_DEMO_ENABLED": "true",
            "TWILIO_ACCOUNT_SID": "AC" + "1" * 32, "TWILIO_AUTH_TOKEN": "2" * 32,
            "TWILIO_FROM_NUMBER": "+15555550101", "TERRA_SMS_TO_NUMBER": "+15555550102",
            "TERRA_SMS_DB_PATH": str(tmp_path / "private" / "sms.sqlite3")}


@pytest.fixture(scope="module")
def demo_packet():
    return build_demo_assessment()


def live_packet(demo_packet):
    packet = copy.deepcopy(demo_packet)
    packet["data_mode"] = "current"
    packet["query"]["as_of_utc"] = NOW.isoformat()
    packet["provenance"]["live_source"] = {
        "provider": "CIMIS", "status": "refreshed", "checked_at_utc": NOW.isoformat(),
        "retrieved_at_utc": NOW.isoformat(), "data_through_utc": packet["query"]["valid_utc"],
        "failed_station_ids": [], "missing_station_ids": [], "warning": None}
    return packet


def dispatcher(environment, requests=None, handler=None, *, clock=lambda: NOW):
    requests = [] if requests is None else requests

    def respond(request):
        requests.append(request)
        return handler(request) if handler else httpx.Response(201, json={"sid": MESSAGE_SID, "status": "queued"})

    return SMSDispatcher.from_env(environment, transport=httpx.MockTransport(respond), clock=clock)


@pytest.fixture
def notification():
    return {"schema_version": "terra-notification-v1", "event_id": "combined-farm-message-1",
            "source_event_ids": ["station-6-opening", "chill-milestone-1"],
            "episode_ids": ["station-6-episode"], "orchard_id": "orchard-1",
            "data_mode": "current", "kind": "orchard_update", "transition": "combined",
            "severity": "moderate", "category": "temperature",
            "text": "Orchard 1: station 6 has a suspected temperature anomaly. Check an independent reading.",
            "created_at_utc": (NOW - timedelta(seconds=10)).isoformat(),
            "checked_at_utc": NOW.isoformat(),
            "observation_time_utc": (NOW - timedelta(minutes=5)).isoformat(),
            "expires_at_utc": (NOW + timedelta(minutes=10)).isoformat(),
            "eligibility": {"eligible": True, "reason": "active_and_relevant"}}


def test_unconfigured_and_disabled_are_safe_and_do_not_send(environment, tmp_path):
    empty = SMSDispatcher.from_env({})
    assert empty.status()["configured"] is False
    assert empty.status()["enabled"] is False
    assert empty.demo("one")["status"] == "not_configured"
    calls = []
    for changes in ({"TERRA_SMS_ENABLED": "false"}, {"TERRA_SMS_DEMO_ENABLED": "false"}):
        instance = dispatcher({**environment, **changes}, calls)
        assert instance.demo("one")["status"] == "disabled"
    assert calls == []
    assert not (tmp_path / "private").exists()


@pytest.mark.parametrize("changes", [
    {"TERRA_SMS_ENABLED": "probably"}, {"TWILIO_ACCOUNT_SID": "not-an-account"},
    {"TWILIO_AUTH_TOKEN": ""}, {"TWILIO_FROM_NUMBER": "55550101"},
    {"TERRA_SMS_TO_NUMBER": "+1555<script>"}, {"TERRA_SMS_MAX_PER_DAY": "0"},
    {"TERRA_SMS_MAX_PER_DAY": "101"}, {"TERRA_SMS_MAX_PER_DAY": "NaN"},
    {"TWILIO_API_KEY_SID": "SK" + "3" * 32},
])
def test_invalid_configuration_fails_closed_without_echoing_values(environment, changes):
    calls = []
    instance = dispatcher({**environment, **changes}, calls)
    assert instance.status()["configured"] is False
    assert instance.status()["enabled"] is False
    result = instance.demo("one")
    assert result["status"] == "not_configured" and calls == []
    exposed = json.dumps(instance.status()) + json.dumps(result) + repr(instance.config)
    assert environment["TWILIO_AUTH_TOKEN"] not in exposed
    assert environment["TERRA_SMS_TO_NUMBER"] not in exposed
    assert "<script>" not in exposed


def test_explicit_environment_file_process_precedence_and_no_shell_expansion(environment, tmp_path):
    path = tmp_path / "private.env"
    path.write_text("\n".join(["# private runtime", *[f'{k}="{v}"' for k, v in environment.items()],
                               "UNRELATED=$(not-a-command)"]), encoding="utf-8")
    config = SMSConfig.from_env({"TERRA_SMS_ENV_FILE": str(path), "TERRA_SMS_ENABLED": "false"})
    assert config.errors == () and config.enabled is False and config.demo_enabled is True
    assert config.recipient == environment["TERRA_SMS_TO_NUMBER"]
    missing = SMSConfig.from_env({"TERRA_SMS_ENV_FILE": str(tmp_path / "missing")})
    assert "env_file_unavailable_or_invalid" in missing.errors
    path.write_text("TWILIO_AUTH_TOKEN='unterminated", encoding="utf-8")
    assert "env_file_unavailable_or_invalid" in SMSConfig.from_env({"TERRA_SMS_ENV_FILE": str(path)}).errors


def test_api_key_auth_uses_account_for_url_and_key_for_basic_auth(environment):
    calls = []
    instance = dispatcher({**environment, "TWILIO_API_KEY_SID": "SK" + "3" * 32,
                           "TWILIO_API_KEY_SECRET": "private-api-secret"}, calls)
    assert instance.demo("auth-key")["status"] == "provider_accepted"
    assert calls[0].url.host == "api.twilio.com"
    assert str(calls[0].url).endswith("/Accounts/" + environment["TWILIO_ACCOUNT_SID"] + "/Messages.json")
    assert base64.b64decode(calls[0].headers["authorization"].split()[1]).decode() == "SK" + "3" * 32 + ":private-api-secret"


def test_demo_runs_real_detector_and_sends_labelled_fixed_recipient_evidence(environment, demo_packet):
    finding = next(f for f in demo_packet["station_findings"] if f["station_id"] == 6)
    assert demo_packet["data_mode"] == "synthetic"
    assert demo_packet["provenance"]["side_effects"] == "none"
    assert finding["reason"] == "extreme_departure" and finding["severity"] == "strong"
    assert finding["summary"]["observed_f"] == 120
    assert finding["summary"]["expected_f"] == pytest.approx(40.475)
    calls = []
    instance = dispatcher(environment, calls)
    result = instance.demo("demo-one")
    assert result["status"] == "provider_accepted" and result["provider_status"] == "queued"
    assert result["provider_message_id"] == MESSAGE_SID and result["mode"] == "demo"
    assert result["transport"] == "twilio" and result["data_mode"] == "synthetic"
    assert result["duplicate"] is False
    payload = parse_qs(calls[0].content.decode())
    assert payload["To"] == [environment["TERRA_SMS_TO_NUMBER"]]
    assert payload["From"] == [environment["TWILIO_FROM_NUMBER"]]
    assert set(payload) == {"To", "From", "Body", "ValidityPeriod"}
    assert payload["ValidityPeriod"] == ["300"]
    assert payload["Body"][0].startswith("TERRA DEMO - simulated flag.")
    assert "2026-01-11 16:00 UTC" in payload["Body"][0]
    assert "120.0 F; expected 40.5 F (+79.5 F)" in payload["Body"][0]
    assert "not confirmed sensor failure" in payload["Body"][0]
    public = json.dumps(result) + json.dumps(instance.status())
    assert environment["TWILIO_AUTH_TOKEN"] not in public
    assert environment["TWILIO_ACCOUNT_SID"] not in public
    assert environment["TERRA_SMS_TO_NUMBER"] not in public
    assert instance.status()["recipient"] == "***0102"


def test_duplicate_demo_and_restart_never_send_twice_or_store_secrets(environment):
    calls = []
    first = dispatcher(environment, calls).demo("same-opening")
    second = dispatcher(environment, calls).demo("same-opening")
    assert first["duplicate"] is False and second["duplicate"] is True
    assert first["provider_message_id"] == second["provider_message_id"] and len(calls) == 1
    with sqlite3.connect(environment["TERRA_SMS_DB_PATH"]) as db:
        rows = db.execute("SELECT * FROM sms_delivery").fetchall()
    assert len(rows) == 1
    stored = repr(rows)
    for secret in (environment["TERRA_SMS_TO_NUMBER"], environment["TWILIO_FROM_NUMBER"],
                   environment["TWILIO_AUTH_TOKEN"], "TERRA DEMO"):
        assert secret not in stored


def test_concurrent_attempts_are_deduplicated_atomically(environment):
    calls = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: dispatcher(environment, calls).demo("concurrent"), range(4)))
    assert len(calls) == 1
    assert sum(not result["duplicate"] for result in results) == 1
    assert all(result["status"] in {"provider_accepted", "uncertain"} for result in results)


@pytest.mark.parametrize("event_id", ["", "bad id", "x" * 129, "../../x", "secret\nvalue", None, 12])
def test_invalid_event_ids_cannot_send_and_are_not_echoed(environment, event_id):
    calls = []
    instance = dispatcher(environment, calls)
    assert instance.demo(event_id) == {"event_id": None, "status": "blocked", "duplicate": False,
                                       "mode": "demo", "error_code": "invalid_event_id"}
    assert instance.receipt(event_id)["event_id"] is None and calls == []


@pytest.mark.parametrize("content_mode", ["terra", "trial_template"])
def test_timeout_is_uncertain_and_never_retried_even_after_restart(environment, content_mode):
    environment["TERRA_SMS_CONTENT_MODE"] = content_mode
    calls = []

    def timeout(request):
        raise httpx.ReadTimeout("private token and phone must never leak", request=request)

    instance = dispatcher(environment, calls, timeout)
    result = instance.demo("timeout")
    assert result["status"] == "uncertain" and result["error_code"] == "provider_outcome_unknown"
    assert "private token" not in json.dumps(result)
    assert dispatcher(environment, calls).demo("timeout")["duplicate"] is True
    assert instance.receipt("timeout")["status"] == "uncertain" and len(calls) == 1


@pytest.mark.parametrize("http_status,payload,outcome,error", [
    (500, {"message": "secret", "code": 20500}, "uncertain", "20500"),
    (408, {"message": "secret"}, "uncertain", "provider_outcome_unknown"),
    (429, {"message": "secret", "code": 20429}, "failed", "20429"),
    (400, {"message": "secret", "code": 21608}, "failed", "21608"),
    (401, {"message": "secret", "code": 20003}, "failed", "20003"),
    (302, {"message": "secret"}, "failed", "provider_rejected"),
])
def test_provider_failure_is_redacted_and_never_blindly_repeated(environment, http_status, payload, outcome, error):
    calls = []
    instance = dispatcher(environment, calls, lambda _: httpx.Response(http_status, json=payload,
        headers={"Location": "https://example.invalid/steal-auth"}))
    result = instance.demo("failure")
    assert result["status"] == outcome and result["error_code"] == error
    assert "secret" not in json.dumps(result)
    assert instance.demo("failure")["duplicate"] is True and len(calls) == 1


@pytest.mark.parametrize("payload", [{}, {"sid": "arbitrary", "status": "queued"},
    {"sid": MESSAGE_SID, "status": "made_up"}, {"sid": MESSAGE_SID}, []])
def test_success_with_invalid_sid_or_status_is_uncertain(environment, payload):
    calls = []
    result = dispatcher(environment, calls, lambda _: httpx.Response(201, json=payload)).demo("malformed")
    assert result["status"] == "uncertain" and result["error_code"] == "provider_invalid_response"
    assert len(calls) == 1


def test_non_json_success_is_uncertain(environment):
    result = dispatcher(environment, handler=lambda _: httpx.Response(201, text="<private-error>")).demo("bad-json")
    assert result["status"] == "uncertain" and "private" not in json.dumps(result)


def test_receipt_requires_provider_delivery_evidence_and_never_posts_again(environment):
    calls = []
    instance = dispatcher(environment, calls, lambda request: httpx.Response(201 if request.method == "POST" else 200,
        json={"sid": MESSAGE_SID, "status": "queued" if request.method == "POST" else "delivered"}))
    assert instance.demo("receipt")["status"] == "provider_accepted"
    received = instance.receipt("receipt")
    assert received["status"] == received["provider_status"] == "delivered"
    assert calls[1].method == "GET" and str(calls[1].url).endswith("/Messages/" + MESSAGE_SID + ".json")
    assert instance.receipt("receipt")["status"] == "delivered" and len(calls) == 2


@pytest.mark.parametrize("provider_status,error", [("undelivered", 30032), ("failed", 30007), ("canceled", 21610)])
def test_failed_delivery_retains_safe_carrier_error(environment, provider_status, error):
    calls = []
    instance = dispatcher(environment, calls, lambda request: httpx.Response(200,
        json={"sid": MESSAGE_SID, "status": "queued" if request.method == "POST" else provider_status,
              "error_code": error, "error_message": environment["TERRA_SMS_TO_NUMBER"]}))
    instance.demo("receipt-failure")
    result = instance.receipt("receipt-failure")
    assert result["status"] == provider_status and result["error_code"] == str(error)
    assert environment["TERRA_SMS_TO_NUMBER"] not in json.dumps(result)
    assert len(calls) == 2


def test_mismatched_receipt_does_not_claim_delivery(environment):
    instance = dispatcher(environment, handler=lambda request: httpx.Response(200,
        json={"sid": MESSAGE_SID if request.method == "POST" else "SM" + "b" * 32,
              "status": "queued" if request.method == "POST" else "delivered"}))
    instance.demo("mismatch")
    result = instance.receipt("mismatch")
    assert result["status"] == "provider_accepted" and result["receipt_error"] == "provider_invalid_receipt"


def test_receipt_provider_error_preserves_last_known_evidence(environment):
    instance = dispatcher(environment, handler=lambda request: httpx.Response(201 if request.method == "POST" else 503,
        json={"sid": MESSAGE_SID, "status": "sent"} if request.method == "POST" else {"message": "private"}))
    instance.demo("unavailable-receipt")
    result = instance.receipt("unavailable-receipt")
    assert result["status"] == "provider_accepted" and result["provider_status"] == "sent"
    assert result["receipt_error"] == "provider_receipt_unavailable"
    assert instance.receipt("missing")["status"] == "not_found"


def test_concurrent_receipt_cannot_regress_delivered_to_old_sent_state(environment):
    stale_started, release_stale = Event(), Event()
    receipt_count = [0]

    def respond(request):
        if request.method == "POST":
            return httpx.Response(201, json={"sid": MESSAGE_SID, "status": "queued"})
        receipt_count[0] += 1
        if receipt_count[0] == 1:
            stale_started.set()
            assert release_stale.wait(5)
            return httpx.Response(200, json={"sid": MESSAGE_SID, "status": "sent"})
        return httpx.Response(200, json={"sid": MESSAGE_SID, "status": "delivered"})

    instance = dispatcher(environment, handler=respond)
    instance.demo("interleaved")
    with ThreadPoolExecutor(max_workers=1) as pool:
        stale = pool.submit(instance.receipt, "interleaved")
        assert stale_started.wait(5)
        try:
            assert instance.receipt("interleaved")["status"] == "delivered"
        finally:
            release_stale.set()
        assert stale.result(timeout=5)["status"] == "delivered"
    assert dispatcher(environment).receipt("interleaved")["status"] == "delivered"


def test_persistent_disable_survives_environment_reenable_and_restart(environment):
    calls = []
    first = dispatcher(environment, calls)
    disabled = first.disable()
    assert disabled["status"] == "disabled" and disabled["suppressed"] is True
    assert disabled["enabled"] is disabled["demo_enabled"] is disabled["live_enabled"] is False
    restarted = dispatcher(environment, calls)
    assert restarted.demo("after-disable")["status"] == "suppressed" and calls == []
    assert restarted.enable()["status"] == "enabled"
    assert restarted.demo("after-disable")["status"] == "provider_accepted" and len(calls) == 1
    assert restarted.disable()["status"] == "disabled"
    assert dispatcher({**environment, "TERRA_SMS_ENABLED": "false"}, calls).enable()["status"] == "blocked"
    assert dispatcher(environment, calls).status()["suppressed"] is True


def test_daily_attempt_limit_persists_and_resets_on_next_utc_day(environment):
    calls = []
    env = {**environment, "TERRA_SMS_MAX_PER_DAY": "2"}
    instance = dispatcher(env, calls)
    assert instance.demo("one")["status"] == "provider_accepted"
    assert instance.demo("two")["status"] == "provider_accepted"
    assert dispatcher(env, calls).demo("three")["status"] == "rate_limited" and len(calls) == 2
    later = dispatcher(env, calls, clock=lambda: NOW + timedelta(days=1))
    assert later.demo("three")["status"] == "provider_accepted" and len(calls) == 3


def test_corrupt_or_unwritable_store_prevents_send(environment, tmp_path):
    calls = []
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_text("not a sqlite database", encoding="utf-8")
    instance = dispatcher({**environment, "TERRA_SMS_DB_PATH": str(corrupt)}, calls)
    assert "delivery_store_unavailable" in instance.status()["configuration_errors"]
    assert instance.demo("corrupt")["status"] == "not_configured" and calls == []


def test_live_finding_dispatches_once_and_never_mutates_assessment(environment, demo_packet):
    calls = []
    packet = live_packet(demo_packet)
    original = copy.deepcopy(packet)
    instance = dispatcher(environment, calls)
    results = instance.dispatch_assessment(packet)
    assert len(results) == 1 and results[0]["mode"] == "live" and results[0]["status"] == "provider_accepted"
    assert results[0]["event_id"].startswith("live-")
    repeated = instance.dispatch_assessment(packet)
    assert repeated[0]["duplicate"] is True and len(calls) == 1
    assert packet == original
    assert "DEMO" not in parse_qs(calls[0].content.decode())["Body"][0]


@pytest.mark.parametrize("alter", [
    lambda p: p.update(data_mode="synthetic"), lambda p: p.update(data_mode="injected"),
    lambda p: p.update(data_mode="historical_replay"), lambda p: p["provenance"].pop("live_source"),
    lambda p: p["provenance"]["live_source"].update(status="cached_after_error"),
    lambda p: p["provenance"]["live_source"].update(failed_station_ids=[6]),
    lambda p: p["provenance"]["live_source"].update(warning="refresh failed"),
    lambda p: p["provenance"]["live_source"].update(checked_at_utc=(NOW - timedelta(minutes=6)).isoformat(),
        retrieved_at_utc=(NOW - timedelta(minutes=6)).isoformat()),
    lambda p: p["provenance"]["live_source"].update(retrieved_at_utc=(NOW - timedelta(hours=7)).isoformat()),
    lambda p: p["query"].update(max_age_hours=100),
    lambda p: p["query"].update(as_of_utc=(NOW + timedelta(minutes=1)).isoformat()),
    lambda p: p["station_findings"][0].update(freshness="stale"),
    lambda p: p["station_findings"][0].update(reference_id="different-reference"),
    lambda p: p["station_findings"][0]["evidence"][-1].update(observation_available=False),
    lambda p: p["station_findings"][0]["evidence"][-1].update(source_quality="missing"),
    lambda p: p["station_findings"][0]["evidence"][-1].update(original_source_flag="M"),
    lambda p: p["station_findings"][0]["evidence"][-1].update(expected_f=None),
    lambda p: p.update(station_findings=[]),
])
def test_ineligible_live_packets_never_reach_real_transport(environment, demo_packet, alter):
    calls = []
    packet = live_packet(demo_packet)
    alter(packet)
    assert dispatcher(environment, calls).dispatch_assessment(packet) == [] and calls == []


def test_stale_live_packet_and_malformed_packets_are_safely_withheld(environment, demo_packet):
    calls = []
    instance = dispatcher(environment, calls, clock=lambda: NOW + timedelta(hours=7))
    assert instance.dispatch_assessment(live_packet(demo_packet)) == []
    for packet in (None, {}, [], {"data_mode": "current", "schema_version": "terra-location-assessment-v1"}):
        assert instance.dispatch_assessment(packet) == []
    assert calls == []


def test_continuing_live_readings_keep_same_opening_delivery_identity(environment, demo_packet):
    calls = []
    clock = [NOW]
    instance = dispatcher(environment, calls, clock=lambda: clock[0])
    packet = live_packet(demo_packet)
    first = instance.dispatch_assessment(packet)[0]
    clock[0] += timedelta(hours=1)
    new_valid = (clock[0] - timedelta(minutes=5)).isoformat()
    packet["query"].update(valid_utc=new_valid, as_of_utc=clock[0].isoformat())
    packet["provenance"]["data_snapshot_id"] = "a-new-snapshot"
    packet["provenance"]["live_source"].update(checked_at_utc=clock[0].isoformat(),
        retrieved_at_utc=clock[0].isoformat(), data_through_utc=new_valid)
    finding = packet["station_findings"][0]
    finding["end_utc"] = new_valid
    finding["evidence"].append({**finding["evidence"][-1], "timestamp": new_valid, "temperature_f": 121.0})
    second = instance.dispatch_assessment(packet)[0]
    assert first["event_id"] == second["event_id"] and second["duplicate"] is True and len(calls) == 1


def test_live_sliding_source_window_preserves_continuing_incident_after_restart(environment, demo_packet):
    calls = []
    clock = [NOW]
    packet = live_packet(demo_packet)
    first = dispatcher(environment, calls, clock=lambda: clock[0]).dispatch_assessment(packet)[0]
    for hour in range(1, 72):
        clock[0] = NOW + timedelta(hours=hour)
        valid = clock[0] - timedelta(minutes=5)
        packet["query"].update(valid_utc=valid.isoformat(), as_of_utc=clock[0].isoformat())
        packet["provenance"]["live_source"].update(checked_at_utc=clock[0].isoformat(),
            retrieved_at_utc=clock[0].isoformat(), data_through_utc=valid.isoformat())
        finding = packet["station_findings"][0]
        sliding_start = max(NOW - timedelta(minutes=5), valid - timedelta(hours=47))
        finding.update(start_utc=sliding_start.isoformat(), detected_at_utc=sliding_start.isoformat(),
                       end_utc=valid.isoformat(), visible_at_utc=sliding_start.isoformat())
        finding["evidence"] = [{**finding["evidence"][-1], "timestamp": valid.isoformat()}]
        result = dispatcher(environment, calls, clock=lambda: clock[0]).dispatch_assessment(packet)[0]
        assert result["event_id"] == first["event_id"] and result["duplicate"] is True
    assert len(calls) == 1


def test_live_new_episode_after_evidence_gap_sends_new_opening_and_old_results_are_withheld(environment, demo_packet):
    calls = []
    clock = [NOW]
    packet = live_packet(demo_packet)
    instance = dispatcher(environment, calls, clock=lambda: clock[0])
    first = instance.dispatch_assessment(packet)[0]
    old_packet = copy.deepcopy(packet)
    clock[0] += timedelta(hours=2)
    valid = (clock[0] - timedelta(minutes=5)).isoformat()
    packet["query"].update(valid_utc=valid, as_of_utc=clock[0].isoformat())
    packet["provenance"]["live_source"].update(checked_at_utc=clock[0].isoformat(),
        retrieved_at_utc=clock[0].isoformat(), data_through_utc=valid)
    finding = packet["station_findings"][0]
    finding.update(start_utc=valid, detected_at_utc=valid, end_utc=valid, visible_at_utc=valid)
    finding["evidence"] = [{**finding["evidence"][-1], "timestamp": valid}]
    second = instance.dispatch_assessment(packet)[0]
    assert second["event_id"] != first["event_id"] and second["duplicate"] is False and len(calls) == 2
    assert instance.dispatch_assessment(old_packet) == [] and len(calls) == 2


def test_trusted_notification_and_alias_send_immutable_text_once_across_restart(environment, notification):
    calls = []
    original = copy.deepcopy(notification)
    result = dispatcher(environment, calls).dispatch_event(notification)
    assert result["status"] == "provider_accepted" and result["mode"] == "farm"
    assert result["source_event_id"] == notification["event_id"]
    assert result["event_id"].startswith("farm-") and len(result["event_id"]) == 69
    assert result["event_id"] != notification["event_id"]
    repeated = dispatcher(environment, calls).dispatch_message(notification)
    assert repeated["event_id"] == result["event_id"] and repeated["duplicate"] is True
    assert len(calls) == 1 and notification == original
    posted = parse_qs(calls[0].content.decode())
    assert posted["Body"] == [notification["text"]] and posted["ValidityPeriod"] == ["300"]
    public = json.dumps(result)
    assert notification["text"] not in public
    assert environment["TERRA_SMS_TO_NUMBER"] not in public


def test_same_notification_id_with_llm_text_variation_is_a_conflict_not_another_sms(environment, notification):
    calls = []
    first = dispatcher(environment, calls).dispatch_event(notification)
    varied = {**notification, "text": "A new LLM paraphrase of exactly the same immutable farm event."}
    second = dispatcher(environment, calls).dispatch_event(varied)
    assert second["status"] == "blocked" and second["error_code"] == "event_id_conflict"
    assert second["event_id"] == first["event_id"] and len(calls) == 1


def test_notification_receipt_uses_returned_ledger_id_and_stored_provider_sid(environment, notification):
    calls = []
    instance = dispatcher(environment, calls, lambda request: httpx.Response(201 if request.method == "POST" else 200,
        json={"sid": MESSAGE_SID, "status": "queued" if request.method == "POST" else "delivered"}))
    sent = instance.dispatch_event(notification)
    receipt = instance.receipt(sent["event_id"])
    assert receipt["status"] == "delivered" and receipt["event_id"] == sent["event_id"]
    assert receipt["source_event_id"] == notification["event_id"]
    assert calls[-1].method == "GET" and str(calls[-1].url).endswith("/Messages/" + MESSAGE_SID + ".json")
    assert instance.receipt(notification["event_id"])["status"] == "not_found"


def test_notification_timeout_never_resends_after_restart(environment, notification):
    calls = []

    def timeout(request):
        raise httpx.ReadTimeout("private provider details", request=request)

    first = dispatcher(environment, calls, timeout).dispatch_event(notification)
    second = dispatcher(environment, calls).dispatch_event(notification)
    assert first["status"] == second["status"] == "uncertain"
    assert second["duplicate"] is True and len(calls) == 1
    assert "private provider details" not in json.dumps(first)


@pytest.mark.parametrize("observation", [None, (NOW - timedelta(days=30)).isoformat()])
def test_fresh_health_check_may_report_null_or_stale_subject_observation(environment, notification, observation):
    calls = []
    notification.update(category="data_health", observation_time_utc=observation,
                        text="The orchard station has no new data; check its connection.",
                        expires_at_utc=(NOW + timedelta(seconds=90)).isoformat())
    result = dispatcher(environment, calls).dispatch_event(notification)
    assert result["status"] == "provider_accepted" and len(calls) == 1
    assert parse_qs(calls[0].content.decode())["ValidityPeriod"] == ["90"]


@pytest.mark.parametrize("changes,code", [
    ({"data_mode": "replay"}, "noncurrent_notification"),
    ({"data_mode": "injected"}, "noncurrent_notification"),
    ({"data_mode": "synthetic"}, "noncurrent_notification"),
    ({"eligibility": {"eligible": False, "reason": "resolved"}}, "notification_ineligible"),
    ({"eligibility": {"eligible": "true"}}, "invalid_notification_eligibility"),
    ({"checked_at_utc": (NOW - timedelta(minutes=6)).isoformat()}, "stale_monitor_check"),
    ({"checked_at_utc": (NOW + timedelta(seconds=1)).isoformat()}, "invalid_notification_times"),
    ({"created_at_utc": (NOW + timedelta(seconds=1)).isoformat()}, "invalid_notification_times"),
    ({"expires_at_utc": (NOW - timedelta(seconds=1)).isoformat()}, "event_expired"),
    ({"expires_at_utc": (NOW + timedelta(milliseconds=900)).isoformat()}, "event_expired"),
    ({"expires_at_utc": (NOW + timedelta(hours=7)).isoformat()}, "invalid_notification_times"),
    ({"observation_time_utc": None}, "missing_observation_time"),
    ({"observation_time_utc": (NOW - timedelta(hours=6, seconds=1)).isoformat()}, "stale_observation"),
    ({"category": "data_health", "observation_time_utc": (NOW + timedelta(seconds=1)).isoformat()}, "invalid_observation_time"),
    ({"created_at_utc": "2026-01-11T16:00:00"}, "invalid_notification"),
    ({"checked_at_utc": "not-a-clock"}, "invalid_notification"),
    ({"observation_time_utc": "2026-01-11T16:00:00"}, "invalid_notification"),
    ({"text": ""}, "invalid_notification_text"),
    ({"text": "x" * 1201}, "invalid_notification_text"),
    ({"text": "x\x00private"}, "invalid_notification_text"),
    ({"text": "\ud800"}, "invalid_notification"),
    ({"orchard_id": ""}, "invalid_orchard_id"),
    ({"source_event_ids": []}, "invalid_notification_identifiers"),
    ({"source_event_ids": ["same", "same"]}, "invalid_notification_identifiers"),
    ({"episode_ids": "episode"}, "invalid_notification_identifiers"),
    ({"kind": "arbitrary_text"}, "invalid_notification_kind"),
    ({"transition": "unknown"}, "invalid_notification_kind"),
    ({"severity": "urgent"}, "invalid_notification_category"),
    ({"category": "unknown"}, "invalid_notification_category"),
    ({"schema_version": "another-schema"}, "invalid_notification_schema"),
    ({"schema": "another-schema"}, "invalid_notification_schema"),
])
def test_ineligible_expired_future_or_malformed_notifications_cannot_send(environment, notification, changes, code):
    calls = []
    result = dispatcher(environment, calls).dispatch_event({**notification, **changes})
    assert result["status"] in {"blocked", "suppressed", "expired"}
    assert result["error_code"] == code and calls == []
    assert environment["TWILIO_AUTH_TOKEN"] not in json.dumps(result)
    assert environment["TERRA_SMS_TO_NUMBER"] not in json.dumps(result)


@pytest.mark.parametrize("envelope", [None, [], {}, {"event_id": "bad event id"},
    {"event_id": "private\nidentifier"}, {"event_id": 1}])
def test_invalid_notification_envelopes_never_echo_unsafe_ids(environment, envelope):
    calls = []
    result = dispatcher(environment, calls).dispatch_event(envelope)
    assert result["status"] == "blocked" and result["event_id"] is None and calls == []
    assert "source_event_id" not in result


def test_notification_schema_alias_and_exact_body_limit_are_accepted(environment, notification):
    calls = []
    notification["schema"] = notification.pop("schema_version")
    notification["text"] = "x" * 1200
    assert dispatcher(environment, calls).dispatch_event(notification)["status"] == "provider_accepted"
    assert len(parse_qs(calls[0].content.decode())["Body"][0]) == 1200


@pytest.mark.parametrize("content_mode", ["terra", "trial_template"])
def test_notification_rechecks_expiry_after_blocking_ledger_reservation(environment, notification, monkeypatch, content_mode):
    environment["TERRA_SMS_CONTENT_MODE"] = content_mode
    calls = []
    clock = [NOW]
    instance = dispatcher(environment, calls, clock=lambda: clock[0])
    notification["expires_at_utc"] = (NOW + timedelta(seconds=2)).isoformat()
    reserve = instance._reserve

    def slow_reserve(*args):
        result = reserve(*args)
        clock[0] += timedelta(seconds=3)
        return result

    monkeypatch.setattr(instance, "_reserve", slow_reserve)
    result = instance.dispatch_event(notification)
    assert result["status"] == "expired" and result["error_code"] == "event_expired" and calls == []


def test_notification_validity_is_floored_and_rechecked_before_http(environment, notification, monkeypatch):
    calls = []
    clock = [NOW]
    instance = dispatcher(environment, calls, clock=lambda: clock[0])
    notification["expires_at_utc"] = (NOW + timedelta(seconds=30.9)).isoformat()
    reserve = instance._reserve

    def slow_reserve(*args):
        result = reserve(*args)
        clock[0] += timedelta(seconds=10.2)
        return result

    monkeypatch.setattr(instance, "_reserve", slow_reserve)
    assert instance.dispatch_event(notification)["status"] == "provider_accepted"
    assert parse_qs(calls[0].content.decode())["ValidityPeriod"] == ["20"]


@pytest.mark.parametrize("content_mode", ["terra", "trial_template"])
def test_notification_fixed_recipient_gates_and_daily_cap_apply(environment, notification, content_mode):
    environment["TERRA_SMS_CONTENT_MODE"] = content_mode
    calls = []
    instance = dispatcher({**environment, "TERRA_SMS_MAX_PER_DAY": "1"}, calls)
    assert instance.disable()["status"] == "disabled"
    assert instance.dispatch_event(notification)["status"] == "suppressed" and calls == []
    assert instance.enable()["status"] == "enabled"
    assert instance.dispatch_event(notification)["status"] == "provider_accepted"
    another = {**notification, "event_id": "combined-farm-message-2"}
    assert instance.dispatch_event(another)["status"] == "rate_limited" and len(calls) == 1
    assert dispatcher({**environment, "TERRA_SMS_ENABLED": "false"}, calls).dispatch_event(another)["status"] == "disabled"


@pytest.mark.parametrize("observed", [None, (NOW - timedelta(hours=18)).isoformat()])
def test_fresh_eligible_daily_chill_can_describe_older_completed_day_cutoff(environment, notification, observed):
    calls = []
    notification.update(category="chill", observation_time_utc=observed,
                        text="Daily chill is below the selected historical range through the last completed day.")
    assert dispatcher(environment, calls).dispatch_event(notification)["status"] == "provider_accepted"
    assert len(calls) == 1


@pytest.mark.parametrize("changes,code", [
    ({"eligibility": {"eligible": False, "reason": "wrong_completed_day"}}, "notification_ineligible"),
    ({"expires_at_utc": (NOW - timedelta(seconds=1)).isoformat()}, "event_expired"),
])
def test_daily_chill_still_requires_current_backend_eligibility_and_unexpired_message(environment, notification, changes, code):
    calls = []
    notification.update(category="chill", observation_time_utc=(NOW - timedelta(hours=18)).isoformat(), **changes)
    result = dispatcher(environment, calls).dispatch_event(notification)
    assert result["error_code"] == code and calls == []


@pytest.mark.parametrize("content_mode", ["terra", "trial_template"])
def test_definitive_rate_limit_retries_only_when_due_across_restart_and_keeps_attempt_evidence(environment, notification, content_mode):
    environment["TERRA_SMS_CONTENT_MODE"] = content_mode
    calls = []
    clock = [NOW]

    def respond(_):
        return httpx.Response(429, json={"code": 20429, "message": "private rejection"}) if len(calls) < 3 else httpx.Response(201,
            json={"sid": MESSAGE_SID, "status": "queued"})

    def dispatch():
        notification["checked_at_utc"] = clock[0].isoformat()
        return dispatcher(environment, calls, respond, clock=lambda: clock[0]).dispatch_event(notification)

    first = dispatch()
    assert first["status"] == "retry_wait" and first["attempt_count"] == 1
    assert first["next_attempt_at_utc"] == (NOW + timedelta(seconds=30)).isoformat()
    clock[0] += timedelta(seconds=29)
    waiting = dispatch()
    assert waiting["status"] == "retry_wait" and waiting["duplicate"] is True and len(calls) == 1
    clock[0] += timedelta(seconds=1)
    second = dispatch()
    assert second["status"] == "retry_wait" and second["attempt_count"] == 2
    assert second["next_attempt_at_utc"] == (NOW + timedelta(seconds=150)).isoformat()
    clock[0] += timedelta(seconds=119)
    assert dispatch()["duplicate"] is True and len(calls) == 2
    clock[0] += timedelta(seconds=1)
    third = dispatch()
    assert third["status"] == "provider_accepted" and third["attempt_count"] == 3
    assert "next_attempt_at_utc" not in third and len(calls) == 3
    clock[0] += timedelta(seconds=60)
    assert dispatch()["duplicate"] is True and len(calls) == 3
    with sqlite3.connect(environment["TERRA_SMS_DB_PATH"]) as db:
        history = db.execute("SELECT attempt_no,outcome,error_code FROM sms_attempt ORDER BY attempt_no").fetchall()
    assert history == [(1, "retry_wait", "20429"), (2, "retry_wait", "20429"), (3, "provider_accepted", None)]


def test_three_definitive_rejections_exhaust_retry_budget_without_fourth_send(environment, notification):
    calls = []
    clock = [NOW]
    for elapsed, attempt in ((0, 1), (30, 2), (150, 3), (300, 3)):
        clock[0] = NOW + timedelta(seconds=elapsed)
        notification["checked_at_utc"] = clock[0].isoformat()
        instance = dispatcher(environment, calls, lambda _: httpx.Response(429, json={"code": 20429}), clock=lambda: clock[0])
        result = instance.dispatch_event(notification)
        assert result["attempt_count"] == attempt
        assert result["status"] == ("retry_wait" if elapsed < 150 else "failed")
    assert len(calls) == 3 and result["error_code"] == "20429" and "next_attempt_at_utc" not in result


def test_pending_rejection_retry_is_withheld_after_expiry_or_disable(environment, notification):
    calls = []
    clock = [NOW]
    notification["expires_at_utc"] = (NOW + timedelta(seconds=90)).isoformat()
    instance = dispatcher(environment, calls, lambda _: httpx.Response(429, json={"code": 20429}), clock=lambda: clock[0])
    assert instance.dispatch_event(notification)["status"] == "retry_wait"
    instance.disable()
    clock[0] += timedelta(seconds=30)
    notification["checked_at_utc"] = clock[0].isoformat()
    assert dispatcher(environment, calls, clock=lambda: clock[0]).dispatch_event(notification)["status"] == "suppressed"
    instance.enable()
    clock[0] += timedelta(seconds=61)
    notification["checked_at_utc"] = clock[0].isoformat()
    assert dispatcher(environment, calls, clock=lambda: clock[0]).dispatch_event(notification)["status"] == "expired"
    assert len(calls) == 1


@pytest.mark.parametrize("payload", [
    {"code": 20429, "sid": MESSAGE_SID},
    {"code": 20429, "sid": MESSAGE_SID, "status": "queued"},
    {"code": 20429, "status": "accepted"},
])
def test_rate_limit_with_possible_acceptance_is_uncertain_and_never_retried(environment, notification, payload):
    calls = []
    clock = [NOW]
    first = dispatcher(environment, calls, lambda _: httpx.Response(429, json=payload), clock=lambda: clock[0]).dispatch_event(notification)
    assert first["status"] == "uncertain" and "next_attempt_at_utc" not in first
    clock[0] += timedelta(seconds=60)
    notification["checked_at_utc"] = clock[0].isoformat()
    second = dispatcher(environment, calls, clock=lambda: clock[0]).dispatch_event(notification)
    assert second["status"] == "uncertain" and second["duplicate"] is True and len(calls) == 1


def test_retry_respects_daily_attempt_cap_and_immutable_text(environment, notification):
    calls = []
    clock = [NOW]
    env = {**environment, "TERRA_SMS_MAX_PER_DAY": "1"}
    instance = dispatcher(env, calls, lambda _: httpx.Response(429, json={"code": 20429}), clock=lambda: clock[0])
    assert instance.dispatch_event(notification)["status"] == "retry_wait"
    clock[0] += timedelta(seconds=30)
    notification["checked_at_utc"] = clock[0].isoformat()
    assert dispatcher(env, calls, clock=lambda: clock[0]).dispatch_event(notification)["status"] == "rate_limited"
    changed = {**notification, "text": "A different LLM text while the first message awaits retry."}
    conflict = dispatcher(env, calls, clock=lambda: clock[0]).dispatch_event(changed)
    assert conflict["error_code"] == "event_id_conflict" and len(calls) == 1


def test_existing_pre_retry_ledger_migrates_without_losing_prior_no_resend_evidence(environment, notification):
    calls = []
    instance = dispatcher(environment, calls)
    first = instance.dispatch_event(notification)
    # Reproduce exactly the previous schema while preserving its delivery row.
    with sqlite3.connect(environment["TERRA_SMS_DB_PATH"]) as db:
        db.execute("DROP TABLE sms_attempt")
        db.execute("ALTER TABLE sms_delivery DROP COLUMN attempt_count")
        db.execute("ALTER TABLE sms_delivery DROP COLUMN next_attempt_at")
        db.execute("ALTER TABLE sms_delivery DROP COLUMN source_event_id")
    repeated = dispatcher(environment, calls).dispatch_event(notification)
    assert repeated["event_id"] == first["event_id"] and repeated["duplicate"] is True
    assert repeated["attempt_count"] == 1 and len(calls) == 1
    assert repeated["source_event_id"] == notification["event_id"]


@pytest.mark.parametrize("outcome", ["timeout", "http408", "http500", "invalid_success", "http400"])
def test_ambiguous_or_other_rejection_after_one_safe_retry_never_retries_again(environment, notification, outcome):
    calls = []
    clock = [NOW]

    def respond(request):
        if len(calls) == 1:
            return httpx.Response(429, json={"code": 20429})
        if outcome == "timeout":
            raise httpx.ReadTimeout("private outcome", request=request)
        if outcome == "invalid_success":
            return httpx.Response(201, json={"status": "queued"})
        return httpx.Response(int(outcome[4:]), json={"code": 20003})

    first = dispatcher(environment, calls, respond, clock=lambda: clock[0]).dispatch_event(notification)
    assert first["status"] == "retry_wait"
    clock[0] += timedelta(seconds=30)
    notification["checked_at_utc"] = clock[0].isoformat()
    second = dispatcher(environment, calls, respond, clock=lambda: clock[0]).dispatch_event(notification)
    assert second["status"] == ("failed" if outcome == "http400" else "uncertain")
    assert second["attempt_count"] == 2 and "next_attempt_at_utc" not in second
    clock[0] += timedelta(seconds=150)
    notification["checked_at_utc"] = clock[0].isoformat()
    third = dispatcher(environment, calls, clock=lambda: clock[0]).dispatch_event(notification)
    assert third["status"] == second["status"] and third["duplicate"] is True and len(calls) == 2


def test_concurrent_due_retries_claim_one_new_attempt(environment, notification):
    calls = []
    clock = [NOW]
    first = dispatcher(environment, calls, lambda _: httpx.Response(429, json={"code": 20429}), clock=lambda: clock[0]).dispatch_event(notification)
    assert first["status"] == "retry_wait"
    clock[0] += timedelta(seconds=30)
    notification["checked_at_utc"] = clock[0].isoformat()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: dispatcher(environment, calls, clock=lambda: clock[0]).dispatch_event(notification), range(4)))
    assert len(calls) == 2
    assert all(result["attempt_count"] == 2 for result in results)
    assert sum(result["duplicate"] is False for result in results) == 1


def test_notification_deadline_is_rechecked_after_slow_client_initialization(environment, notification, monkeypatch):
    calls = []
    clock = [NOW]
    instance = dispatcher(environment, clock=lambda: clock[0])
    notification["expires_at_utc"] = (NOW + timedelta(seconds=2)).isoformat()

    class SlowClient:
        def __enter__(self):
            clock[0] += timedelta(seconds=3)
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            calls.append((args, kwargs))
            return httpx.Response(201, json={"sid": MESSAGE_SID, "status": "queued"})

    monkeypatch.setattr(instance, "_client", lambda: SlowClient())
    result = instance.dispatch_event(notification)
    assert result["status"] == "expired" and calls == []


def test_pending_receipts_keep_original_farm_ids_and_poll_oldest_bounded_batch_across_restart(environment, notification):
    calls = []
    clock = [NOW]

    def respond(request):
        return httpx.Response(201 if request.method == "POST" else 200,
            json={"sid": MESSAGE_SID, "status": "queued" if request.method == "POST" else "delivered"})

    for index in range(3):
        clock[0] = NOW + timedelta(seconds=index)
        envelope = {**notification, "event_id": "farm-message-" + str(index), "checked_at_utc": clock[0].isoformat()}
        assert dispatcher(environment, calls, respond, clock=lambda: clock[0]).dispatch_event(envelope)["status"] == "provider_accepted"
    restarted = dispatcher(environment, calls, respond, clock=lambda: clock[0])
    first = restarted.reconcile_pending(limit=2)
    assert [result["source_event_id"] for result in first] == ["farm-message-0", "farm-message-1"]
    assert all(result["status"] == "delivered" for result in first)
    second = restarted.poll_pending_receipts(limit=1)
    assert [result["source_event_id"] for result in second] == ["farm-message-2"]
    assert restarted.reconcile_pending(limit=100) == []
    assert [request.method for request in calls] == ["POST", "POST", "POST", "GET", "GET", "GET"]


@pytest.mark.parametrize("limit", [0, -1, 101, True, 1.5, "10"])
def test_receipt_batch_invalid_limits_are_safe_and_cannot_trigger_network(environment, limit):
    calls = []
    result = dispatcher(environment, calls).reconcile_pending(limit)
    assert len(result) == 1 and result[0]["error_code"] == "invalid_receipt_limit" and calls == []


def test_receipt_batch_provider_error_retains_source_id_without_resending(environment, notification):
    calls = []

    def respond(request):
        if request.method == "POST":
            return httpx.Response(201, json={"sid": MESSAGE_SID, "status": "sent"})
        return httpx.Response(503, json={"message": environment["TERRA_SMS_TO_NUMBER"]})

    first = dispatcher(environment, calls, respond).dispatch_event(notification)
    result = dispatcher(environment, calls, respond).reconcile_pending()
    assert len(result) == 1 and result[0]["event_id"] == first["event_id"]
    assert result[0]["source_event_id"] == notification["event_id"]
    assert result[0]["status"] == "provider_accepted" and result[0]["receipt_error"] == "provider_receipt_unavailable"
    assert environment["TERRA_SMS_TO_NUMBER"] not in json.dumps(result)
    assert [request.method for request in calls] == ["POST", "GET"]


def test_receipt_batch_does_not_retry_uncertain_sends_without_sid(environment, notification):
    calls = []

    def timeout(request):
        raise httpx.ReadTimeout("unknown acceptance", request=request)

    assert dispatcher(environment, calls, timeout).dispatch_event(notification)["status"] == "uncertain"
    assert dispatcher(environment, calls).reconcile_pending() == []
    assert len(calls) == 1


def test_relative_journal_path_is_anchored_to_package_and_shares_dedupe_across_cwd(environment, notification, tmp_path, monkeypatch):
    relative = ".terra-local/shared-sms.sqlite3"
    environment["TERRA_SMS_DB_PATH"] = relative
    assert SMSConfig.from_env(environment).db_path == Path(sms.__file__).resolve().parents[2] / relative
    # Use an isolated package anchor for actual writes; the working directory
    # changes model the API and worker being launched from different folders.
    package = tmp_path / "package"
    monkeypatch.setattr(sms, "PACKAGE_ROOT", package)
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    calls = []
    monkeypatch.chdir(one)
    api = dispatcher(environment, calls)
    assert api.config.db_path == package / relative
    assert api.dispatch_event(notification)["status"] == "provider_accepted"
    monkeypatch.chdir(two)
    worker = dispatcher(environment, calls)
    assert worker.config.db_path == api.config.db_path
    assert worker.dispatch_event(notification)["duplicate"] is True and len(calls) == 1


def test_failed_oldest_receipt_poll_cannot_starve_later_healthy_message(environment, notification):
    calls = []
    clock = [NOW]
    other_sid = "SM" + "b" * 32

    def respond(request):
        if request.method == "POST":
            return httpx.Response(201, json={"sid": MESSAGE_SID if len(calls) == 1 else other_sid, "status": "queued"})
        if MESSAGE_SID in str(request.url):
            return httpx.Response(503, json={"message": "temporary unavailable"})
        return httpx.Response(200, json={"sid": other_sid, "status": "delivered"})

    first = dispatcher(environment, calls, respond, clock=lambda: clock[0]).dispatch_event(notification)
    clock[0] += timedelta(seconds=1)
    another = {**notification, "event_id": "another-orchard-message", "checked_at_utc": clock[0].isoformat()}
    second = dispatcher(environment, calls, respond, clock=lambda: clock[0]).dispatch_event(another)
    clock[0] += timedelta(seconds=1)
    failed = dispatcher(environment, calls, respond, clock=lambda: clock[0]).reconcile_pending(limit=1)
    assert failed[0]["event_id"] == first["event_id"] and "receipt_error" in failed[0]
    clock[0] += timedelta(seconds=1)
    healthy = dispatcher(environment, calls, respond, clock=lambda: clock[0]).reconcile_pending(limit=1)
    assert healthy[0]["event_id"] == second["event_id"] and healthy[0]["status"] == "delivered"
    assert [request.method for request in calls] == ["POST", "POST", "GET", "GET"]


def test_trial_template_is_explicit_without_sender_and_posts_only_allowed_fields(environment, notification):
    environment.pop("TWILIO_FROM_NUMBER")
    assert "invalid_or_missing_sender" in SMSConfig.from_env(environment).errors
    environment["TERRA_SMS_CONTENT_MODE"] = "trial_template"
    calls = []
    instance = dispatcher(environment, calls)
    assert instance.status()["enabled"] is True
    assert instance.status()["sender"] is None
    assert instance.status()["content_mode"] == "trial_template"
    assert instance.status()["template_name"] == sms.TRIAL_TEMPLATE
    original = copy.deepcopy(notification)
    sent = instance.dispatch_event(notification)
    assert sent["status"] == "provider_accepted" and sent["mode"] == "farm"
    assert sent["transport"] == "twilio" and sent["data_mode"] == "current"
    assert sent["content_mode"] == "trial_template" and sent["template_name"] == sms.TRIAL_TEMPLATE
    assert parse_qs(calls[0].content.decode()) == {
        "To": [environment["TERRA_SMS_TO_NUMBER"]], "Body": [sms.TRIAL_TEMPLATE]}
    assert notification == original
    with sqlite3.connect(environment["TERRA_SMS_DB_PATH"]) as db:
        wire_hash, source_hash = db.execute("SELECT body_hash,source_body_hash FROM sms_delivery").fetchone()
    assert wire_hash == sms._digest(sms.TRIAL_TEMPLATE)
    assert source_hash == sms._digest(notification["text"]) and source_hash != wire_hash
    assert notification["text"] not in json.dumps(sent)


@pytest.mark.parametrize("value", ["", "trial", "TRIAL_TEMPLATE", "private-invalid-mode"])
def test_invalid_content_mode_fails_closed_without_echoing_operator_value(environment, notification, value):
    calls = []
    instance = dispatcher({**environment, "TERRA_SMS_CONTENT_MODE": value}, calls)
    assert "invalid_content_mode" in instance.status()["configuration_errors"]
    assert instance.dispatch_event(notification)["status"] == "not_configured" and calls == []
    if value:
        assert value not in json.dumps(instance.status()) + repr(instance.config)


def test_trial_receipt_metadata_survives_restart_and_current_configuration_change(environment, notification):
    trial = {**environment, "TERRA_SMS_CONTENT_MODE": "trial_template"}
    calls = []
    sent = dispatcher(trial, calls).dispatch_event(notification)
    restarted = dispatcher(environment, calls, lambda _: httpx.Response(200,
        json={"sid": MESSAGE_SID, "status": "delivered"}))
    receipt = restarted.reconcile_pending()[0]
    assert receipt["event_id"] == sent["event_id"]
    assert receipt["source_event_id"] == notification["event_id"]
    assert receipt["status"] == "delivered"
    assert receipt["transport"] == "twilio" and receipt["data_mode"] == "current"
    assert receipt["content_mode"] == "trial_template" and receipt["template_name"] == sms.TRIAL_TEMPLATE
    assert [request.method for request in calls] == ["POST", "GET"]
    assert restarted.receipt(sent["event_id"])["content_mode"] == "trial_template"
    assert len(calls) == 2


def test_old_ledger_migrates_as_terra_and_never_inherits_current_trial_labels(environment, notification):
    calls = []
    sent = dispatcher(environment, calls).dispatch_event(notification)
    with sqlite3.connect(environment["TERRA_SMS_DB_PATH"]) as db:
        for column in ("content_mode", "template_name", "source_body_hash"):
            db.execute("ALTER TABLE sms_delivery DROP COLUMN " + column)
    trial = {**environment, "TERRA_SMS_CONTENT_MODE": "trial_template"}
    restarted = dispatcher(trial, calls, lambda _: httpx.Response(200,
        json={"sid": MESSAGE_SID, "status": "delivered"}))
    receipt = restarted.receipt(sent["event_id"])
    assert receipt["status"] == "delivered" and receipt["content_mode"] == "terra"
    assert receipt["template_name"] is None
    assert dispatcher(environment, calls).dispatch_event(notification)["duplicate"] is True
    assert [request.method for request in calls] == ["POST", "GET"]


@pytest.mark.parametrize("first_mode,second_mode", [("terra", "trial_template"), ("trial_template", "terra")])
def test_same_event_cannot_change_content_mode_or_create_another_send(environment, notification, first_mode, second_mode):
    calls = []
    first = dispatcher({**environment, "TERRA_SMS_CONTENT_MODE": first_mode}, calls).dispatch_event(notification)
    changed = dispatcher({**environment, "TERRA_SMS_CONTENT_MODE": second_mode}, calls).dispatch_event(notification)
    assert changed["event_id"] == first["event_id"]
    assert changed["status"] == "blocked" and changed["error_code"] == "event_id_conflict"
    assert len(calls) == 1


def test_trial_same_wire_template_preserves_immutable_source_text_and_distinct_event_identity(environment, notification):
    trial = {**environment, "TERRA_SMS_CONTENT_MODE": "trial_template"}
    calls = []
    first = dispatcher(trial, calls).dispatch_event(notification)
    repeated = dispatcher(trial, calls).dispatch_event(notification)
    assert repeated["duplicate"] is True and len(calls) == 1
    rephrased = {**notification, "text": "A different phrasing for the same immutable notification."}
    assert dispatcher(trial, calls).dispatch_event(rephrased)["error_code"] == "event_id_conflict"
    other = dispatcher(trial, calls).dispatch_event({**notification, "event_id": "a-separate-meaningful-event"})
    assert other["event_id"] != first["event_id"] and len(calls) == 2
    assert all(parse_qs(request.content.decode())["Body"] == [sms.TRIAL_TEMPLATE] for request in calls)
