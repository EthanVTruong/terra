"""Saved weather replies preserve native availability and never use inference."""
from datetime import timedelta

import pytest

from terra_monitoring.conversation import ConversationService
from terra_monitoring.conversation.local_facts import weather_answer
from terra_monitoring.farm_ops.service import Monitor
from terra_monitoring.farm_ops.store import Store
from terra_qc.qc import CURRENT_MAX_AGE_HOURS
from test_farm_chill_ops import HOUR, packet


class NoWriter:
    def write(self, _):
        raise AssertionError("A saved-weather answer must not use the writer")


@pytest.fixture
def weather(tmp_path):
    clock = [HOUR]
    monitor = Monitor(Store(tmp_path / "weather.sqlite3"), writer=NoWriter(), clock=lambda: clock[0])
    profile = monitor.create_orchard("Peach farm", orchard_id="peach", crops=["peach"],
                                     lat=38.55, lon=-121.8, elevation_ft=65)
    return monitor, profile, clock


def retain(monitor, profile, clock, **options):
    value = packet(profile, clock[0], **options)
    monitor.apply(profile["id"], {"latest": value}, now=clock[0])
    return value


def test_temperature_uses_saved_orchard_estimate_not_station_or_chill_value(weather):
    monitor, profile, clock = weather
    retain(monitor, profile, clock, temperature=52.75)
    before = monitor.store.events(profile["id"])
    text, facts = weather_answer("temperature", monitor, profile["id"])
    assert "52.75°F" in text and "estimated orchard" in text.lower()
    assert "Jan 11, 2026" in text and "PST" in text
    assert facts["temperature_time"] == HOUR.isoformat() and facts["evidence_id"]
    assert facts["comparison_available"] and facts["used_station_ids"] == [139, 226, 235]
    assert monitor.store.events(profile["id"]) == before
    assert monitor.store.messages(profile["id"]) == []


def test_no_data_never_becomes_zero_temperature_or_normal(weather):
    monitor, profile, _ = weather
    for intent in ("temperature", "freshness", "station_findings", "temperature_normal"):
        text, facts = weather_answer(intent, monitor, profile["id"])
        assert facts["temperature_f"] is None and not facts["comparison_available"]
        assert "0°F" not in text and "no supporting-station discrepancy" not in text.lower()
        assert "not available" in text or "isn't available" in text or "no weather reading" in text.lower() or "can't" in text or "isn't enough" in text


def test_latest_missing_hour_does_not_present_prior_good_temperature_as_current(weather):
    monitor, profile, clock = weather
    retain(monitor, profile, clock, temperature=41.5)
    clock[0] += timedelta(hours=1)
    retain(monitor, profile, clock, status="insufficient_data")
    text, facts = weather_answer("temperature", monitor, profile["id"])
    assert "last usable" in text.lower() and "41.5°F" in text
    assert "current weather support is unavailable" in text.lower()
    assert facts["temperature_time"] == HOUR.isoformat()
    assert facts["observation_time"] == clock[0].isoformat() and not facts["comparison_available"]
    assert facts["temperature_evidence_id"] != facts["evidence_id"]


def test_native_age_boundary_and_stale_saved_temperature_are_distinct(weather):
    monitor, profile, clock = weather
    retain(monitor, profile, clock)
    clock[0] = HOUR + timedelta(hours=CURRENT_MAX_AGE_HOURS)
    assert weather_answer("freshness", monitor, profile["id"])[1]["comparison_available"]
    clock[0] += timedelta(seconds=1)
    for intent in ("temperature", "freshness"):
        text, facts = weather_answer(intent, monitor, profile["id"])
        assert facts["stale"] and not facts["comparison_available"]
        assert "stale" in text.lower() and "Jan 11, 2026" in text


def test_source_failure_discloses_retained_support_without_claiming_live_data(weather):
    monitor, profile, clock = weather
    retain(monitor, profile, clock)
    clock[0] += timedelta(hours=1)
    monitor._source_failure(profile["id"], clock[0])
    for intent in ("temperature", "freshness"):
        text, facts = weather_answer(intent, monitor, profile["id"])
        assert "source refresh failed" in text.lower() and facts["source_error"]
        assert "Jan 11, 2026" in text


@pytest.mark.parametrize("mode", ["replay", "injected"])
def test_historical_mode_never_claims_saved_readings_are_current(tmp_path, mode):
    clock = [HOUR]
    monitor = Monitor(Store(tmp_path / "replay.sqlite3"), writer=NoWriter(), clock=lambda: clock[0])
    profile = monitor.create_orchard(orchard_id="replay", crops=["peach"], mode=mode)
    retain(monitor, profile, clock, temperature=47.6)
    clock[0] += timedelta(days=200)
    temperature, facts = weather_answer("temperature", monitor, profile["id"])
    freshness, _ = weather_answer("freshness", monitor, profile["id"])
    assert temperature.startswith("Replay only:") and "47.6°F" in temperature
    assert facts["data_mode"] == mode and facts["comparison_available"]
    assert "saved readings" in freshness and "not current weather" in freshness


def test_station_findings_are_suspicions_while_orchard_support_can_remain_usable(weather):
    monitor, profile, clock = weather
    retain(monitor, profile, clock, flags=True)
    text, facts = weather_answer("station_findings", monitor, profile["id"])
    assert "Station 6" in text and "suspected" in text and "cause is unconfirmed" in text
    assert "broken" not in text and facts["comparison_available"]
    normal, _ = weather_answer("temperature_normal", monitor, profile["id"])
    assert "No calibrated normal-temperature range" in normal and "suspected" in normal


