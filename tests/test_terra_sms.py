"""SMS tests use Twilio-shaped local transports; no external message is sent."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from threading import Event
from urllib.parse import parse_qs

import httpx
import pytest

from terra_monitoring.sms import SMSConfig, SMSDispatcher, build_demo_assessment

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


def test_timeout_is_uncertain_and_never_retried_even_after_restart(environment):
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
