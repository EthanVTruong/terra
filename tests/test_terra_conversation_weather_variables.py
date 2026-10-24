"""Grounded multivariable replies; synthetic station evidence, no provider or LLM."""
from datetime import timedelta
from pathlib import Path
import re

import pytest

from terra_monitoring.conversation import ConversationService
from terra_monitoring.conversation.local_facts import weather_answer
from terra_monitoring.conversation.routing import PROMPT, route
from terra_monitoring.farm_ops.service import Monitor
from terra_monitoring.farm_ops.store import Store
from test_farm_chill_ops import HOUR, packet


class NoWriter:
    def write(self, _):
        raise AssertionError("Saved weather questions must remain local")


@pytest.fixture
def weather(tmp_path):
    clock = [HOUR]
    monitor = Monitor(Store(tmp_path / "variables.sqlite3"), writer=NoWriter(), clock=lambda: clock[0])
    profile = monitor.create_orchard("Peach farm", orchard_id="peach", crops=["peach"],
                                     lat=38.55, lon=-121.8, elevation_ft=65)
    return monitor, profile, clock


def retain(weather, *, values=(74.5, 7.2, 0), statuses=("assessable", "assessable", "low_daylight"),
           findings=(), health=()):
    monitor, profile, clock = weather
    value = packet(profile, clock[0])
    keys = ("relative_humidity_pct", "wind_speed_mph", "solar_radiation_w_m2")
    value["weather"] = {"schema_version": "terra-weather-assessment-v1", "policy_version": "terra-weather-policy-v1",
                        "valid_time_utc": clock[0].isoformat(), "as_of_utc": clock[0].isoformat(),
                        "variables": [{"variable": key, "stations": [{"station_id": 6, "observed": observed,
                            "expected": observed, "departure": 0 if observed is not None else None,
                            "peer_count": 4, "status": state, "reason": "low_daylight" if state == "low_daylight" else
                                "source_warning" if state == "unavailable" else "available",
                            "classification": "no_alert" if state == "assessable" else None}]}
                            for key, observed, state in zip(keys, values, statuses, strict=True)],
                        "findings": list(findings), "data_health": list(health)}
    monitor.apply(profile["id"], {"latest": value}, now=clock[0])
    return value


@pytest.mark.parametrize(("text", "intent"), [
    ("What is the humidity at my farm?", "humidity"),
    ("Is relative humidity normal?", "humidity"),
    ("How is wind speed today?", "wind"),
    ("What is solar radiation?", "solar_radiation"),
    ("Show humidity, wind and solar radiation", "weather_variables"),
    ("What are the weather variables?", "weather_variables"),
    ("Compare humidity with last year", "weather_history_unavailable"),
    ("Predict humidity tomorrow", "unsupported"),
    ("humidity password", "clarify"),
])
def test_bounded_variable_routes(text, intent):
    assert route(text, "farm") == intent


@pytest.mark.parametrize(("intent", "expected"), [("humidity", "74.5%"), ("wind", "7.2 mph"), ("solar_radiation", "0 W/m²")])
def test_station_values_keep_units_and_source_times(weather, intent, expected):
    retain(weather)
    monitor, profile, _ = weather
    text, facts = weather_answer(intent, monitor, profile["id"])
    assert expected in text and "station 6" in text and "Jan 11, 2026" in text and "PST" in text
    assert "orchard humidity" not in text.lower() and "orchard wind" not in text.lower()
    assert facts["weather_schema_version"] == "terra-weather-assessment-v1"
    assert not facts["current_conditions_verified"]
    if intent == "solar_radiation":
        assert "low daylight" in text and "unavailable" not in text


def test_old_temperature_only_packets_withhold_variable_conclusions(weather):
    monitor, profile, clock = weather
    monitor.apply(profile["id"], {"latest": packet(profile, clock[0])}, now=clock[0])
    for intent in ("humidity", "wind", "solar_radiation", "weather_variables"):
        text, facts = weather_answer(intent, monitor, profile["id"])
        assert "aren't available" in text and not facts["weather_variables"]
        assert "normal" not in text and "0%" not in text


