"""Saved weather replies preserve native availability and never use inference."""
from copy import deepcopy
from datetime import timedelta
import json

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


def test_explicit_temperature_reference_uses_immutable_assessment_after_same_hour_revision(weather):
    monitor, profile, clock = weather
    retain(monitor, profile, clock, temperature=40, flags=True)
    service = ConversationService(monitor)
    original = service.message(request_id="original", text="What is the temperature?", orchard_id=profile["id"])["assistant_turn"]
    old_evidence = original["data"]["local_facts"]["evidence_id"]
    retain(monitor, profile, clock, temperature=55)
    assert monitor.store.hours(profile["id"])[-1]["temperature_f"] == 55
    reply = service.message(request_id="old-temperature", text="What is the temperature?", orchard_id=profile["id"], reply_to=original["id"])["assistant_turn"]
    assert "40°F" in reply["text"] and "55°F" not in reply["text"]
    assert "Saved assessment" in reply["text"] and "was" in reply["text"]
    assert reply["data"]["local_facts"]["evidence_id"] == old_evidence
    assert not reply["data"]["local_facts"]["current_conditions_verified"]
    current = service.message(request_id="current-temperature", text="What is the temperature now?", orchard_id=profile["id"], reply_to=original["id"])["assistant_turn"]
    assert "55°F" in current["text"] and not current["data"]["local_facts"]["referenced_snapshot"]
    assert next(turn for turn in service._turns(profile["id"]) if turn["id"] == original["id"]) == original


def test_elliptical_normal_followup_keeps_old_finding_after_controls_advance(weather):
    monitor, profile, clock = weather
    retain(monitor, profile, clock, temperature=40, flags=True)
    service = ConversationService(monitor)
    original = service.message(request_id="first", text="What is the temperature?", orchard_id=profile["id"])["assistant_turn"]
    clock[0] += timedelta(hours=1)
    retain(monitor, profile, clock, temperature=55)
    reply = service.message(request_id="normal-old", text="Is that normal?", orchard_id=profile["id"])["assistant_turn"]
    assert reply["data"]["intent"] == "temperature_normal"
    assert "Station 6" in reply["text"] and "suspected" in reply["text"]
    assert reply["data"]["local_facts"]["observation_time"] == HOUR.isoformat()
    assert reply["data"]["local_facts"]["evidence_id"] == original["data"]["local_facts"]["evidence_id"]
    again = service.message(request_id="normal-chain", text="What does that mean?", orchard_id=profile["id"])["assistant_turn"]
    assert again["data"]["local_facts"]["evidence_id"] == reply["data"]["local_facts"]["evidence_id"]


def test_reference_keeps_revised_qc_evidence_when_hour_value_was_deduplicated(weather):
    monitor, profile, clock = weather
    retain(monitor, profile, clock, temperature=40)
    retain(monitor, profile, clock, temperature=40, flags=True)
    service = ConversationService(monitor)
    original = service.message(request_id="same-value", text="What is the temperature?", orchard_id=profile["id"])["assistant_turn"]
    facts = original["data"]["local_facts"]
    assert facts["temperature_evidence_id"] == facts["evidence_id"]
    assert facts["evidence_id"] != monitor.store.hours(profile["id"])[0]["evidence_id"]
    clock[0] += timedelta(hours=1)
    retain(monitor, profile, clock, temperature=55)
    reply = service.message(request_id="revised-qc", text="Is that normal?", orchard_id=profile["id"])["assistant_turn"]
    assert "Station 6" in reply["text"] and reply["data"]["local_facts"]["evidence_id"] == facts["evidence_id"]


def test_field_note_station_question_inspects_note_evidence_not_latest_weather(weather):
    from terra_monitoring.farm_ops.writer import Writer
    monitor, profile, clock = weather
    retain(monitor, profile, clock, flags=True)
    monitor.writer = Writer(key="", model="")
    monitor.flush(profile["id"])
    old_note = monitor.store.messages(profile["id"])[0]
    monitor.writer = NoWriter()
    clock[0] += timedelta(hours=1)
    retain(monitor, profile, clock, temperature=55)
    reply = ConversationService(monitor).message(request_id="old-station", text="Which station is acting up?", orchard_id=profile["id"], reply_to=old_note["id"])["assistant_turn"]
    assert "Station 6" in reply["text"] and "cause is unconfirmed" in reply["text"]
    assert reply["data"]["local_facts"]["observation_time"] == HOUR.isoformat()
    assert reply["data"]["local_facts"]["referenced_snapshot"]


def test_reference_without_assessment_does_not_borrow_new_weather(weather):
    from terra_monitoring.farm_ops.writer import Writer
    monitor, profile, clock = weather
    monitor._source_failure(profile["id"], clock[0])
    monitor.writer = Writer(key="", model="")
    monitor.flush(profile["id"])
    note = monitor.store.messages(profile["id"])[0]
    monitor.writer = NoWriter()
    clock[0] += timedelta(hours=1)
    retain(monitor, profile, clock, temperature=55)
    reply = ConversationService(monitor).message(request_id="no-evidence", text="What is the temperature?", orchard_id=profile["id"], reply_to=note["id"])["assistant_turn"]
    assert "doesn't have a saved temperature assessment" in reply["text"] and "55" not in reply["text"]
    assert reply["data"]["local_facts"]["evidence_id"] is None


