"""Real selected-plan/service bridge with fake generation, never external inference."""
from copy import deepcopy
from datetime import timedelta
import json

import pytest

from terra_monitoring.chatgpt.provider import PlanError
from terra_monitoring.conversation import ConversationService
from terra_monitoring.conversation.factual_writer import write_factual
from terra_monitoring.conversation.local_facts import profile_answer, weather_answer
from terra_monitoring.farm_ops.conversational_writer import ConversationalWriter
from terra_monitoring.farm_ops.service import Monitor
from terra_monitoring.farm_ops.store import Store
from terra_monitoring.farm_ops.writer import Writer
from test_farm_chill_ops import HOUR, packet
from test_terra_conversation_weather_variables import retain


def prose(event):
    kind = event["kind"]
    if kind == "profile_crops":
        return "you have {{crops}} saved" + ("; saved variety is {{variety}}" if "{{variety}}" in event["placeholders"] else "")
    if kind == "profile_location":
        return "your saved farm location is {{location}}"
    if kind == "profile_notifications_on":
        return "browser alerts are enabled in your saved preferences"
    if kind == "profile_notifications_off":
        return "browser alerts remain disabled in your saved preferences"
    if kind == "profile_monitor":
        return "monitoring remains " + ("active" if event["status"] == "ready" else "pending")
    if kind == "chill_method":
        return "estimated chill uses hours in {{range}} range; season is {{season}}, using {{zone}}"
    if kind == "temperature":
        return ("estimated orchard temperature stands at {{temperature}}" if event["current_verified"] else
                "last usable estimated orchard temperature was {{temperature}}")
    if kind == "freshness":
        return "the saved weather readings come from {{source_time}}"
    if kind == "station_reading":
        return "at station {{station}}, {{variable}} was {{observed}}"
    if kind == "weather_discrepancy":
        return "station {{station}} {{variable}} reads {{observed}}, {{expected}} peer reference; departure {{departure}}, provisional discrepancy, cause unconfirmed"
    if kind == "station_flag":
        return ("a provisional " if event["provisional"] else "a ") + "suspected {{variable}} discrepancy was flagged at {{stations}}; cause is unconfirmed"
    if kind == "station_clear":
        return "the supporting station comparison flagged no discrepancy"
    raise AssertionError(kind)