def test_quality_and_missing_never_become_zero_or_discrepancies(weather):
    health = [{"variable": "relative_humidity_pct", "station_id": 6, "valid_utc": HOUR.isoformat(), "reason": "source_warning"}]
    retain(weather, values=(99, None, 0), statuses=("unavailable", "unavailable", "low_daylight"), health=health)
    monitor, profile, _ = weather
    text, facts = weather_answer("humidity", monitor, profile["id"])
    assert "unavailable" in text and "source-quality warning" in text
    assert "99%" not in text and "suspected" not in text
    assert facts["weather_data_health"] and not facts["weather_findings"]
    wind, _ = weather_answer("wind", monitor, profile["id"])
    assert "unavailable" in wind and "0 mph" not in wind


def test_variable_discrepancy_is_provisional_and_visible_in_station_question(weather):
    finding = {"id": "synthetic-rh", "variable": "relative_humidity_pct", "station_id": 6,
               "classification": "suspected_discrepancy", "severity": "strong", "observed": 95,
               "expected": 40, "departure": 55, "unit": "%", "departure_unit": "percentage points", "detected_at_utc": HOUR.isoformat()}
    value = retain(weather, values=(95, 7.2, 0), findings=[finding])
    value["weather"]["variables"][0]["stations"][0]["classification"] = "suspected_discrepancy"
    value["weather"]["variables"][0]["stations"][0].update(expected=40, departure=55)
    monitor, profile, clock = weather
    monitor.apply(profile["id"], {"latest": value}, now=clock[0])
    text, facts = weather_answer("humidity", monitor, profile["id"])
    assert "95%" in text and "provisional" in text and "cause is unconfirmed" in text
    assert "peer reference 40%" in text and "departure +55 percentage points" in text
    assert "broken" not in text and "crop" not in text
    assert facts["weather_findings"][0]["expected"] == 40
    assert facts["weather_findings"][0]["departure_unit"] == "percentage points"
    general, _ = weather_answer("station_findings", monitor, profile["id"])
    assert "relative humidity" in general and "No supporting-station discrepancy" not in general


def test_source_failure_and_stale_readings_cannot_become_current(weather):
    retain(weather)
    monitor, profile, clock = weather
    clock[0] += timedelta(hours=7)
    monitor._source_failure(profile["id"], clock[0])
    text, facts = weather_answer("humidity", monitor, profile["id"])
    assert "74.5%" in text and "source refresh failed" in text and "don't verify current conditions" in text
    assert not facts["current_conditions_verified"]


def test_replay_variable_readings_stay_labelled(tmp_path):
    clock = [HOUR]
    monitor = Monitor(Store(tmp_path / "replay.sqlite3"), writer=NoWriter(), clock=lambda: clock[0])
    profile = monitor.create_orchard(orchard_id="replay", crops=["peach"], mode="injected")
    retain((monitor, profile, clock))
    text, facts = weather_answer("wind", monitor, profile["id"])
    assert text.startswith("Replay only:") and facts["data_mode"] == "injected"
    assert not facts["current_conditions_verified"]


def test_variable_conversation_reference_survives_new_measurements_and_restart(weather):
    retain(weather)
    monitor, profile, clock = weather
    service = ConversationService(monitor)
    first = service.message(request_id="humidity", text="What is humidity?", orchard_id=profile["id"])
    assert first["assistant_turn"]["data"]["intent"] == "humidity"
    original = first["assistant_turn"]["data"]["local_facts"]["evidence_id"]
    clock[0] += timedelta(hours=1)
    retain(weather, values=(55, 4.5, 20))
    restarted = ConversationService(Monitor(Store(monitor.store.path), writer=NoWriter(), clock=lambda: clock[0]))
    answer = restarted.message(request_id="reference", text="Is that normal?", orchard_id=profile["id"],
                               reply_to=first["assistant_turn"]["id"])
    assert "74.5%" in answer["assistant_turn"]["text"] and "55%" not in answer["assistant_turn"]["text"]
    facts = answer["assistant_turn"]["data"]["local_facts"]
    assert facts["referenced_snapshot"] and facts["evidence_id"] == original
    assert "is normal" not in answer["assistant_turn"]["text"]


def test_visible_brand_is_terra_while_saved_api_identity_remains_compatible():
    assert "I'm Terra" in PROMPT
    root = Path(__file__).resolve().parents[1]
    source = (root / "web/src/TaraConversation.jsx").read_text(encoding="utf-8")
    assert "Message Terra" in source and "Conversation with Terra" in source
    assert not re.search(r"\b(?:Tera|Tara)\b", source)
    assert "/api/tara/conversation" in source
