"""Actual durable conversation flow with controlled selected-plan responses."""
from copy import deepcopy
import json

import pytest

from terra_monitoring.conversation import ConversationService
from terra_monitoring.farm_ops.service import Monitor
from terra_monitoring.farm_ops.store import Store
from terra_monitoring.farm_ops.writer import Writer
from test_farm_chill_ops import HOUR


class Plan:
    def __init__(self):
        self.calls = []
        self.answer = None

    def generate(self, **request):
        self.calls.append(deepcopy(request))
        value = self.answer(request["packet"]) if callable(self.answer) else self.answer
        return {"status": "completed", "text": json.dumps(value)}


class EngineWriter:
    conversation_engine = True

    def __init__(self, generator):
        self.generator = generator

    def write(self, packet):
        raise AssertionError("Conversation must not fall through to a second writer request")


def response(text, *, outcome="message", fact_ids=None, focus="weather"):
    return {"outcome": outcome, "text": text, "fact_ids": fact_ids or [], "focus": focus}


def cite(packet, needle):
    return next(fact["id"] for fact in packet["facts"] if needle in fact["text"].lower())


@pytest.fixture
def flow(tmp_path):
    plan = Plan()
    monitor = Monitor(Store(tmp_path / "farm.sqlite3"), writer=Writer(key="", model=""), clock=lambda: HOUR)
    profile = monitor.create_orchard("Peach farm", orchard_id="peach-farm", crops=["peach"],
                                     lat=38.55, lon=-121.8, elevation_ft=65)
    service = ConversationService(monitor, notification_capability="browser_open")
    monitor.writer = EngineWriter(plan)
    return service, monitor, plan, profile["id"]


@pytest.mark.parametrize("question", [
    "Does this represent an anomaly?",
    "Can you summarize the implications of these readings for my orchard?",
    "What additional context do you need from me?",
    "Why is the station behaving strangely?",
])
def test_unusual_natural_questions_reach_primary_engine(flow, question):
    service, _, plan, orchard = flow
    plan.answer = lambda packet: response("Do you mean the orchard estimate or a particular station reading?",
        outcome="clarify", focus="weather")
    result = service.message(request_id="natural-question", text=question, orchard_id=orchard)
    assert result["outcome"] == "message"
    assert result["assistant_turn"]["data"]["response_kind"] == "clarify"
    assert result["assistant_turn"]["data"]["generation"] == "chatgpt_plan"
    assert plan.calls[0]["packet"]["request"]["text"] == question
    assert len(plan.calls) == 1


def test_followup_sees_prior_question_and_actual_model_answer(flow):
    service, _, plan, orchard = flow
    first_text = "Which reading are you asking about?"
    plan.answer = response(first_text, outcome="clarify", focus="temperature")
    service.message(request_id="first", text="Does that look unusual?", orchard_id=orchard)
    plan.answer = response("Do you mean the orchard estimate itself?", outcome="clarify", focus="temperature")
    second = service.message(request_id="follow-up", text="The estimate itself", orchard_id=orchard)
    history = plan.calls[-1]["packet"]["history"]
    assert any(turn["text"] == "Does that look unusual?" for turn in history)
    assert any(turn["text"] == first_text for turn in history)
    assert second["assistant_turn"]["data"]["focus"] == "temperature"
    assert len(plan.calls) == 2


def test_generation_and_silence_decisions_are_durable_and_refresh_is_free(flow):
    service, monitor, plan, orchard = flow
    plan.answer = response(None, outcome="none", focus=None)
    body = {"request_id": "thanks", "text": "Thanks for explaining that", "orchard_id": orchard}
    result = service.message(**body)
    assert result["outcome"] == "none"
    assert result["decision"]["generation"] == "chatgpt_plan"
    restarted = ConversationService(Monitor(Store(monitor.store.path), writer=monitor.writer, clock=lambda: HOUR))
    assert restarted.message(**body)["decision"] == result["decision"]
    restarted.state(orchard)
    restarted.state(orchard)
    assert len(plan.calls) == 1


def test_invalid_generation_completes_with_local_fallback_without_retry(flow):
    service, _, plan, orchard = flow
    plan.answer = response("Your orchard is at 999 degrees.", fact_ids=["made-up-fact"])
    body = {"request_id": "invalid", "text": "Does this represent an anomaly?", "orchard_id": orchard}
    result = service.message(**body)
    assert result["assistant_turn"]["data"]["generation"] == "template"
    assert result["assistant_turn"]["data"]["generation_outcome"] == "engine_unavailable"
    assert "999" not in result["assistant_turn"]["text"]
    assert not result["state"]["pending_requests"]
    assert service.message(**body)["assistant_turn"] == result["assistant_turn"]
    assert len(plan.calls) == 1


