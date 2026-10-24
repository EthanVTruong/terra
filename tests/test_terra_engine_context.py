"""Read-only contextual evidence admission, chronology and privacy boundaries."""
from copy import deepcopy
from datetime import timedelta
import json

import pytest

from terra_monitoring.conversation import ConversationService
from terra_monitoring.conversation.context import context_packet, MAX_HISTORY_CHARS, MAX_PACKET_BYTES
from terra_monitoring.farm_ops.service import Monitor
from terra_monitoring.farm_ops.store import Store
from terra_monitoring.farm_ops.writer import Writer
from test_farm_chill_ops import HOUR, packet


@pytest.fixture
def saved(tmp_path):
    clock = [HOUR]
    monitor = Monitor(Store(tmp_path / "context.sqlite3"), writer=Writer(key="", model=""), clock=lambda: clock[0])
    profile = monitor.create_orchard("Peach field", orchard_id="peach", crops=["peach"],
                                     lat=38.55, lon=-121.8, elevation_ft=65)
    service = ConversationService(monitor)
    return service, monitor, profile, clock


def collect(service, orchard, text="What does that mean?", reply_to=None, setup_override=None):
    return context_packet(service, text=text, selected=orchard, context=service._context(orchard, reply_to),
                          reply_to=reply_to, setup_override=setup_override)


def by_id(value):
    return {fact["id"]: fact for fact in value["facts"]}


def retain(monitor, profile, clock, **options):
    source = packet(profile, clock[0], **options)
    monitor.apply(profile["id"], {"latest": source}, now=clock[0])
    return source


def test_setup_override_is_context_only_without_geometry_or_other_farm(saved):
    service, monitor, profile, _ = saved
    monitor.create_orchard("Other private orchard", orchard_id="other", crops=["walnut"], lat=39, lon=-120, elevation_ft=90)
    original = service.state()["setup"]
    override = {**original, "stage": "location", "crops": ["peach"], "variety": "O'Henry"}
    value = collect(service, None, "I grow O'Henry peaches", setup_override=override)
    assert value["setup"] == {"stage": "location", "crops": ["peach"], "variety": "O'Henry"}
    assert "O'Henry" in by_id(value)["profile_crops"]["text"]
    assert "location" in by_id(value)["setup"]["text"]
    assert service.state()["setup"] == original and not value["orchard"]["id"]
    body = json.dumps(value)
    assert "Other private orchard" not in body and "walnut" not in body and "38.55" not in body and "-121.8" not in body


def test_current_facts_are_real_computed_values_and_no_raw_arrays(saved):
    service, monitor, profile, clock = saved
    retain(monitor, profile, clock, temperature=52.75, flags=True)
    before = (deepcopy(monitor.store.events(profile["id"])), deepcopy(monitor.store.messages(profile["id"])))
    value = collect(service, profile["id"])
    facts = by_id(value)
    assert "52.75" in facts["current_temperature"]["text"]
    assert "suspected" in facts["current_station_findings"]["text"].lower()
    assert facts["current_temperature"]["evidence_id"]
    assert facts["chill_history"]["text"] == "A reliable historical comparison isn't available yet."
    body = json.dumps(value)
    for forbidden in ("38.55", "-121.8", "query_lat", "geometry_key", "station_findings", "provenance"):
        # The stable fact ID is descriptive; no station finding arrays enter.
        if forbidden == "station_findings":
            assert '"station_findings": [' not in body
        else:
            assert forbidden not in body
    assert (monitor.store.events(profile["id"]), monitor.store.messages(profile["id"])) == before


