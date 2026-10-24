"""App/profile questions and comparable arithmetic use real local facts only."""
from datetime import datetime, timedelta

import pytest

from terra_monitoring.conversation.local_facts import gap_answer
from terra_monitoring.farm_ops.season import METHOD, PST
from test_farm_chill_ops import HOUR
from test_terra_conversation import conversation, setup_farm


@pytest.mark.parametrize("text,intent,fragment", [
    ("What crops am I growing?", "profile_crops", "peach"),
    ("Where is my farm saved?", "profile_location", "38.5500"),
    ("Are browser alerts enabled?", "profile_notifications", "off"),
    ("Are you monitoring?", "profile_monitor", "Waiting"),
    ("How do you count chill hours?", "chill_method", "32–45°F"),
    ("When does chill counting start?", "chill_method", "November 1"),
])
def test_app_and_method_questions_answer_locally(conversation, text, intent, fragment):
    service, _, writer = conversation
    orchard = setup_farm(service)
    response = service.message(request_id="local-question", text=text, orchard_id=orchard)
    assert response["assistant_turn"]["data"]["intent"] == intent
    assert fragment in response["assistant_turn"]["text"]
    assert not writer.packets


def test_saved_notification_preference_and_actual_worker_are_distinct(conversation):
    service, monitor, writer = conversation
    orchard = setup_farm(service)
    service.action(request_id="enable", action="notifications", notifications={"enabled": True, "permission": "granted"})
    answer = service.message(request_id="enabled-status", text="Are browser alerts enabled?", orchard_id=orchard)
    assert "saved preferences" in answer["assistant_turn"]["text"] and "while Terra is open" in answer["assistant_turn"]["text"]
    assert service.state(orchard)["monitoring"]["status"] == "pending"
    assert not writer.packets


def test_ambiguous_ellipsis_needs_an_actual_prior_factual_reply(conversation):
    service, _, writer = conversation
    orchard = setup_farm(service)
    result = service.message(request_id="ambiguous", text="What does that mean?", orchard_id=orchard)
    assert result["assistant_turn"]["text"] == "Which farm note or monitored condition do you mean?"
    assert not writer.packets


@pytest.mark.parametrize("question,expected", [("Are you monitoring?", "profile_monitor"), ("How do you count chill hours?", "chill_method")])
def test_followups_keep_the_latest_actual_local_answer_context(conversation, question, expected):
    service, _, writer = conversation
    orchard = setup_farm(service)
    first = service.message(request_id="context-fact", text=question, orchard_id=orchard)
    followup = service.message(request_id="context-followup", text="What does that mean?", orchard_id=orchard)
    assert followup["assistant_turn"]["data"]["intent"] == expected
    assert followup["assistant_turn"]["data"]["local_facts"] == first["assistant_turn"]["data"]["local_facts"]
    assert not writer.packets


def test_gap_uses_existing_comparable_summary_and_keeps_actual_source_date(conversation):
    service, monitor, writer = conversation
    orchard = setup_farm(service)
    profile = monitor.store.orchard(orchard)
    method = "synthetic-matched-estimator"
    cutoff = datetime(2025, 11, 11, tzinfo=PST).astimezone(HOUR.tzinfo)
    monitor.clock = lambda: cutoff
    for year in range(2020, 2026):
        start = datetime(year, 11, 1, tzinfo=PST).astimezone(HOUR.tzinfo)
        with monitor.store.connect(write=True) as db:
            for hour in range(1, 241):
                row = {"valid_utc": (start + timedelta(hours=hour)).isoformat(), "temperature_f": 60.0 if hour <= (60 if year == 2025 else 20) else 40.0,
                       "usable": True, "method_version": method, "geometry_key": profile["geometry_key"], "qc_reference": "synthetic", "status": "ok"}
                monitor.store.put_hour(db, orchard, row)
    with monitor.store.connect(write=True) as db:
        monitor.store.set_state(db, "watch:" + orchard, {"method_version": method, "last_observation": cutoff.isoformat(), "comparison_available": True})
    summary = monitor.status(orchard)["summary"]
    assert summary["comparison_hours"] == 180 and summary["historical_median_hours"] == 220 and summary["complete"]
    result = service.message(request_id="actual-gap", text="How far behind are we?", orchard_id=orchard)
    answer = result["assistant_turn"]
    assert "40 estimated chill hours behind" in answer["text"] and "Nov 10" in answer["text"]
    assert answer["data"]["local_facts"]["difference_hours"] == 40
    detail = monitor.store.details(orchard, answer["data"]["message_id"])
    assert detail["message"]["llm_input"]["calculation"]["data_through"] == cutoff.isoformat()
    assert not writer.packets


@pytest.mark.parametrize("changes", [{"complete": False}, {"historical_median_hours": None}, {"comparison_hours": None},
    {"comparison_coverage": 0.5}, {"reference_count": 4}, {"cutoff": None}, {"method": "other-method"}])
def test_incomparable_or_undated_gap_never_invents_example_numbers(changes):
    summary = {"method": METHOD, "comparison_hours": 180, "hours": 204, "historical_median_hours": 220,
               "cutoff": "2026-03-02T08:00:00+00:00", "data_through": "2026-03-02T08:00:00+00:00", "complete": True,
               "coverage": 1.0, "comparison_coverage": 1.0, "reference_count": 5}
    text, facts = gap_answer({**summary, **changes})
    assert "available yet" in text and "40" not in text and "220" not in text
    assert "difference_hours" not in facts


def test_gap_uses_leap_adjusted_comparison_hours_not_display_total():
    summary = {"method": METHOD, "comparison_hours": 180, "hours": 204, "historical_median_hours": 220,
               "cutoff": "2024-03-02T08:00:00+00:00", "data_through": "2024-03-02T08:00:00+00:00", "complete": True,
               "coverage": 1.0, "comparison_coverage": 1.0, "reference_count": 5}
    text, facts = gap_answer(summary)
    assert "40 estimated chill hours" in text and facts["difference_hours"] == 40


def test_completed_season_gap_retains_actual_year_and_past_season_status():
    summary = {"method": METHOD, "comparison_hours": 180, "historical_median_hours": 220,
               "cutoff": "2026-04-01T08:00:00+00:00", "data_through": "2026-04-01T08:00:00+00:00", "complete": True,
               "coverage": 1.0, "comparison_coverage": 1.0, "reference_count": 5,
               "active_season": False, "status": "offseason", "season_start": "2025-11-01"}
    text, facts = gap_answer(summary)
    assert "completed season through Mar 31, 2026" in text and "was about 40 hours behind" in text
    assert facts["active_season"] is False and facts["status"] == "offseason" and facts["season_start"] == "2025-11-01"


def test_stale_comparison_across_new_year_preserves_both_actual_source_years():
    summary = {"method": METHOD, "comparison_hours": 180, "historical_median_hours": 220,
               "cutoff": "2027-01-02T08:00:00+00:00", "data_through": "2026-12-31T20:00:00+00:00", "complete": True,
               "coverage": 0.98, "comparison_coverage": 0.98, "reference_count": 5,
               "active_season": True, "status": "below20", "season_start": "2026-11-01"}
    text, facts = gap_answer(summary)
    assert "through Jan 1, 2027" in text and "Latest usable data is from Dec 31, 2026" in text
    assert facts["source_date"] == "2026-12-31" and facts["comparison_date"] == "2027-01-01"