def test_old_freshness_reply_uses_original_reading_and_check_clock(weather):
    monitor, profile, clock = weather
    retain(monitor, profile, clock)
    service = ConversationService(monitor)
    original = service.message(request_id="first-temperature", text="What is the temperature?", orchard_id=profile["id"])["assistant_turn"]
    clock[0] += timedelta(hours=8)
    retain(monitor, profile, clock, temperature=60)
    reply = service.message(request_id="old-freshness", text="Are those readings old?", orchard_id=profile["id"], reply_to=original["id"])["assistant_turn"]
    facts = reply["data"]["local_facts"]
    assert facts["observation_time"] == HOUR.isoformat() and facts["last_checked"] == HOUR.isoformat()
    assert "doesn't verify current conditions" in reply["text"] and "current age limit" not in reply["text"]


@pytest.mark.parametrize("change", [{"data_mode": "replay"}, {"query_lat": 39.0}])
def test_bad_reference_mode_or_geometry_is_rejected_before_reserving_request(weather, change):
    monitor, profile, clock = weather
    source = retain(monitor, profile, clock)
    service = ConversationService(monitor)
    original = service.message(request_id="good-reference", text="What is the temperature?", orchard_id=profile["id"])["assistant_turn"]
    bad = deepcopy(source)
    if "query_lat" in change:
        bad["query"]["lat"] = change["query_lat"]
    else:
        bad.update(change)
    with monitor.store.connect(write=True) as db:
        wrong = monitor.store.retain(db, bad)
        original["data"]["local_facts"]["evidence_id"] = wrong
        original["data"]["local_facts"]["temperature_evidence_id"] = wrong
        db.execute("UPDATE tara_turns SET payload=? WHERE id=?", (json.dumps(original), original["id"]))
    with pytest.raises(ValueError):
        service.message(request_id="bad-reference", text="What is the temperature?", orchard_id=profile["id"], reply_to=original["id"])
    assert not service.state(profile["id"])["pending_requests"]
    assert not any(turn["id"] == "bad-reference:user" for turn in service._turns(profile["id"]))


def test_unowned_reference_and_cross_farm_turn_are_rejected(weather):
    monitor, profile, clock = weather
    service = ConversationService(monitor)
    other = monitor.create_orchard(orchard_id="other", crops=["peach"], lat=39.0, lon=-121.8, elevation_ft=65)
    retain(monitor, other, clock, temperature=70)
    evidence = monitor.store.get("watch:other")["latest_evidence_id"]
    with pytest.raises(ValueError, match="does not belong"):
        weather_answer("temperature", monitor, profile["id"], evidence_id=evidence, referenced=True)
    other_turn = service.message(request_id="other-temperature", text="What is the temperature?", orchard_id="other")["assistant_turn"]
    with pytest.raises(ValueError, match="another farm"):
        service.message(request_id="cross-farm-weather", text="What is the temperature?", orchard_id=profile["id"], reply_to=other_turn["id"])


def test_weather_history_limitation_is_local_and_preserves_its_subject(weather, monkeypatch):
    monitor, profile, _ = weather
    monkeypatch.setattr("terra_monitoring.conversation.service.route", lambda *_: "weather_history_unavailable")
    service = ConversationService(monitor)
    reply = service.message(request_id="weather-history", text="Compare temperature with last year", orchard_id=profile["id"])["assistant_turn"]
    assert "Historical weather comparisons aren't available" in reply["text"]
    assert service._context(profile["id"], None)["intent"] == "weather_history_unavailable"


def test_readiness_reason_matches_usable_data_without_a_worker(weather):
    monitor, profile, clock = weather
    retain(monitor, profile, clock)
    state = ConversationService(monitor).state(profile["id"])["monitoring"]
    assert state["status"] == "pending" and state["details"]["comparison_available"]
    assert "weather data is usable" in state["reason"] and "monitor isn't running" in state["reason"]


@pytest.mark.parametrize("mode", ["replay", "injected"])
def test_replay_readiness_stays_explicit_even_if_another_farm_has_a_fresh_worker(tmp_path, mode):
    monitor = Monitor(Store(tmp_path / "replay-state.sqlite3"), writer=NoWriter(), clock=lambda: HOUR)
    profile = monitor.create_orchard(orchard_id="replay", crops=["peach"], mode=mode)
    retain(monitor, profile, [HOUR])
    with monitor.store.connect(write=True) as db:
        monitor.store.set_state(db, "worker-runtime", {"heartbeat": HOUR.isoformat(), "running": True, "interval_seconds": 300})
    state = ConversationService(monitor).state(profile["id"])["monitoring"]
    assert state["status"] == "pending" and state["details"]["comparison_available"]
    assert "replay readings aren't live monitoring" in state["reason"]
