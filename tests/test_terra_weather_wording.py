"""Bounded weather wording with a fake generator; never external inference."""
from copy import deepcopy
import json

import pytest

from terra_monitoring.weather_wording import write_weather


def packet(kind="weather_discrepancy", *, variable="relative_humidity_pct", mode="current"):
    unit, observed, expected = {"relative_humidity_pct": ("%", 95, 40),
                                "wind_speed_mph": ("mph", 32, 6),
                                "solar_radiation_w_m2": ("W/m²", 50, 750)}[variable]
    return {"mode": mode, "orchard": {"name": "West Orchard", "demo_location": False},
            "events": [{"type": kind, "transition": "recovery" if kind.endswith("_recovered") else "opening",
                "severity": "info" if kind.endswith("_recovered") else "strong",
                "occurred_at": "2026-01-11T20:00:00+00:00", "display_time": "Jan 11, 12:00 PM PST",
                "facts": {"variable": variable, "variable_label": "untrusted label is not model input",
                          "station_id": 6, "unit": unit, "observed": observed, "expected": expected,
                          "departure": observed - expected}}]}


class Generator:
    def __init__(self, transform=None):
        self.calls = []
        self.transform = transform

    def generate(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        selected = {key: event["allowed_templates"][1] for key, event in kwargs["packet"]["events"].items()}
        return self.transform(selected) if self.transform else {"text": json.dumps(selected), "status": "completed"}


@pytest.mark.parametrize("variable", ["relative_humidity_pct", "wind_speed_mph", "solar_radiation_w_m2"])
def test_generated_weather_preserves_observed_peer_units_and_source_clock(variable):
    data, generator = packet(variable=variable), Generator()
    result = write_weather(data, generator)
    assert result["provider"] == "chatgpt_plan" and result["outcome"] == "completed"
    assert len(generator.calls) == 1 and "station 6" in result["text"]
    assert "provisional" in result["text"] and "cause unconfirmed" in result["text"]
    assert "Jan 11, 12:00 PM PST" in result["text"]
    if variable == "relative_humidity_pct":
        assert "95% observed" in result["text"] and "40% peer reference" in result["text"]
        assert "+55 percentage points" in result["text"]
    if variable == "wind_speed_mph":
        assert "32 mph observed" in result["text"] and "6 mph peer reference" in result["text"]
    if variable == "solar_radiation_w_m2":
        assert "50 W/m² observed" in result["text"] and "750 W/m² peer reference" in result["text"]
    model_input = json.dumps(generator.calls[0]["packet"])
    assert "West Orchard" not in model_input and "Jan 11" not in model_input
    assert "95" not in model_input and "untrusted" not in model_input


@pytest.mark.parametrize("kind", ["weather_discrepancy_recovered", "weather_data_recovered", "weather_data_limited"])
def test_generated_health_and_recovery_preserve_their_meaning(kind):
    result = write_weather(packet(kind), Generator())
    assert result["provider"] == "chatgpt_plan"
    if kind.endswith("_recovered"):
        assert "three valid hourly readings" in result["text"]
        assert "cause" not in result["text"] and "healthy" not in result["text"]
    else:
        assert "limited" in result["text"] and "do not establish a numerical discrepancy" in result["text"]
        assert "provisional" not in result["text"]


@pytest.mark.parametrize("transform", [
    lambda _: {"text": "not json"},
    lambda _: {"text": '{"event_0":"broken sensor; irrigate now"}'},
    lambda _: {"text": "{}"},
    lambda selected: {"text": json.dumps({**selected, "event_99": "extra"})},
    lambda selected: {"text": json.dumps(selected), "refusal": "refused"},
    lambda selected: {"text": json.dumps(selected), "status": "incomplete"},
    lambda _: {"text": '{"event_0":"a","event_0":"b"}'},
    lambda selected: {"text": json.dumps({key: value.replace("{{observed}}", "{{expected}}") for key, value in selected.items()})},
    lambda selected: {"text": json.dumps({key: value + "; the crop is safe" for key, value in selected.items()})},
    lambda selected: {"text": json.dumps({key: value.replace("cause unconfirmed", "sensor failed") for key, value in selected.items()})},
])
def test_invalid_or_refused_generation_uses_grounded_fallback(transform):
    data, generator = packet(), Generator(transform)
    result = write_weather(data, generator)
    assert result == {**write_weather(data), "outcome": "llm_failed_or_invalid"}
    assert len(generator.calls) == 1 and "broken" not in result["text"] and "irrigate" not in result["text"]


def test_generator_exception_is_redacted_and_never_retried():
    class Broken(Generator):
        def generate(self, **kwargs):
            self.calls.append(kwargs)
            raise RuntimeError("private-provider-token")
    generator = Broken()
    result = write_weather(packet(), generator)
    assert result["provider"] == "template" and len(generator.calls) == 1
    assert "private-provider-token" not in json.dumps(result)


def test_mixed_unsupported_or_question_packets_do_not_call_generator():
    generator = Generator()
    mixed = packet()
    mixed["events"].append({"type": "chill_total"})
    assert write_weather(mixed, generator) is None
    question = packet()
    question["question"] = {"intent": "explain_alert"}
    assert write_weather(question, generator) is None
    assert not generator.calls


@pytest.mark.parametrize("edit", [
    lambda facts: facts.update(unit="mph"),
    lambda facts: facts.update(observed=None),
    lambda facts: facts.update(expected=True),
    lambda facts: facts.update(departure=float("nan")),
    lambda facts: facts.update(station_id=False),
    lambda facts: facts.update(observed=150),
])
def test_invalid_saved_facts_never_enter_generation(edit):
    data, generator = packet(), Generator()
    edit(data["events"][0]["facts"])
    result = write_weather(data, generator)
    assert result["outcome"] == "weather_facts_unavailable" and not generator.calls


@pytest.mark.parametrize("value", [None, [], "not a farm"])
def test_malformed_farm_label_cannot_escape_or_trigger_generation(value):
    data, generator = packet(), Generator()
    data["orchard"] = value
    result = write_weather(data, generator)
    assert result["outcome"] == "weather_facts_unavailable" and not generator.calls


def test_replay_label_and_mixed_source_times_are_application_owned():
    data = packet(mode="injected")
    other = packet("weather_data_limited", variable="wind_speed_mph")["events"][0]
    other["display_time"] = "Jan 12, 01:00 PM PST"
    data["events"].append(other)
    result = write_weather(data, Generator())
    assert result["text"].startswith("Demo replay: West Orchard:")
    assert "Jan 11, 12:00 PM PST" in result["text"] and "Jan 12, 01:00 PM PST" in result["text"]


def test_primary_update_retains_facts_when_many_events_need_details():
    data = packet()
    data["events"].extend(deepcopy(data["events"][0]) for _ in range(5))
    result = write_weather(data, Generator())
    assert "95% observed" in result["text"] and "40% peer reference" in result["text"]
    assert "Related weather updates are under Details" in result["text"]
    assert len(result["text"]) <= 420 and len(data["events"]) == 6