class Generator:
    def __init__(self, transform=None):
        self.calls, self.transform = [], transform

    def generate(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        result = {key: prose(event) for key, event in kwargs["packet"]["events"].items()}
        return self.transform(result) if self.transform else {"status": "completed", "text": json.dumps(result)}


@pytest.fixture
def factual(tmp_path):
    clock, generator = [HOUR], Generator()
    monitor = Monitor(Store(tmp_path / "facts.sqlite3"), writer=ConversationalWriter(Writer(generator=generator)), clock=lambda: clock[0])
    profile = monitor.create_orchard("PRIVATE EVA ORCHARD", orchard_id="private-profile", crops=["walnut"], variety="PRIVATE_CULTIVAR",
                                     lat=38.55, lon=-121.8, elevation_ft=65)
    service = ConversationService(monitor, notification_capability="browser_open")
    return service, monitor, profile, clock, generator


def ask(factual, text, request_id="actual-question", **kwargs):
    service, _, profile, _, _ = factual
    return service.message(request_id=request_id, text=text, orchard_id=profile["id"], **kwargs)["assistant_turn"]


@pytest.mark.parametrize("question,intent,fragment", [
    ("What crops am I growing?", "profile_crops", "walnut"),
    ("Where is my farm saved?", "profile_location", "38.5500, -121.8000, 65 ft"),
    ("Are browser alerts enabled?", "profile_notifications", "disabled"),
    ("Are you monitoring?", "profile_monitor", "pending"),
    ("How do you count chill hours?", "chill_method", "32–45°F"),
])
def test_saved_profile_facts_use_actual_selected_generator_once_without_private_profile(factual, question, intent, fragment):
    service, monitor, profile, _, generator = factual
    original_profile = deepcopy(profile)
    local, facts = profile_answer(intent, service.state(profile["id"]))
    answer = ask(factual, question)
    assert answer["data"]["generation"] == "chatgpt_plan" and answer["data"]["generation_outcome"] == "completed"
    assert answer["data"]["local_facts"] == facts and fragment in answer["text"] and answer["text"] != local
    assert len(generator.calls) == 1
    request = json.dumps(generator.calls)
    assert all(private not in request for private in (question, "PRIVATE EVA ORCHARD", "PRIVATE_CULTIVAR", "38.55", "-121.8", "private-profile"))
    assert all("enum" not in value for value in generator.calls[0]["schema"]["properties"].values())
    assert monitor.store.orchard(profile["id"]) == original_profile
    assert ask(factual, question) == answer and len(generator.calls) == 1
    restarted = ConversationService(Monitor(Store(monitor.store.path), writer=monitor.writer, clock=monitor.clock), notification_capability="browser_open")
    assert restarted.message(request_id="actual-question", text=question, orchard_id=profile["id"])["assistant_turn"] == answer
    assert len(generator.calls) == 1


@pytest.mark.parametrize("question,intent,fragment", [
    ("What is the temperature?", "temperature", "52.75°F"),
    ("Are the weather readings fresh?", "freshness", "Jan 11, 2026"),
    ("What is the humidity?", "humidity", "74.5%"),
    ("How is wind speed today?", "wind", "7.2 mph"),
    ("What is solar radiation?", "solar_radiation", "0 W/m²"),
    ("What are the weather variables?", "weather_variables", "74.5%"),
    ("Is the temperature normal?", "temperature_normal", "No calibrated normal-temperature range"),
])
def test_ordinary_saved_weather_is_genuinely_authored_and_keeps_source_facts(factual, question, intent, fragment):
    _, monitor, profile, clock, generator = factual
    # The temperature and independent station variables remain distinct facts.
    if intent in {"temperature", "freshness", "temperature_normal"}:
        monitor.apply(profile["id"], {"latest": packet(profile, clock[0], temperature=52.75)}, now=clock[0])
    else:
        retain((monitor, profile, clock))
    local, facts = weather_answer(intent, monitor, profile["id"])
    before = deepcopy(facts)
    answer = ask(factual, question)
    assert answer["data"]["intent"] == intent and answer["data"]["generation"] == "chatgpt_plan"
    assert answer["data"]["local_facts"] == before and fragment in answer["text"]
    assert answer["text"] != local and "Jan 11, 2026" in answer["text"] and "PST" in answer["text"]
    if intent == "solar_radiation":
        assert "withheld in low daylight" in answer["text"]
    if intent == "weather_variables":
        assert "7.2 mph" in answer["text"] and "0 W/m²" in answer["text"]
    assert len(generator.calls) == 1 and question not in json.dumps(generator.calls)
    assert ask(factual, question) == answer and len(generator.calls) == 1


def test_temperature_reference_and_source_failure_do_not_become_current_claims(factual):
    _, monitor, profile, clock, generator = factual
    monitor.apply(profile["id"], {"latest": packet(profile, clock[0], temperature=52.75)}, now=clock[0])
    first = ask(factual, "What is the temperature?", "source-temp")
    evidence = first["data"]["local_facts"]["evidence_id"]
    clock[0] += timedelta(hours=8)
    monitor._source_failure(profile["id"], clock[0])
    monitor.apply(profile["id"], {"latest": packet(profile, clock[0], temperature=80)}, now=clock[0])
    answer = ask(factual, "What is that temperature?", "old-reference", reply_to=first["id"])
    assert answer["data"]["local_facts"]["referenced_snapshot"]
    assert answer["data"]["local_facts"]["evidence_id"] == evidence
    assert "52.75°F" in answer["text"] and "80°F" not in answer["text"]
    assert "does not verify current conditions" in answer["text"] and len(generator.calls) == 2
    stale = ask(factual, "What is the temperature now?", "stale")
    assert "source refresh failed" in stale["text"] and "current conditions are not verified" in stale["text"]


@pytest.mark.parametrize("question", ["Tell me about Minecraft", "What is the humidity password?", "Predict humidity tomorrow", "Thanks", "Got it"])
def test_offtopic_unsupported_and_acknowledgments_never_call_plan(factual, question):
    answer = ask(factual, question)
    assert answer is None or answer["data"].get("generation") != "chatgpt_plan"
    assert factual[-1].calls == []


@pytest.mark.parametrize("question", ["What is the temperature?", "What is the humidity?", "Compare humidity with last year"])
def test_absent_or_unsupported_weather_stays_truthfully_local(factual, question):
    answer = ask(factual, question)
    assert answer["data"]["generation"] == "template" and factual[-1].calls == []


@pytest.mark.parametrize("edit", [
    lambda text: text.replace("{{observed}}", "96%"),
    lambda text: text.replace("{{observed}}", "{{observed}} mph"),
    lambda text: text.replace("{{station}}", "{{observed}}").replace("was {{observed}}", "was {{station}}"),
    lambda text: text.replace("{{variable}}", "temperature {{variable}}"),
    lambda text: text + "; the crop is safe",
    lambda text: text + "; irrigate now",
    lambda text: text + "; station is healthy",
    lambda text: text + "; there is no risk",
])
def test_hallucinated_station_entities_quantities_units_and_agronomy_fall_back(factual, edit):
    _, monitor, profile, clock, generator = factual
    retain((monitor, profile, clock))
    expected, facts = weather_answer("humidity", monitor, profile["id"])
    generator.transform = lambda clauses: {"text": json.dumps({key: edit(text) for key, text in clauses.items()})}
    answer = ask(factual, "What is the humidity?")
    assert answer["text"] == expected and answer["data"]["local_facts"] == facts
    assert answer["data"]["generation"] == "template" and answer["data"]["generation_outcome"] == "llm_failed_or_invalid"
    assert len(generator.calls) == 1 and ask(factual, "What is the humidity?") == answer and len(generator.calls) == 1


@pytest.mark.parametrize("intent,facts,invalid", [
    ("profile_crops", {"crops": ["walnut"], "variety": None}, "you have not {{crops}} saved"),
    ("profile_location", {"lat": 38.55, "lon": -121.8, "elevation_ft": 65}, "your farm is not saved at {{location}}"),
    ("profile_notifications", {"capability": "browser_open", "enabled": False}, "browser alerts are enabled"),
    ("profile_monitor", {"status": "pending"}, "monitoring is active"),
    ("temperature", {"temperature_f": 52.75, "temperature_time": HOUR.isoformat(), "current_conditions_verified": False}, "estimated orchard temperature is {{temperature}}"),
])
def test_profile_and_availability_polarity_cannot_reverse(intent, facts, invalid):
    generator = Generator(lambda _: {"text": json.dumps({"event_0": invalid})})
    result = write_factual(intent, facts, "original local facts", ConversationalWriter(Writer(generator=generator)))
    assert result == {"text": "original local facts", "provider": "template", "outcome": "llm_failed_or_invalid"}
    assert len(generator.calls) == 1


@pytest.mark.parametrize("failure", ["model_selection_required", "provider_error"])
def test_model_or_uncertain_provider_failure_is_redacted_and_same_request_never_retries(factual, failure):
    class Failed(Generator):
        def generate(self, **kwargs):
            self.calls.append(kwargs)
            if failure == "model_selection_required":
                raise PlanError(failure)
            raise RuntimeError("PRIVATE_PROVIDER_BODY_AND_CREDENTIAL")

    service, monitor, profile, _, _ = factual
    generator = Failed()
    monitor.writer = ConversationalWriter(Writer(generator=generator))
    expected, _ = profile_answer("profile_crops", service.state(profile["id"]))
    answer = ask(factual, "What crops am I growing?")
    assert answer["text"] == expected and answer["data"]["generation"] == "template"
    assert "PRIVATE_PROVIDER" not in json.dumps(answer)
    assert ask(factual, "What crops am I growing?") == answer and len(generator.calls) == 1


def test_no_plan_never_uses_legacy_api_key_billing(factual):
    class NeverCall:
        def post(self, *_, **__):
            raise AssertionError("No alternative provider")

    service, monitor, profile, _, _ = factual
    monitor.writer = ConversationalWriter(Writer(key="unused", model="unused", client=NeverCall()))
    answer = ask(factual, "What crops am I growing?")
    assert answer["data"]["generation_outcome"] == "chatgpt_not_connected"
    assert answer["text"] == profile_answer("profile_crops", service.state(profile["id"]))[0]


@pytest.mark.parametrize("swap", [False, True])
def test_saved_discrepancy_reuses_weather_role_validator_and_preserves_signed_units(factual, swap):
    _, monitor, profile, clock, generator = factual
    finding = {"variable": "relative_humidity_pct", "station_id": 6, "classification": "suspected_discrepancy",
               "severity": "strong", "observed": 95, "expected": 40, "departure": 55, "unit": "%",
               "departure_unit": "percentage points", "detected_at_utc": clock[0].isoformat()}
    value = retain((monitor, profile, clock), values=(95, 7.2, 0), findings=[finding])
    value["weather"]["variables"][0]["stations"][0].update(expected=40, departure=55, classification="suspected_discrepancy")
    monitor.apply(profile["id"], {"latest": value}, now=clock[0])
    local, facts = weather_answer("humidity", monitor, profile["id"])
    if swap:
        generator.transform = lambda clauses: {"text": json.dumps({key: text.replace("{{observed}}", "{{swap}}").replace("{{expected}}", "{{observed}}").replace("{{swap}}", "{{expected}}") for key, text in clauses.items()})}
    answer = ask(factual, "What is the humidity?")
    assert answer["data"]["local_facts"] == facts and len(generator.calls) == 1
    if swap:
        assert answer["text"] == local and answer["data"]["generation"] == "template"
    else:
        assert answer["data"]["generation"] == "chatgpt_plan"
        assert all(fragment in answer["text"] for fragment in ("95%", "40%", "+55 percentage points", "provisional", "cause unconfirmed"))


@pytest.mark.parametrize("response", [
    {"text": "not JSON"},
    {"text": "{}"},
    {"text": '{"event_0":"a","event_0":"b"}'},
    {"text": '{"event_0":"you have {{crops}} saved"}', "status": "incomplete"},
    {"text": '{"event_0":"you have {{crops}} saved"}', "refusal": "private refusal"},
])
def test_malformed_refused_and_incomplete_outputs_do_not_become_generated_answers(factual, response):
    service, _, profile, _, generator = factual
    generator.transform = lambda _: response
    local, _ = profile_answer("profile_crops", service.state(profile["id"]))
    answer = ask(factual, "What crops am I growing?")
    assert answer["text"] == local and answer["data"]["generation"] == "template"
    assert answer["data"]["generation_outcome"] == "llm_failed_or_invalid" and len(generator.calls) == 1


def test_two_novel_valid_wordings_are_authored_without_candidate_selection(factual):
    _, _, _, _, generator = factual
    first = ask(factual, "What crops am I growing?", "first-wording")
    generator.transform = lambda clauses: {"text": json.dumps({key: text.replace("you have {{crops}} saved", "your saved crops include {{crops}}") for key, text in clauses.items()})}
    second = ask(factual, "What crops am I growing?", "second-wording")
    assert first["text"] != second["text"]
    assert first["data"]["generation"] == second["data"]["generation"] == "chatgpt_plan"
    assert len(generator.calls) == 2
    for request in generator.calls:
        assert "candidate" not in json.dumps(request["packet"]).lower()
        assert all("enum" not in value for value in request["schema"]["properties"].values())


def test_private_binding_values_are_inserted_once_and_never_reinterpreted_as_fields():
    facts = {"crops": ["custom {{variety}} crop"], "variety": "PRIVATE"}
    generator = Generator()
    result = write_factual("profile_crops", facts, "local", ConversationalWriter(Writer(generator=generator)))
    assert result["provider"] == "chatgpt_plan"
    assert "custom {{variety}} crop" in result["text"] and "saved variety is PRIVATE" in result["text"]
    assert "PRIVATE" not in json.dumps(generator.calls) and "custom {{variety}} crop" not in json.dumps(generator.calls)


def test_actual_injected_farm_temperature_remains_replay_and_source_dated(factual):
    service, monitor, _, clock, generator = factual
    profile = monitor.create_orchard("PRIVATE REPLAY NAME", orchard_id="injected-profile", crops=["walnut"], mode="injected",
                                     lat=38.55, lon=-121.8, elevation_ft=65)
    monitor.apply(profile["id"], {"latest": packet(profile, clock[0], temperature=43.25)}, now=clock[0])
    answer = service.message(request_id="replay-temperature", text="What is the temperature?", orchard_id=profile["id"])["assistant_turn"]
    assert answer["data"]["generation"] == "chatgpt_plan"
    assert not answer["text"].startswith("Replay only:") and "43.25°F" in answer["text"]
    assert answer["data"]["local_facts"]["data_mode"] == "injected"
    assert "Jan 11, 2026" in answer["text"] and not answer["data"]["local_facts"]["current_conditions_verified"]
    assert len(generator.calls) == 1 and "PRIVATE REPLAY NAME" not in json.dumps(generator.calls)


def test_actual_guarded_runtime_api_authors_profile_and_weather_with_selected_plan(factual, tmp_path):
    from fastapi.testclient import TestClient
    from terra_monitoring.demo_simulation.full_flow import DisabledSMS
    from terra_monitoring.runtime import create_runtime_app

    class Query:
        def context(self):
            return {"configured": False}

        def assess(self, _):
            raise AssertionError("No provider acquisition")

    _, monitor, profile, clock, generator = factual
    app = create_runtime_app(monitor=monitor, live_service=Query(), sms_dispatcher=DisabledSMS(), state_dir=tmp_path / "runtime")
    monitor.apply(profile["id"], {"latest": packet(profile, clock[0], temperature=52.75)}, now=clock[0])
    with TestClient(app, base_url="http://127.0.0.1") as client:
        headers = {"X-Terra-SMS-Control": client.get("/api/sms/status").json()["control_token"],
                   "X-Terra-Source-Context": client.get("/api/simulation/connection").json()["source_context"]}
        for index, question in enumerate(("What crops am I growing?", "What is the temperature?")):
            body = {"request_id": f"guarded-{index}", "text": question, "orchard_id": profile["id"]}
            response = client.post("/api/tara/messages", headers=headers, json=body)
            assert response.status_code == 200, response.text
            answer = response.json()["assistant_turn"]
            assert answer["data"]["generation"] == "chatgpt_plan"
            assert client.post("/api/tara/messages", headers=headers, json=body).json()["assistant_turn"] == answer
        assert len(generator.calls) == 2
