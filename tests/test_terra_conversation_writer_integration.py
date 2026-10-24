"""Validate the actual fact-bound writer beneath conversational presentation."""
import copy
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from terra_monitoring.farm_ops.conversational_writer import ConversationalWriter, compact_text
from terra_monitoring.farm_ops.writer import Writer, message_input


def packet(*, mode="current", kind="chill_total", question=True):
    profile = {"name": "North field", "crop": "almond", "variety": None,
               "demo_location": False, "mode": mode}
    event = {"id": "event-one", "type": kind, "transition": "answer", "severity": "info",
             "occurred_at": "2026-01-14T16:00:00+00:00",
             "facts": {"hours": 180.0, "complete": True, "data_through": "2026-01-14T16:00:00+00:00"}}
    return message_input(profile, [event], "2026-01-14T16:00:00+00:00",
                         question={"intent": "chill_total", "text": "How much chill?"} if question else None)


class Generator:
    def __init__(self, clause="estimated chill is {{hours}} hours"):
        self.calls = []
        self.clause = clause

    def generate(self, **request):
        self.calls.append(request)
        return {"text": json.dumps({"event_0": self.clause})}


def test_eligible_reply_uses_one_plan_call_and_retains_exact_input():
    generator = Generator()
    writer = ConversationalWriter(Writer(generator=generator))
    source = packet()
    before = copy.deepcopy(source)
    result = writer.write(source)
    assert result["provider"] == "chatgpt_plan"
    assert result["text"] == "Estimated chill is 180 hours. As of Jan 14."
    assert len(generator.calls) == 1
    assert "Terra's short replies" in generator.calls[0]["instructions"]
    assert "factual output/placeholder rules" in generator.calls[0]["instructions"]
    assert "How much chill?" not in json.dumps(generator.calls)
    assert source == before
    assert "8:00 AM PST" in source["events"][0]["display_time"]


def test_interactive_fallback_never_uses_configured_api_key():
    calls = []
    client = httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(request)))
    writer = ConversationalWriter(Writer(key="fake-test-only", model="configured-model", client=client))
    result = writer.write(packet())
    assert result["provider"] == "template"
    assert result["outcome"] == "chatgpt_not_connected"
    assert result["text"] == "Estimated chill is 180 hours. As of Jan 14."
    assert calls == []


def test_invented_model_quantity_keeps_validated_local_fallback():
    generator = Generator("estimated chill is 999 hours")
    result = ConversationalWriter(Writer(generator=generator)).write(packet())
    assert result["provider"] == "template"
    assert "999" not in result["text"]
    assert "180" in result["text"]
    assert len(generator.calls) == 1


def test_replay_marker_survives_compact_reply():
    result = ConversationalWriter(Writer(key="", model="")).write(packet(mode="replay"))
    assert result["text"].startswith("Demo replay: ")
    assert "North field" not in result["text"]


def test_mixed_evidence_times_are_not_erased():
    source = packet()
    second = copy.deepcopy(source["events"][0])
    second["display_time"] = "Jan 13, 08:00 AM PST"
    source["events"].append(second)
    text = "North field: current total (as of Jan 14, 08:00 AM PST); earlier total (as of Jan 13, 08:00 AM PST)."
    assert "Jan 14, 08:00 AM PST" in compact_text(source, text)
    assert "Jan 13, 08:00 AM PST" in compact_text(source, text)


def test_data_health_clock_remains_when_it_changes_interpretation():
    source = packet(kind="station_health")
    text = "North field: weather support is limited. As of Jan 14, 08:00 AM PST."
    assert compact_text(source, text).endswith("As of Jan 14, 08:00 AM PST.")


def test_approved_weather_voice_is_not_rewritten():
    source = packet(kind="monitoring_limited", question=False)
    text = "Weather data’s a bit spotty right now, so the estimate’s incomplete."
    assert compact_text(source, text) == text


def test_old_or_incomplete_total_keeps_date_and_year_without_clock_noise():
    source = packet()
    source["as_of"] = "2027-01-20T16:00:00+00:00"
    source["events"][0]["facts"]["complete"] = False
    result = ConversationalWriter(Writer(key="", model="")).write(source)
    assert "incomplete" in result["text"]
    assert "As of Jan 14 2026." in result["text"]
    assert "AM PST" not in result["text"]


def test_unavailable_total_does_not_invent_a_source_cutoff():
    source = packet()
    source["events"][0]["facts"]["hours"] = None
    source["events"][0]["facts"].pop("data_through")
    result = ConversationalWriter(Writer(key="", model="")).write(source)
    assert result["text"] == "Estimated chill is currently unavailable."