def test_cross_farm_reference_rejected_before_any_model_attempt(flow):
    service, monitor, plan, orchard = flow
    other = monitor.create_orchard("Other farm", orchard_id="other-farm", crops=["walnut"])
    monitor.writer = Writer(key="", model="")
    monitor._source_failure(other["id"], HOUR)
    note = monitor.flush(other["id"], now=HOUR)
    monitor.writer = EngineWriter(plan)
    with pytest.raises(ValueError):
        service.message(request_id="cross-farm", text="Explain that", orchard_id=orchard, reply_to=note["id"])
    assert plan.calls == []
    assert service.state(orchard)["pending_requests"] == []


def test_reference_context_uses_saved_note_and_preserves_it(flow):
    service, monitor, plan, orchard = flow
    monitor.writer = Writer(key="", model="")
    monitor._source_failure(orchard, HOUR)
    note = monitor.flush(orchard, now=HOUR)
    original = deepcopy(note)
    monitor.writer = EngineWriter(plan)
    plan.answer = lambda packet: response("That note is about limited weather support, so the estimate is incomplete.",
        fact_ids=[cite(packet, "weather coverage")], focus="alert")
    result = service.message(request_id="explain-saved", text="What does that imply?", orchard_id=orchard, reply_to=note["id"])
    assert result["assistant_turn"]["data"]["generation"] == "chatgpt_plan"
    assert any(fact.get("event_ids") == note["event_ids"] for fact in plan.calls[0]["packet"]["facts"])
    assert monitor.store.message(note["id"]) == original
    assert len(plan.calls) == 1


def test_explicit_settings_command_uses_existing_executor_without_model(flow):
    service, _, plan, orchard = flow
    result = service.message(request_id="stop-alerts", text="Turn notifications off", orchard_id=orchard)
    assert result["state"]["notifications"]["enabled"] is False
    assert result["assistant_turn"]["text"].startswith("Notifications are off")
    assert not plan.calls


def test_setup_engine_gets_newly_saved_crop_and_preserves_location_control(flow):
    service, _, plan, _ = flow
    plan.answer = response("Share your farm location so I can find the nearby weather stations.",
                           focus="location")
    result = service.message(request_id="setup-crop", text="Peaches")
    assert result["state"]["setup"]["stage"] == "location"
    assert result["state"]["setup"]["crops"] == ["peach"]
    assert result["assistant_turn"]["data"]["action"] == "location_choice"
    assert plan.calls[0]["packet"]["setup"]["stage"] == "location"
    assert len(plan.calls) == 1


def test_actual_runtime_wrapper_and_guarded_api_use_contextual_engine(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from terra_monitoring.chatgpt import local
    from terra_monitoring.live import LiveQueryService
    from terra_monitoring.runtime import create_runtime_app
    from terra_monitoring.sms import SMSDispatcher

    plan = Plan()
    plan.answer = response("Which reading do you want to look at?", outcome="clarify", focus="weather")
    monkeypatch.setattr(local, "AuthManager", lambda _: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(local, "ChatGPTPlanClient", lambda *_: plan)
    source = LiveQueryService(app_key="", data_dir=tmp_path / "source")
    sms = SMSDispatcher.from_env({"TERRA_SMS_ENABLED": "false", "TERRA_SMS_DEMO_ENABLED": "false",
                                 "TERRA_SMS_DB_PATH": str(tmp_path / "sms.sqlite3")})
    app = create_runtime_app(state_dir=tmp_path, live_service=source, sms_dispatcher=sms,
                             chatgpt_enabled=True, clock=lambda: HOUR)
    assert app.state.runtime_monitor.writer.conversation_engine is True
    profile = app.state.runtime_monitor.create_orchard("API farm", orchard_id="api-farm", crops=["peach"])
    with TestClient(app, base_url="http://127.0.0.1") as client:
        token = client.get("/api/sms/status").json()["control_token"]
        headers = {"X-Terra-SMS-Control": token,
                   "X-Terra-Source-Context": app.state.simulation_connection.live_context}
        body = {"request_id": "api-contextual", "text": "Does this represent an anomaly?", "orchard_id": profile["id"]}
        denied = client.post("/api/tara/messages", json=body)
        assert denied.status_code == 403 and not plan.calls
        result = client.post("/api/tara/messages", headers=headers, json=body)
        assert result.status_code == 200, result.text
        answer = result.json()["assistant_turn"]
        assert answer["data"]["generation"] == "chatgpt_plan"
        assert answer["data"]["response_kind"] == "clarify"
        assert plan.calls[0]["packet"]["request"]["text"] == body["text"]
        assert client.post("/api/tara/messages", headers=headers, json=body).json()["assistant_turn"] == answer
        client.get("/api/tara/conversation", params={"orchard_id": profile["id"]})
        assert len(plan.calls) == 1
