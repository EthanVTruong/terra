"""The proactive adapter retains monitor authority around one engine call."""
from copy import deepcopy
import json

import pytest

from terra_monitoring.farm_ops import insight_writer
from terra_monitoring.farm_ops.insight_writer import InsightWriter, insight_context
from terra_monitoring.farm_ops.writer import Writer, message_input


NOW = "2026-10-24T20:00:00+00:00"


def packet(*, kind="monitoring_limited", mode="current", severity="moderate", facts=None, stamp=NOW):
    profile = {"name": "Private farm name", "crop": "almond", "variety": None,
               "demo_location": False, "mode": mode}
    event = {"id": "source-one", "type": kind, "transition": "recovery" if kind.endswith("recovered") else "opening",
             "severity": severity, "occurred_at": stamp,
             "facts": facts if facts is not None else {"reason": "insufficient_usable_neighbors"}}
    return message_input(profile, [event], NOW)


def completed(text, *, ids=None, outcome="message"):
    return {"outcome": outcome, "text": text, "fact_ids": ["event_0"] if ids is None else ids,
            "focus": "event_0", "provider": "chatgpt_plan", "generation_outcome": "completed"}


class Generator:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    def generate(self, **request):
        self.calls.append(request)
        if self.error:
            raise self.error
        return self.result


@pytest.fixture(autouse=True)
def engine_boundary(monkeypatch):
    # engine.py owns JSON/numeric validation in its independent suite. This spy
    # isolates the adapter's event admission, chronology and single-call rules.
    monkeypatch.setattr(insight_writer, "_respond",
                        lambda generator, context: generator.generate(instructions="engine", packet=context))


def writer(generator):
    return InsightWriter(Writer(key="private-api-key-not-used", model="private-model-not-used",
                                generator=generator, voice="text"))


def test_free_natural_explanation_uses_one_selected_call_and_retains_original_packet():
    text = "Patchy weather readings leave gaps in the orchard estimate; those missing hours remain unknown."
    generator = Generator(completed(text))
    original = packet()
    before = deepcopy(original)
    result = writer(generator).write(original)
    assert result == {"text": text, "provider": "chatgpt_plan", "outcome": "completed"}
    assert original == before
    assert len(generator.calls) == 1
    context = generator.calls[0]["packet"]
    assert context["capabilities"]["communication"] == "proactive_alert"
    assert context["capabilities"]["allow_clarification"] is False
    assert context["history"] == []
    assert "Private farm name" not in str(context)
    assert "crop danger" in context["facts"][0]["text"]


def test_disconnected_writer_renders_locally_without_import_or_provider_call(monkeypatch):
    monkeypatch.setattr(insight_writer, "_respond", lambda *a: pytest.fail("disconnected generation"))
    result = InsightWriter(Writer(key="", model="", voice="text")).write(packet())
    assert result["provider"] == "template"
    assert result["outcome"] == "chatgpt_not_connected"
    assert "incomplete" in result["text"]


@pytest.mark.parametrize("response", [
    completed("The estimate is incomplete; weather readings are missing.", ids=[]),
    completed("Weather readings are missing, so the estimate is incomplete.", ids=["another-farm"]),
    completed("Which reading do you mean?", outcome="clarify"),
    completed(None, outcome="none"),
    {**completed("Weather data is patchy; the estimate is incomplete."), "provider": "template"},
    {**completed("Weather data is patchy; the estimate is incomplete."), "generation_outcome": "incomplete"},
])
def test_unusable_engine_result_falls_back_without_second_call(response):
    generator = Generator(response)
    result = writer(generator).write(packet())
    assert result["provider"] == "template"
    assert result["outcome"] == "insight_failed_or_invalid"
    assert len(generator.calls) == 1
    assert "incomplete" in result["text"]


def test_provider_exception_never_leaks_or_retries():
    generator = Generator(error=RuntimeError("PRIVATE access token"))
    result = writer(generator).write(packet())
    assert len(generator.calls) == 1
    assert result["provider"] == "template"
    assert "PRIVATE" not in str(result)


@pytest.mark.parametrize("kind,phrase,severity", [
    ("monitoring_limited", "Weather coverage is complete and reliable.", "moderate"),
    ("monitoring_limited", "Weather data is spotty, so the estimate is incomplete.", "strong"),
    ("monitoring_limited", "Weather data is unavailable, so we cannot update the orchard estimate.", "moderate"),
    ("monitoring_recovered", "We are still missing weather data and cannot update the estimate.", "info"),
])
def test_reversed_or_weakened_coverage_state_uses_local_facts(kind, phrase, severity):
    generator = Generator(completed(phrase))
    result = writer(generator).write(packet(kind=kind, severity=severity))
    assert result["provider"] == "template"
    assert len(generator.calls) == 1


def test_recovery_explains_the_restored_estimate_without_a_new_promise():
    text = "Weather support has recovered, so the orchard estimate can update again."
    generator = Generator(completed(text))
    result = writer(generator).write(packet(kind="monitoring_recovered", severity="info"))
    assert result["provider"] == "chatgpt_plan"
    assert result["text"] == text


def test_missing_wind_comparison_does_not_invalidate_orchard_temperature():
    source = packet(kind="weather_data_limited", facts={"variable_label": "Wind speed"})
    generator = Generator(completed("Missing wind readings mean the orchard estimate is incomplete."))
    result = writer(generator).write(source)
    assert result["provider"] == "template"
    assert len(generator.calls) == 1