def test_no_finding_does_not_establish_a_calibrated_normal_orchard_temperature(weather):
    monitor, profile, clock = weather
    retain(monitor, profile, clock)
    text, facts = weather_answer("temperature_normal", monitor, profile["id"])
    assert "No calibrated normal-temperature range" in text
    assert "No supporting-station discrepancy was flagged" in text and not facts["station_findings"]
    assert "temperature is normal" not in text


def test_source_quality_warning_is_separate_from_statistical_station_discrepancy(weather):
    monitor, profile, clock = weather
    value = packet(profile)
    value["data_health"] = [{"station_id": 226, "reason": "source_warning", "source_quality": "flagged"}]
    monitor.apply(profile["id"], {"latest": value}, now=clock[0])
    text, facts = weather_answer("station_findings", monitor, profile["id"])
    assert "source-quality warnings" in text and "226" in text
    assert "No statistical station discrepancy" in text and not facts["station_findings"]


def test_direct_reply_context_survives_restart_and_reaction_without_fictitious_weather_context(weather, monkeypatch):
    monitor, profile, clock = weather
    retain(monitor, profile, clock)
    service = ConversationService(monitor)
    assert service._context(profile["id"], None)["intent"] == "farm"
    observed = []
    def router(text, context, _known):
        observed.append(context)
        return {"temperature": "temperature", "got it": "ack", "normal": "temperature_normal"}[text]
    monkeypatch.setattr("terra_monitoring.conversation.service.route", router)
    first = service.message(request_id="temperature", text="temperature", orchard_id=profile["id"])
    assert first["assistant_turn"]["data"]["local_facts"]["evidence_id"]
    service.message(request_id="ack", text="got it", orchard_id=profile["id"])
    restarted = ConversationService(Monitor(Store(monitor.store.path), writer=NoWriter(), clock=lambda: clock[0]))
    result = restarted.message(request_id="normal", text="normal", orchard_id=profile["id"])
    assert observed == ["farm", "temperature", "temperature"]
    assert result["assistant_turn"]["data"]["intent"] == "temperature_normal"
    assert "No calibrated normal-temperature range" in result["assistant_turn"]["text"]


@pytest.mark.parametrize("intent", ["temperature", "freshness", "station_findings", "temperature_normal"])
def test_every_direct_weather_intent_is_local_and_retains_its_context(weather, monkeypatch, intent):
    monitor, profile, clock = weather
    retain(monitor, profile, clock)
    monkeypatch.setattr("terra_monitoring.conversation.service.route", lambda *_: intent)
    service = ConversationService(monitor)
    result = service.message(request_id="weather", text="saved weather question", orchard_id=profile["id"])
    assert result["outcome"] == "message" and result["assistant_turn"]["data"]["intent"] == intent
    assert service._context(profile["id"], None)["intent"] == intent
    assert service._context(profile["id"], result["assistant_turn"]["id"])["intent"] == intent
    assert monitor.store.messages(profile["id"]) == []


def test_notification_completion_names_the_missing_worker_when_weather_is_usable(weather):
    monitor, profile, clock = weather
    retain(monitor, profile, clock)
    service = ConversationService(monitor)
    result = service.action(request_id="in-chat", action="notifications", orchard_id=profile["id"],
                            notifications={"enabled": False, "permission": "default"})
    text = result["assistant_turn"]["text"]
    assert "weather data is usable" in text and "monitor isn't running" in text
    assert "waiting for usable weather" not in text and "You're set" not in text
    assert result["state"]["monitoring"]["status"] != "ready"


def test_notification_completion_waits_for_data_when_worker_is_running(weather):
    monitor, profile, clock = weather
    with monitor.store.connect(write=True) as db:
        monitor.store.set_state(db, "worker-runtime", {"heartbeat": clock[0].isoformat(), "running": True, "interval_seconds": 300})
    result = ConversationService(monitor).action(request_id="no-data", action="notifications", orchard_id=profile["id"],
                            notifications={"enabled": False, "permission": "default"})
    assert "waiting for usable weather data" in result["assistant_turn"]["text"]
    assert "and the monitor" not in result["assistant_turn"]["text"]
    assert result["state"]["monitoring"]["status"] != "ready"


def test_notification_completion_only_announces_ready_with_data_and_actual_worker(weather):
    monitor, profile, clock = weather
    retain(monitor, profile, clock)
    with monitor.store.connect(write=True) as db:
        monitor.store.set_state(db, "worker-runtime", {"heartbeat": clock[0].isoformat(), "running": True, "interval_seconds": 300})
    result = ConversationService(monitor).action(request_id="ready-choice", action="notifications", orchard_id=profile["id"],
                            notifications={"enabled": False, "permission": "default"})
    assert result["state"]["monitoring"]["status"] == "ready"
    assert "You're set" in result["assistant_turn"]["text"]


@pytest.mark.parametrize("mode", ["replay", "injected"])
def test_replay_notification_completion_never_promises_live_monitoring(tmp_path, mode):
    monitor = Monitor(Store(tmp_path / "setup-replay.sqlite3"), writer=NoWriter(), clock=lambda: HOUR)
    profile = monitor.create_orchard(orchard_id="replay", crops=["peach"], mode=mode)
    clock = [HOUR]
    retain(monitor, profile, clock)
    result = ConversationService(monitor).action(request_id="replay-choice", action="notifications", orchard_id=profile["id"],
                            notifications={"enabled": False, "permission": "default"})
    assert "saved replay readings aren't live monitoring" in result["assistant_turn"]["text"]
    assert "waiting for usable weather" not in result["assistant_turn"]["text"]
    assert result["state"]["monitoring"]["status"] != "ready"