def test_stale_weather_and_source_failure_never_echo_private_errors(saved):
    service, monitor, profile, clock = saved
    retain(monitor, profile, clock, temperature=44.25)
    clock[0] += timedelta(hours=8)
    with monitor.store.connect(write=True) as db:
        state = monitor.store.state(db, "watch:" + profile["id"], {})
        state["source_error"] = "PRIVATE_PROVIDER_BODY sk-shouldneverleak123456"
        monitor.store.set_state(db, "watch:" + profile["id"], state)
    value = collect(service, profile["id"])
    facts = by_id(value)
    assert "stale" in facts["current_temperature"]["text"].lower()
    assert "refresh failed" in facts["current_freshness"]["text"].lower()
    assert "PRIVATE_PROVIDER_BODY" not in json.dumps(value) and "sk-shouldneverleak" not in json.dumps(value)


def test_referenced_temperature_stays_distinct_from_newer_temperature(saved):
    service, monitor, profile, clock = saved
    retain(monitor, profile, clock, temperature=40)
    original = service.message(request_id="old-answer", text="What is the temperature?", orchard_id=profile["id"])["assistant_turn"]
    clock[0] += timedelta(hours=1)
    retain(monitor, profile, clock, temperature=55)
    value = collect(service, profile["id"], "Is that normal?", reply_to=original["id"])
    facts = by_id(value)
    assert "40" in facts["reference_temperature"]["text"] and "55" not in facts["reference_temperature"]["text"]
    assert "55" in facts["current_temperature"]["text"]
    assert "doesn't verify current conditions" in facts["reference_freshness"]["text"]
    assert facts["reference_temperature"]["scope"] == "reference"
    assert facts["reference_temperature"]["evidence_id"] != facts["current_temperature"]["evidence_id"]


def test_reference_without_assessment_never_borrows_current_value(saved):
    service, monitor, profile, clock = saved
    monitor._source_failure(profile["id"], clock[0])
    monitor.flush(profile["id"])
    old_note = monitor.store.messages(profile["id"])[0]
    clock[0] += timedelta(hours=1)
    retain(monitor, profile, clock, temperature=55)
    value = collect(service, profile["id"], reply_to=old_note["id"])
    reference = by_id(value)["reference_temperature"]
    assert "doesn't have a saved temperature assessment" in reference["text"]
    assert "55" not in reference["text"] and reference["evidence_id"] is None


def test_crossfarm_and_unowned_reference_rejected_without_a_model_call(saved):
    service, monitor, profile, clock = saved
    other = monitor.create_orchard("Other", orchard_id="other", crops=["walnut"], lat=39, lon=-120, elevation_ft=90)
    retain(monitor, other, clock, temperature=72)
    monitor.flush(other["id"])
    note = monitor.store.messages(other["id"])[0]
    with pytest.raises(ValueError, match="another farm"):
        context_packet(service, text="Explain it", selected=profile["id"], context={}, reply_to=note["id"])
    evidence = monitor.store.get("watch:other")["latest_evidence_id"]
    with pytest.raises(ValueError, match="does not belong"):
        context_packet(service, text="Explain it", selected=profile["id"],
                       context={"weather_reference": True, "weather_evidence_ids": [evidence]})
    assert not service.state(profile["id"])["pending_requests"]


def test_ambiguous_reference_exposes_missing_choice_instead_of_latest_guess(saved):
    service, _, profile, _ = saved
    value = context_packet(service, text="Which reading?", selected=profile["id"],
                           context={"weather_reference": True, "weather_evidence_ids": ["first", "second"]})
    assert value["references"]["ambiguous"]
    assert "Ask which reading" in by_id(value)["reference_ambiguity"]["text"]
    assert "reference_temperature" not in by_id(value)