@pytest.mark.parametrize("phrase,accepted", [
    ("Your local sensor shows a suspected discrepancy; the cause is unconfirmed.", True),
    ("Your local sensor is broken.", False),
    ("Your local sensor differs from the station estimate.", False),
])
def test_sensor_flags_keep_cause_uncertainty(phrase, accepted):
    generator = Generator(completed(phrase))
    source = packet(kind="sensor_flag", facts={"observed_f": 80.0, "expected_f": 47.0, "deviation_f": 33.0})
    result = writer(generator).write(source)
    assert (result["provider"] == "chatgpt_plan") is accepted
    assert len(generator.calls) == 1


def test_mixed_event_packet_requires_every_selected_event_and_exposes_no_raw_arrays():
    source = packet()
    recovery = packet(kind="weather_data_recovered", severity="info",
                      facts={"variable_label": "Wind speed", "variable": "wind_speed_mph"})["events"][0]
    source["events"].append(recovery)
    generator = Generator(completed("Weather data is patchy, so the estimate is incomplete.", ids=["event_0"]))
    result = writer(generator).write(source)
    assert result["provider"] == "template"
    context = generator.calls[0]["packet"]
    assert [f["id"] for f in context["facts"]] == ["event_0", "event_1"]
    assert "another variable" in context["facts"][1]["text"] or "comparison recovered" in context["facts"][1]["text"]
    assert "stations" not in context and "peer_arrays" not in context


@pytest.mark.parametrize("mode,stamp", [("replay", "2026-03-04T15:00:00+00:00"),
                                        ("current", "2026-10-23T15:00:00+00:00")])
def test_saved_or_stale_source_clock_is_added_locally(mode, stamp):
    generator = Generator(completed("Weather data is patchy, so the orchard estimate is incomplete."))
    source = packet(mode=mode, stamp=stamp)
    result = writer(generator).write(source)
    assert result["provider"] == "chatgpt_plan"
    assert "Saved evidence:" in result["text"]
    assert "2026 15:00 UTC" in result["text"]
    assert result["text"].startswith("Demo replay: ") is (mode == "replay")


def test_replay_fallback_is_labeled_too():
    generator = Generator(error=RuntimeError("not available"))
    result = writer(generator).write(packet(mode="replay"))
    assert result["provider"] == "template"
    assert result["text"].startswith("Demo replay: ")


def test_questions_keep_the_explicit_existing_writer_contract():
    class Existing:
        generator = object()
        voice = "text"

        def write(self, source):
            assert source["question"]["intent"] == "station_health"
            return {"text": "Existing answer", "provider": "template", "outcome": "local_facts"}

    source = packet()
    source["question"] = {"intent": "station_health", "text": "Is the data usable?"}
    assert InsightWriter(Existing()).write(source)["text"] == "Existing answer"


def test_unknown_future_event_is_not_sent_to_model():
    generator = Generator(completed("An update."))
    result = writer(generator).write(packet(kind="unrecognized_future_event"))
    assert result["provider"] == "template"
    assert generator.calls == []


def test_final_alert_bound_includes_locally_added_source_time():
    text = "Weather data is patchy and the estimate is incomplete. " + "A" * 350
    assert len(text) <= 420
    generator = Generator(completed(text))
    result = writer(generator).write(packet(mode="replay", stamp="2026-03-04T15:00:00+00:00"))
    assert result["provider"] == "template"


def test_context_covers_every_supported_event_family():
    cases = [
        ("welcome", {"hours": None, "comparison_available": False}),
        ("chill_total", {"hours": 120.0, "complete": True}),
        ("history", {"hours": 120.0, "complete": True, "historical_median_hours": 150.0}),
        ("chill_pace", {"hours": 120.0}),
        ("chill_pace_recovered", {}),
        ("chill_reference", {"reference_hours": 100.0}),
        ("station_health", {"comparison_available": False}),
        ("explain_alert", {"explanation": "weather coverage is incomplete"}),
        ("weather_discrepancy", {"station_id": 12, "variable_label": "Wind speed", "observed": 20.0, "expected": 2.0, "unit": "mph"}),
        ("weather_discrepancy_recovered", {"station_id": 12, "variable_label": "Wind speed"}),
        ("weather_data_limited", {"variable_label": "Wind speed"}),
        ("weather_data_recovered", {"variable_label": "Wind speed"}),
    ]
    for kind, facts in cases:
        context = insight_context(packet(kind=kind, facts=facts))
        assert context["facts"][0]["kind"] == kind
        assert context["facts"][0]["text"]
    welcome = insight_context(packet(kind="welcome", facts={"hours": None, "comparison_available": False}))
    assert "your farm is saved" in welcome["facts"][0]["text"].lower()
    assert "monitoring is connected" not in welcome["facts"][0]["text"].lower()


@pytest.mark.parametrize("text,accepted", [
    ("Patchy weather readings leave the orchard estimate incomplete.", True),
    ("The orchard estimate is 999 degrees.", False),
])
def test_actual_contextual_engine_protocol_requires_completed_grounded_alert(monkeypatch, text, accepted):
    engine = pytest.importorskip("terra_monitoring.conversation.engine")
    monkeypatch.setattr(insight_writer, "_respond", engine.respond)

    class Model:
        def __init__(self):
            self.calls = []

        def generate(self, **request):
            self.calls.append(request)
            return {"status": "completed", "text": json.dumps({
                "outcome": "message", "text": text,
                "fact_ids": [fact["id"] for fact in request["packet"]["facts"]], "focus": "alert"})}

    model = Model()
    result = writer(model).write(packet())
    assert (result["provider"] == "chatgpt_plan") is accepted
    assert len(model.calls) == 1
    assert model.calls[0]["schema"]["additionalProperties"] is False
