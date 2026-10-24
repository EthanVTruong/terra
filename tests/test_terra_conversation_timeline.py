"""Publication order is independent of replay clocks and immutable retries."""
from datetime import datetime, timedelta, timezone
import json

import pytest

from terra_monitoring.conversation import ConversationService
from terra_monitoring.farm_ops.service import Monitor
from terra_monitoring.farm_ops.store import Store, utc
from terra_monitoring.farm_ops.writer import Writer
from test_farm_chill_ops import HOUR, packet


@pytest.fixture
def timeline(tmp_path, monkeypatch):
    wall = [datetime(2026, 10, 24, 19, tzinfo=timezone.utc)]
    def clock(value=None):
        if value is not None:
            return utc(value)
        wall[0] += timedelta(milliseconds=1)
        return wall[0]
    monkeypatch.setattr("terra_monitoring.farm_ops.store.utc", clock)
    monkeypatch.setattr("terra_monitoring.conversation.service.utc", clock)
    monitor = Monitor(Store(tmp_path / "timeline.sqlite3"), writer=Writer(key="", model=""), clock=lambda: HOUR)
    return ConversationService(monitor), monitor, wall


def setup(service):
    service.message(request_id="crop", text="Peaches")
    result = service.action(request_id="pin", action="location", location={"name": "Peach farm", "lat": 38.55,
        "lon": -121.8, "elevation_ft": 65.0, "source": "manual", "confirmed": True})
    orchard = result["state"]["orchard_id"]
    choice = service.action(request_id="notifications", action="notifications", notifications={"enabled": False, "permission": "default"})
    return orchard, choice["assistant_turn"]


def apply(monitor, orchard, **options):
    monitor.apply(orchard, {"latest": packet(monitor.store.orchard(orchard), **options)}, now=HOUR)
    monitor.flush(orchard, now=HOUR)


def test_same_model_hour_keeps_setup_welcome_question_and_coverage_in_publication_order(timeline):
    service, monitor, _ = timeline
    orchard, setup_reply = setup(service)
    apply(monitor, orchard)
    welcome = monitor.store.messages(orchard)[0]
    question = service.message(request_id="temperature", text="What is the temperature?", orchard_id=orchard)["assistant_turn"]
    apply(monitor, orchard, status="insufficient_data")
    coverage = next(note for note in monitor.store.messages(orchard) if note["id"] != welcome["id"])
    visible = [setup_reply, welcome, question, coverage]
    assert len({item["created_at"] for item in visible}) == 1
    assert [item["id"] for item in sorted(visible, key=lambda item: utc(item["published_at"]))] == [item["id"] for item in visible]
    assert len({item["published_at"] for item in visible}) == 4
    assert all(utc(item["published_at"]).month == 10 for item in visible)
    assert all(utc(item["created_at"]) == HOUR for item in visible)
    for note in (welcome, coverage):
        detail = monitor.store.details(orchard, note["id"])
        assert all(event["occurred_at"] == HOUR.isoformat() for event in detail["events"])
        assert all(assessment["query"]["valid_utc"] == HOUR.isoformat() for assessment in detail["assessments"])


def test_retries_keep_original_publication_metadata_and_legacy_payloads_are_not_backfilled(timeline):
    service, monitor, wall = timeline
    orchard, _ = setup(service)
    apply(monitor, orchard)
    note = monitor.store.messages(orchard)[0]
    wall[0] += timedelta(days=1)
    duplicate = monitor.store.publish(orchard, note["llm_input"], {"text": "must not replace saved text", "provider": "template", "outcome": "retry"},
                                      note["event_ids"], HOUR)
    assert duplicate == note
    first = service.message(request_id="question", text="What is the temperature?", orchard_id=orchard)
    wall[0] += timedelta(days=1)
    again = service.message(request_id="question", text="What is the temperature?", orchard_id=orchard)
    assert again["assistant_turn"] == first["assistant_turn"] and again["user_turn"] == first["user_turn"]
    legacy = {key: value for key, value in note.items() if key != "published_at"}
    with monitor.store.connect(write=True) as db:
        original_bytes = json.dumps(legacy, separators=(",", ":"))
        db.execute("UPDATE messages SET payload=? WHERE id=?", (original_bytes, note["id"]))
    replayed = monitor.store.publish(orchard, note["llm_input"], {"text": "must not backfill", "provider": "template", "outcome": "retry"}, note["event_ids"], HOUR)
    assert replayed == legacy and "published_at" not in replayed
    with monitor.store.connect() as db:
        assert db.execute("SELECT payload FROM messages WHERE id=?", (note["id"],)).fetchone()[0] == original_bytes
    legacy_turn = {key: value for key, value in first["assistant_turn"].items() if key != "published_at"}
    with monitor.store.connect(write=True) as db:
        original_turn_bytes = json.dumps(legacy_turn, separators=(",", ":"))
        db.execute("UPDATE tara_turns SET payload=? WHERE id=?", (original_turn_bytes, legacy_turn["id"]))
        replacement = {**legacy_turn, "text": "must not replace", "published_at": wall[0].isoformat()}
        service._put(db, orchard, replacement)
        assert replacement == legacy_turn
        assert db.execute("SELECT payload FROM tara_turns WHERE id=?", (legacy_turn["id"],)).fetchone()[0] == original_turn_bytes


def test_newer_proactive_note_is_default_context_but_explicit_old_reference_and_now_still_win(timeline):
    service, monitor, _ = timeline
    orchard, _ = setup(service)
    apply(monitor, orchard)
    original = service.message(request_id="temperature", text="What is the temperature?", orchard_id=orchard)["assistant_turn"]
    assert service._context(orchard, None)["intent"] == "temperature"
    apply(monitor, orchard, status="insufficient_data")
    coverage = monitor.store.messages(orchard)[0]
    default = service._context(orchard, None)
    assert default["intent"] == "explain_alert" and default["event_ids"] == coverage["event_ids"]
    explicit = service.message(request_id="old-normal", text="Is that normal?", orchard_id=orchard, reply_to=original["id"])["assistant_turn"]
    assert explicit["data"]["intent"] == "temperature_normal"
    assert explicit["data"]["local_facts"]["evidence_id"] == original["data"]["local_facts"]["evidence_id"]
    current = service.message(request_id="now", text="What is the temperature now?", orchard_id=orchard)["assistant_turn"]
    assert not current["data"]["local_facts"]["comparison_available"]
    assert not current["data"]["local_facts"]["referenced_snapshot"]


def test_missing_or_invalid_publication_metadata_does_not_guess_over_typed_context(timeline):
    service, monitor, _ = timeline
    orchard, _ = setup(service)
    apply(monitor, orchard)
    service.message(request_id="temperature", text="What is the temperature?", orchard_id=orchard)
    apply(monitor, orchard, status="insufficient_data")
    note = monitor.store.messages(orchard)[0]
    with monitor.store.connect(write=True) as db:
        db.execute("UPDATE messages SET payload=? WHERE id=?", (json.dumps({**note, "published_at": "invalid"}), note["id"]))
    assert service._context(orchard, None)["intent"] == "temperature"


def test_new_turn_publication_uses_actual_utc_wall_clock():
    before = utc()
    turn = ConversationService._turn("turn", "assistant", "Saved", HOUR.isoformat())
    after = utc()
    assert before <= utc(turn["published_at"]) <= after
    assert turn["created_at"] == HOUR.isoformat()