def test_unavailable_total_does_not_spend_plan_usage_rewording_absence():
    source = packet()
    source["events"][0]["facts"]["hours"] = None
    generator = Generator()
    result = ConversationalWriter(Writer(generator=generator)).write(source)
    assert result["text"] == "Estimated chill is currently unavailable."
    assert result["outcome"] == "local_unavailable"
    assert generator.calls == []


def test_real_runtime_routes_select_plan_writer_only_for_relevant_input(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from terra_monitoring.chatgpt import local
    from terra_monitoring.live import LiveQueryService
    from terra_monitoring.runtime import create_runtime_app
    from terra_monitoring.sms import SMSDispatcher

    provider = Generator("supporting weather data is unavailable for the orchard estimate")
    monkeypatch.setattr(local, "AuthManager", lambda path: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(local, "ChatGPTPlanClient", lambda auth, path: provider)
    source = LiveQueryService(app_key="", data_dir=tmp_path / "source")
    sms = SMSDispatcher.from_env({"TERRA_SMS_ENABLED": "false", "TERRA_SMS_DEMO_ENABLED": "false",
                                 "TERRA_SMS_DB_PATH": str(tmp_path / "sms.sqlite3")})
    app = create_runtime_app(state_dir=tmp_path, live_service=source, sms_dispatcher=sms,
        chatgpt_enabled=True, clock=lambda: datetime(2026, 1, 14, 16, tzinfo=timezone.utc))
    assert app.state.runtime_monitor.writer.generator is provider
    with TestClient(app, base_url="http://127.0.0.1") as client:
        control = client.get("/api/sms/status").json()["control_token"]
        headers = {"X-Terra-SMS-Control": control,
                   "X-Terra-Source-Context": client.get("/api/simulation/connection").json()["source_context"]}
        assert client.get("/api/tara/conversation").json()["setup"]["stage"] == "crop"

        def post(path, body):
            response = client.post(path, json=body, headers=headers)
            assert response.status_code == 200, response.text
            return response.json()

        post("/api/tara/messages", {"request_id": "crop", "text": "Peaches"})
        saved = post("/api/tara/setup", {"request_id": "location", "action": "location", "location": {
            "lat": 38.6, "lon": -121.84, "elevation_ft": 70.0, "name": "Conversation QA farm",
            "source": "manual", "confirmed": True}})
        orchard = saved["state"]["orchard_id"]
        assert saved["state"]["orchards"][0]["variety"] is None
        post("/api/tara/setup", {"request_id": "notifications", "action": "notifications",
            "notifications": {"enabled": False, "permission": "default"}})
        for identity, text, outcome in (("game", "Tell me about Minecraft", "reaction"),
                ("thanks", "Thanks for the update!", "none"), ("ack", "Got it", "reaction")):
            result = post("/api/tara/messages", {"request_id": identity, "text": text, "orchard_id": orchard})
            assert result["outcome"] == outcome
        assert provider.calls == []
        unavailable = post("/api/tara/messages", {"request_id": "no-total", "text": "How much chill?", "orchard_id": orchard})
        assert unavailable["assistant_turn"]["text"] == "Estimated chill is currently unavailable."
        assert provider.calls == []
        question = {"request_id": "actual-question", "text": "Thanks, is the weather data usable?", "orchard_id": orchard}
        answer = post("/api/tara/messages", question)
        assert answer["outcome"] == "message"
        assert answer["assistant_turn"]["data"]["generation"] == "chatgpt_plan"
        assert "unavailable for the orchard estimate" in answer["assistant_turn"]["text"]
        assert len(provider.calls) == 1
        again = post("/api/tara/messages", question)
        assert again["assistant_turn"]["id"] == answer["assistant_turn"]["id"]
        assert len(provider.calls) == 1
        for _ in range(2):
            assert client.get("/api/tara/conversation", params={"orchard_id": orchard}).status_code == 200
        assert len(provider.calls) == 1
        legacy = client.post(f"/api/orchards/{orchard}/questions", headers=headers,
                             json={"question": "Compare ChatGPT and Claude", "request_id": "legacy-offtopic"})
        assert legacy.status_code == 200
        assert len(provider.calls) == 1
        assert client.post("/api/tara/messages", json={"request_id": "unguarded", "text": "Chill?", "orchard_id": orchard}).status_code == 403


@pytest.mark.parametrize("question", ["Why did I get this alert?", "Terra, why did I get this alert?"])
def test_referenced_get_alert_question_uses_the_configured_fact_checked_writer_once(tmp_path, question):
    from terra_monitoring.conversation import ConversationService
    from terra_monitoring.farm_ops.service import Monitor
    from terra_monitoring.farm_ops.store import Store

    clock = datetime(2026, 1, 14, 16, tzinfo=timezone.utc)
    monitor = Monitor(Store(tmp_path / "referenced-alert.sqlite3"), writer=Writer(key="", model=""), clock=lambda: clock)
    profile = monitor.create_orchard("Saved walnut farm", orchard_id="walnut-farm", crops=["walnut"])
    with monitor.store.connect(write=True) as db:
        monitor.store.set_state(db, "watch:" + profile["id"], {"welcomed": True})
    monitor._source_failure(profile["id"], clock)
    monitor.flush(profile["id"], now=clock)
    source = monitor.store.messages(profile["id"])[0]
    original = copy.deepcopy(source)
    provider = Generator("weather coverage is limited and the orchard estimate is incomplete")
    monitor.writer = ConversationalWriter(Writer(generator=provider))
    service = ConversationService(monitor)
    body = {"request_id": "why-get-alert", "text": question, "orchard_id": profile["id"], "reply_to": source["id"]}
    answer = service.message(**body)["assistant_turn"]
    assert answer["data"]["intent"] == "explain_alert"
    assert answer["data"]["generation"] == "chatgpt_plan"
    assert answer["data"]["source_event_ids"] == source["event_ids"]
    assert "weather coverage is limited" in answer["text"].lower()
    assert len(provider.calls) == 1
    assert body["text"] not in json.dumps(provider.calls)
    assert monitor.store.message(source["id"]) == original
    assert service.message(**body)["assistant_turn"] == answer
    assert len(provider.calls) == 1
    for index, text in enumerate(("Why did I get this alert in Minecraft?", "Why did I get this alert about qux telemetry?")):
        service.message(request_id=f"out-of-scope-{index}", text=text, orchard_id=profile["id"], reply_to=source["id"])
    assert len(provider.calls) == 1



def test_saved_weather_alert_followup_keeps_actual_station_facts_and_plan_writer(tmp_path):
    from terra_monitoring.conversation import ConversationService
    from terra_monitoring.farm_ops.service import Monitor, _event
    from terra_monitoring.farm_ops.store import Store

    clock = datetime(2026, 1, 14, 16, tzinfo=timezone.utc)
    monitor = Monitor(Store(tmp_path / "saved-weather.sqlite3"), writer=Writer(key="", model=""), clock=lambda: clock)
    profile = monitor.create_orchard("Walnut farm", orchard_id="weather-farm", crops=["walnut"])
    with monitor.store.connect(write=True) as db:
        monitor.store.set_state(db, "watch:" + profile["id"], {"welcomed": True})
    event = _event(profile, "weather_discrepancy", "opening", "strong", clock,
        {"variable": "wind_speed_mph", "variable_label": "Wind speed", "station_id": 6, "unit": "mph",
         "observed": 60.0, "expected": 6.0, "departure": 54.0, "observation_time": clock.isoformat()}, "saved-wind")
    with monitor.store.connect(write=True) as db:
        monitor.store.add_event(db, profile["id"], event, clock)
    saved_input = message_input(profile, [event], clock)
    source = monitor.store.publish(profile["id"], saved_input, monitor.writer.write(saved_input), [event["id"]], clock)
    original = copy.deepcopy(source)
    provider = Generator("at station {{station}}, {{variable}} reads {{observed}}, compared with peer reference {{expected}}; provisional departure {{departure}}, cause unconfirmed")
    monitor.writer = ConversationalWriter(Writer(generator=provider))
    service = ConversationService(monitor)
    body = {"request_id": "weather-followup", "text": "Terra, why did I get this alert?",
            "orchard_id": profile["id"], "reply_to": source["id"]}
    reply = service.message(**body)["assistant_turn"]
    assert reply["data"]["generation"] == "chatgpt_plan"
    assert reply["data"]["source_event_ids"] == [event["id"]]
    assert "60 mph" in reply["text"] and "6 mph" in reply["text"] and "+54 mph" in reply["text"]
    assert "Jan 14" in reply["text"]
    assert len(provider.calls) == 1 and body["text"] not in json.dumps(provider.calls)
    assert monitor.store.message(source["id"]) == original
    assert service.message(**body)["assistant_turn"] == reply
    assert len(provider.calls) == 1
