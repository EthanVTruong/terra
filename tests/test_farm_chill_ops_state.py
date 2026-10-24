"""Farm persistence regressions using disposable journals and no external services."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, Lock

import pytest

from terra_monitoring.farm_ops.service import Monitor
from terra_monitoring.farm_ops.store import QuestionInProgress, Store, encoded
from terra_monitoring.farm_ops.writer import Writer, fallback
from test_farm_chill_ops import HOUR, packet


@pytest.fixture
def watch(tmp_path):
    clock = [HOUR]
    monitor = Monitor(Store(tmp_path / "watch.sqlite3"), writer=Writer(key="", model=""), clock=lambda: clock[0])
    profile = monitor.create_orchard(orchard_id="west")
    return monitor, profile, clock


def ingest(monitor, profile, clock, hours, *, missing=(), method="synthetic-method-v1", latest=None):
    clock[0] = HOUR + timedelta(hours=max(hours))
    history = []
    for hour in hours:
        value = packet(profile, HOUR + timedelta(hours=hour), status="insufficient_data" if hour in missing else "ok")
        value["query"]["as_of_utc"] = clock[0].isoformat()
        value["location_estimate"]["method_version"] = method
        history.append(value)
    chosen = history[-1] if latest is None else next(p for p in history if p["query"]["valid_utc"] == (HOUR + timedelta(hours=latest)).isoformat())
    monitor.apply(profile["id"], {"latest": chosen, "history": history}, now=clock[0])
    monitor.flush(profile["id"], now=clock[0])


def health(monitor, profile):
    return monitor.store.get("watch:" + profile["id"])


def recoveries(monitor, profile):
    return [e for e in monitor.store.events(profile["id"]) if e["type"] == "monitoring_recovered"]


def test_restart_recovers_from_three_persisted_catchup_hours_once(watch):
    monitor, profile, clock = watch
    ingest(monitor, profile, clock, [0], missing=[0])
    restarted = Monitor(Store(monitor.store.path), writer=Writer(key="", model=""), clock=lambda: clock[0])
    ingest(restarted, profile, clock, [1, 2, 3])
    assert health(restarted, profile)["health_incident"] is None
    assert health(restarted, profile)["health_good_count"] == 3
    assert len(recoveries(restarted, profile)) == 1
    ingest(restarted, profile, clock, [1, 2, 3])
    assert len(recoveries(restarted, profile)) == 1


def test_catchup_gap_breaks_recovery_streak(watch):
    monitor, profile, clock = watch
    ingest(monitor, profile, clock, [0], missing=[0])
    ingest(monitor, profile, clock, [1, 3, 4])
    assert health(monitor, profile)["health_good_count"] == 2 and not recoveries(monitor, profile)
    ingest(monitor, profile, clock, [3, 4, 5])
    assert len(recoveries(monitor, profile)) == 1


def test_revised_missing_hour_breaks_previously_counted_run(watch):
    monitor, profile, clock = watch
    ingest(monitor, profile, clock, [0], missing=[0])
    ingest(monitor, profile, clock, [1, 2])
    assert health(monitor, profile)["health_good_count"] == 2
    ingest(monitor, profile, clock, [2, 3], missing=[2])
    assert health(monitor, profile)["health_good_count"] == 1 and not recoveries(monitor, profile)
    ingest(monitor, profile, clock, [3, 4])
    assert not recoveries(monitor, profile)
    ingest(monitor, profile, clock, [3, 4, 5])
    assert len(recoveries(monitor, profile)) == 1


def test_revision_is_rechecked_even_when_latest_hour_is_unchanged(watch):
    monitor, profile, clock = watch
    ingest(monitor, profile, clock, [0], missing=[0])
    ingest(monitor, profile, clock, [1, 2])
    ingest(monitor, profile, clock, [1, 2], missing=[1])
    assert health(monitor, profile)["health_good_count"] == 1 and not recoveries(monitor, profile)


def test_old_method_readings_cannot_complete_new_method_recovery(watch):
    monitor, profile, clock = watch
    ingest(monitor, profile, clock, [0], missing=[0])
    ingest(monitor, profile, clock, [1, 2])
    ingest(monitor, profile, clock, [3], method="synthetic-method-v2")
    assert health(monitor, profile)["health_good_count"] == 1 and not recoveries(monitor, profile)
    ingest(monitor, profile, clock, [4], method="synthetic-method-v2")
    assert not recoveries(monitor, profile)
    ingest(monitor, profile, clock, [5], method="synthetic-method-v2")
    assert len(recoveries(monitor, profile)) == 1


def test_recomputed_same_method_catchup_can_complete_recovery(watch):
    monitor, profile, clock = watch
    ingest(monitor, profile, clock, [0], missing=[0])
    ingest(monitor, profile, clock, [1, 2])
    ingest(monitor, profile, clock, [1, 2, 3], method="synthetic-method-v2")
    assert len(recoveries(monitor, profile)) == 1


def test_only_causal_prefix_through_latest_can_supply_recovery(watch):
    monitor, profile, clock = watch
    ingest(monitor, profile, clock, [0], missing=[0])
    ingest(monitor, profile, clock, [1, 2, 3], latest=2)
    assert health(monitor, profile)["health_good_count"] == 2 and not recoveries(monitor, profile)


def test_source_failure_uses_last_unavailable_hour_not_wall_clock_boundary(watch):
    monitor, profile, clock = watch
    ingest(monitor, profile, clock, [0])
    clock[0] = HOUR + timedelta(hours=7)
    monitor._source_failure(profile["id"], clock[0])
    monitor.flush(profile["id"])
    assert health(monitor, profile)["health_incident"]
    ingest(monitor, profile, clock, [6, 7, 8])
    assert len(recoveries(monitor, profile)) == 1


def test_legacy_watch_without_outage_boundary_preserves_recovery_progress(watch):
    monitor, profile, clock = watch
    ingest(monitor, profile, clock, [0], missing=[0])
    ingest(monitor, profile, clock, [1, 2])
    with monitor.store.connect(write=True) as db:
        state = monitor.store.state(db, "watch:" + profile["id"])
        state.pop("health_unavailable_through")
        monitor.store.set_state(db, "watch:" + profile["id"], state)
    restarted = Monitor(Store(monitor.store.path), writer=Writer(key="", model=""), clock=lambda: clock[0])
    ingest(restarted, profile, clock, [3])
    assert len(recoveries(restarted, profile)) == 1


class BlockingWriter:
    def __init__(self):
        self.entered, self.release, self.lock = Event(), Event(), Lock()
        self.calls = 0

    def write(self, value):
        with self.lock:
            self.calls += 1
        self.entered.set()
        if not self.release.wait(5):
            raise RuntimeError("Audit fixture timed out")
        return {"text": fallback(value), "provider": "template", "outcome": "test"}


def test_same_request_concurrent_across_monitors_reserves_one_generation(watch):
    monitor, profile, clock = watch
    writer = BlockingWriter()
    monitor.writer = writer
    other = Monitor(Store(monitor.store.path), writer=writer, clock=lambda: clock[0])
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(monitor.ask, profile["id"], "How much chill?", request_id="same")
        try:
            assert writer.entered.wait(3)
            with pytest.raises(QuestionInProgress):
                other.ask(profile["id"], "How much chill?", request_id="same")
            # Generating a message does not hold SQLite's writer transaction.
            with other.store.connect(write=True) as db:
                other.store.set_state(db, "audit-heartbeat", {"alive": True})
        finally:
            writer.release.set()
        saved = first.result()
    assert writer.calls == 1
    assert other.ask(profile["id"], "How much chill?", request_id="same") == saved
    assert len(other.store.messages(profile["id"])) == 1 and writer.calls == 1
    assert other.store.get("question-request:" + saved["id"]) is None


def test_pending_request_identity_rejects_different_question_before_generation(watch):
    monitor, profile, clock = watch
    writer = BlockingWriter()
    monitor.writer = writer
    other = Monitor(Store(monitor.store.path), writer=writer, clock=lambda: clock[0])
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(monitor.ask, profile["id"], "How much chill?", request_id="same")
        try:
            assert writer.entered.wait(3)
            with pytest.raises(ValueError, match="another question"):
                other.ask(profile["id"], "What is the history?", request_id="same")
        finally:
            writer.release.set()
        first.result()
    assert writer.calls == 1


def test_unfinished_generation_reservation_survives_restart_without_blind_retry(watch):
    monitor, profile, clock = watch
    class UncertainWriter:
        def write(self, value):
            raise RuntimeError("Outcome unknown")
    monitor.writer = UncertainWriter()
    with pytest.raises(RuntimeError):
        monitor.ask(profile["id"], "How much chill?", request_id="same")
    restarted = Monitor(Store(monitor.store.path), writer=Writer(key="", model=""), clock=lambda: clock[0])
    with pytest.raises(QuestionInProgress):
        restarted.ask(profile["id"], "How much chill?", request_id="same")
    with pytest.raises(ValueError, match="another question"):
        restarted.ask(profile["id"], "Another question", request_id="same")
    assert restarted.store.messages(profile["id"]) == []


def test_old_message_details_uses_direct_owned_lookup_and_only_related_events(watch, monkeypatch):
    monitor, profile, clock = watch
    other = monitor.create_orchard(orchard_id="other")
    store = monitor.store
    with store.connect(write=True) as db:
        evidence_id = store.retain(db, {"fixture": "associated evidence"})
        for event_id, orchard_id in [("selected", profile["id"]), ("unrelated", profile["id"]), ("another-orchard", other["id"])]:
            store.add_event(db, orchard_id, {"id": event_id, "evidence_id": evidence_id}, clock[0])
        for number in range(1001):
            key = "message-" + str(number)
            body = {"id": key, "orchard_id": profile["id"], "event_ids": ["selected", "another-orchard"] if number == 0 else []}
            db.execute("INSERT INTO messages VALUES(?,?,?,?,?)", (key, profile["id"], "answer", clock[0].isoformat(), encoded(body)))
    monkeypatch.setattr(store, "messages", lambda *a, **kw: pytest.fail("Details must not scan the inbox"))
    monkeypatch.setattr(store, "events", lambda *a, **kw: pytest.fail("Details must not scan all events"))
    details = store.details(profile["id"], "message-0")
    assert details["message"]["id"] == "message-0"
    assert [event["id"] for event in details["events"]] == ["selected"]
    assert details["assessments"] == [{"fixture": "associated evidence"}]
    with pytest.raises(KeyError):
        store.details(other["id"], "message-0")


def test_notification_related_events_do_not_load_the_whole_event_history(watch, monkeypatch):
    monitor, profile, clock = watch
    monitor.set_sms_opt_in(profile["id"], True)
    ingest(monitor, profile, clock, [0], missing=[0])
    monkeypatch.setattr(monitor.store, "events", lambda *a, **kw: pytest.fail("Delivery must use the message's event identities"))
    envelope = monitor.notifications_pending()[0]
    assert envelope["category"] == "data_health" and envelope["eligibility"]["eligible"]
