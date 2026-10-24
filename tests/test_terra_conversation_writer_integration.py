"""Validate the actual fact-bound writer beneath conversational presentation."""
import copy
import json

import httpx

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
    assert "Tara's short replies" in generator.calls[0]["instructions"]
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
