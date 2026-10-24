"""Referenced alert explanations retain each immutable source's evidence time."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from terra_monitoring.farm_ops.conversational_writer import ConversationalWriter
from terra_monitoring.farm_ops.service import _event
from terra_monitoring.farm_ops.writer import Writer, message_input, _time
from test_terra_conversation import conversation, setup_farm


def at(day):
    return datetime(2026, 1, day, 16, tzinfo=timezone.utc)


class RecordingLocalWriter:
    def __init__(self):
        self.packets = []
        self.writer = ConversationalWriter(Writer(key="", model=""))

    def write(self, packet):
        self.packets.append(deepcopy(packet))
        return self.writer.write(packet)


def source_note(monitor, profile, events):
    with monitor.store.connect(write=True) as db:
        for event in events:
            monitor.store.add_event(db, profile["id"], event, event["occurred_at"])
    packet = message_input(profile, events, at(10))
    return monitor.store.publish(profile["id"], packet,
        {"text": "Original synthetic field note.", "provider": "template", "outcome": "fixture"},
        [event["id"] for event in events], at(10))


@pytest.mark.parametrize("facts,occurred", [
    ({"observation_time": at(5).isoformat(), "data_through": at(6).isoformat()}, at(7)),
    ({"data_through": at(5).isoformat()}, at(7)),
    ({}, at(5)),
])
def test_single_stale_alert_uses_original_observation_data_or_occurrence_time(conversation, facts, occurred):
    service, monitor, _ = conversation
    orchard = setup_farm(service)
    profile = monitor.store.orchard(orchard)
    event = _event(profile, "monitoring_limited", "opening", "moderate", occurred, facts, "original-outage")
    note = source_note(monitor, profile, [event])
    original = deepcopy(note)
    writer = RecordingLocalWriter()
    monitor.writer, monitor.clock = writer, lambda: at(14)
    reply = service.message(request_id="stale-explanation", text="Why?", orchard_id=orchard, reply_to=note["id"])
    packet = writer.packets[0]
    assert packet["as_of"] == str(at(14))
    assert len(packet["events"]) == 1
    explanation = packet["events"][0]
    assert explanation["occurred_at"] == occurred.isoformat()
    assert explanation["facts"]["observation_time"] == at(5).isoformat()
    assert explanation["display_time"] == _time(at(5))
    assert _time(at(5)) in reply["assistant_turn"]["text"] and "Jan 14" not in reply["assistant_turn"]["text"]
    assert reply["assistant_turn"]["data"]["source_event_ids"] == note["event_ids"]
    details = monitor.store.details(orchard, reply["assistant_turn"]["data"]["message_id"])
    assert details["message"]["event_ids"] == note["event_ids"]
    assert details["events"][0]["facts"] == facts
    assert monitor.store.message(note["id"]) == original


def test_mixed_stale_alerts_keep_individual_dates_ids_and_followup_context(conversation):
    service, monitor, _ = conversation
    orchard = setup_farm(service)
    profile = monitor.store.orchard(orchard)
    outage = _event(profile, "monitoring_limited", "opening", "moderate", at(7),
                    {"observation_time": at(5).isoformat(), "data_through": at(6).isoformat()}, "outage")
    recovered = _event(profile, "monitoring_recovered", "recovery", "info", at(10),
                       {"data_through": at(9).isoformat(), "consecutive_valid_readings": 3}, "recovery")
    note = source_note(monitor, profile, [outage, recovered])
    original = deepcopy(note)
    writer = RecordingLocalWriter()
    monitor.writer, monitor.clock = writer, lambda: at(14)
    first = service.message(request_id="mixed-stale", text="Explain this alert", orchard_id=orchard, reply_to=note["id"])
    sent = writer.packets[0]
    assert len(sent["events"]) == 2
    assert {event["display_time"] for event in sent["events"]} == {_time(at(5)), _time(at(9))}
    assert {event["occurred_at"] for event in sent["events"]} == {at(7).isoformat(), at(10).isoformat()}
    text = first["assistant_turn"]["text"].lower()
    assert "cause is unconfirmed (as of " + _time(at(5)).lower() + ")" in text
    assert "coverage recovery (as of " + _time(at(9)).lower() + ")" in text
    assert "jan 14" not in text
    assert first["assistant_turn"]["data"]["source_event_ids"] == note["event_ids"]
    monitor.clock = lambda: at(16)
    second = service.message(request_id="mixed-followup", text="What does that mean?", orchard_id=orchard,
                             reply_to=first["assistant_turn"]["id"])
    assert second["assistant_turn"]["data"]["source_event_ids"] == note["event_ids"]
    assert {event["display_time"] for event in writer.packets[-1]["events"]} == {_time(at(5)), _time(at(9))}
    assert "Jan 16" not in second["assistant_turn"]["text"]
    assert monitor.store.message(note["id"]) == original