def test_publication_order_merges_proactive_notes_and_turns_without_source_clock_order(saved):
    service, monitor, profile, clock = saved
    retain(monitor, profile, clock)
    monitor.flush(profile["id"])
    note = monitor.store.messages(profile["id"])[0]
    service.message(request_id="earlier", text="Thanks!", orchard_id=profile["id"])
    with monitor.store.connect(write=True) as db:
        earlier = json.loads(db.execute("SELECT payload FROM tara_turns WHERE id='earlier:user'").fetchone()[0])
        earlier["published_at"] = "2026-10-24T12:00:00+00:00"
        db.execute("UPDATE tara_turns SET payload=? WHERE id=?", (json.dumps(earlier), earlier["id"]))
        note["published_at"] = "2026-10-24T12:01:00+00:00"
        note["created_at"] = "2025-01-01T00:00:00+00:00"
        db.execute("UPDATE messages SET payload=? WHERE id=?", (json.dumps(note), note["id"]))
    value = collect(service, profile["id"])
    assert [item["id"] for item in value["history"]] == ["earlier:user", note["id"]]
    assert by_id(value)["saved_note_0"]["scope"] == "saved_note"


def test_bounds_and_same_farm_history_keep_recent_complete_repeated_user_text(saved):
    service, monitor, profile, _ = saved
    with monitor.store.connect(write=True) as db:
        for i in range(40):
            item = service._turn(f"history-{i}:user", "user", "word " * 400, HOUR.isoformat())
            service._put(db, profile["id"], item)
        item = service._turn("real-previous:user", "user", "What does that mean?", HOUR.isoformat())
        service._put(db, profile["id"], item)
    value = collect(service, profile["id"])
    assert value["history"][-1]["id"] == "real-previous:user"
    assert len(value["history"]) <= 16 and len(value["facts"]) <= 24
    assert sum(len(row["text"]) for row in value["history"]) <= MAX_HISTORY_CHARS
    assert len(json.dumps(value, ensure_ascii=False).encode()) <= MAX_PACKET_BYTES


def test_current_reserved_turn_is_not_transmitted_twice(saved):
    service, _, profile, _ = saved
    service._reserve("pending", profile["id"], {"text": "Explain this"}, "Explain this")
    value = collect(service, profile["id"], "Explain this")
    assert value["request"]["text"] == "Explain this"
    assert not any(row["id"] == "pending:user" for row in value["history"])


def test_credentials_and_geometry_in_user_dialogue_are_redacted(saved):
    service, _, profile, _ = saved
    value = collect(service, profile["id"], "My pin is 38.5500, -121.8000; Bearer secretvalue and sk-1234567890abcd")
    body = json.dumps(value)
    assert "38.5500" not in body and "-121.8000" not in body and "secretvalue" not in body and "sk-1234567890abcd" not in body
    assert "[private location]" in value["request"]["text"] and "[private credential]" in value["request"]["text"]


def test_implicit_followup_retains_prior_authoritative_citations_separately(saved):
    service, monitor, profile, clock = saved
    retain(monitor, profile, clock, temperature=40)
    first = collect(service, profile["id"], "What is the temperature?")
    cited = by_id(first)["current_temperature"]
    original_evidence = cited["evidence_id"]
    with monitor.store.connect(write=True) as db:
        previous = service._turn("engine-prior:assistant", "assistant", "The orchard estimate was 40°F.", HOUR.isoformat(),
                                 data={"intent": "conversation", "focus": "current_temperature", "source_facts": [cited]})
        service._put(db, profile["id"], previous)
        # A necessary clarification may follow the earlier evidence-bearing answer.
        clarification = service._turn("engine-question:assistant", "assistant", "Do you mean that orchard estimate?", HOUR.isoformat(),
                                      data={"intent": "conversation", "focus": "temperature", "source_facts": []})
        service._put(db, profile["id"], clarification)
    clock[0] += timedelta(hours=1)
    retain(monitor, profile, clock, temperature=55)
    followup = collect(service, profile["id"], "Yes, what does that imply?")
    facts = by_id(followup)
    assert "55" in facts["current_temperature"]["text"]
    assert "40" in facts["prior_answer_0"]["text"] and "55" not in facts["prior_answer_0"]["text"]
    assert facts["prior_answer_0"]["scope"] == "reference"
    assert facts["prior_answer_0"]["evidence_id"] == original_evidence
    assert facts["prior_answer_0"]["source_note_id"] == "engine-prior:assistant"
    assert facts["prior_answer_0"]["source_time"]
