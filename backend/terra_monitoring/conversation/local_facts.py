"""Short local answers from saved application facts, without inference."""
from __future__ import annotations

from datetime import timedelta
import json
import math

from ..farm_ops.season import METHOD, MIN_COVERAGE, MIN_REFERENCES, PST, eligible
from ..farm_ops.store import utc
from terra_qc.qc import CURRENT_MAX_AGE_HOURS


def profile_answer(intent, state):
    profile = next((p for p in state["orchards"] if p["id"] == state["orchard_id"]), None)
    setup = state["setup"]
    if intent == "profile_crops":
        crops = profile.get("crops", [profile["crop"]]) if profile else setup.get("crops", [])
        variety = profile.get("variety") if profile else setup.get("variety")
        text = "Your saved crops are " + ", ".join(crops) + (f" (variety {variety})" if variety else "") + "." if crops else "You haven't saved your crops yet."
        return text, {"crops": crops, "variety": variety}
    if intent == "profile_location":
        if not profile:
            return "Your farm location hasn't been confirmed yet.", {"location": None}
        geometry = {key: profile[key] for key in ("name", "lat", "lon", "elevation_ft", "location_source")}
        return (f"{profile['name']} is saved at {profile['lat']:.4f}, {profile['lon']:.4f}, {profile['elevation_ft']:g} ft.", geometry)
    if intent == "profile_notifications":
        preference = state["notifications"]
        if preference["capability"] != "browser_open":
            return "Browser alerts aren't supported here; in-chat updates stay available.", preference
        return ("Browser alerts are enabled in your saved preferences and work while Terra is open."
                if preference["enabled"] else "Browser alerts are off; in-chat updates stay available."), preference
    if intent == "profile_monitor":
        status = state["monitoring"]
        return (f"Monitoring is active for {profile['name']}." if status["status"] == "ready" and profile
                else status["reason"]), {"status": status["status"], "reason": status["reason"]}
    if intent == "chill_method":
        return ("I count estimated hours between 32–45°F from November 1 through March 31, using fixed PST.",
                {"method": METHOD, "range_f": [32, 45], "season_start": "November 1", "season_end": "March 31", "timezone": "fixed PST"})
    raise ValueError("Unsupported local fact request")


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _observation_date(stamp):
    # CIMIS hour-ending midnight belongs to the preceding observation date.
    return (utc(stamp).astimezone(PST) - timedelta(seconds=1)).date()


def gap_answer(summary):
    """Use comparison_hours, not leap-day-inclusive display totals."""
    values = {key: summary.get(key) for key in ("comparison_hours", "historical_median_hours", "cutoff", "data_through",
              "method", "estimator_method_version", "complete", "coverage", "comparison_coverage", "reference_count",
              "active_season", "status", "season_start")}
    comparable = (values["method"] == METHOD and values["complete"] is True
                  and _finite(values["comparison_hours"]) and values["comparison_hours"] >= 0
                  and _finite(values["historical_median_hours"]) and values["historical_median_hours"] >= 0
                  and _finite(values["coverage"]) and values["coverage"] >= MIN_COVERAGE
                  and _finite(values["comparison_coverage"]) and values["comparison_coverage"] >= MIN_COVERAGE
                  and isinstance(values["reference_count"], int) and not isinstance(values["reference_count"], bool)
                  and values["reference_count"] >= MIN_REFERENCES)
    if not comparable:
        return "A reliable historical comparison isn't available yet.", values
    if not isinstance(values["cutoff"], str) or not isinstance(values["data_through"], str):
        return "A dated historical comparison isn't available yet.", values
    try:
        cutoff, source = _observation_date(values["cutoff"]), _observation_date(values["data_through"])
        if utc(values["data_through"]) > utc(values["cutoff"]):
            raise ValueError("Source must not follow the comparison cutoff")
    except (ValueError, TypeError):
        return "A dated historical comparison isn't available yet.", values
    difference = values["historical_median_hours"] - values["comparison_hours"]
    date = cutoff.strftime("%b %d, %Y").replace(" 0", " ")
    completed = values["active_season"] is False or values["status"] == "offseason"
    if difference == 0:
        text = (f"For the completed season through {date}, estimated chill matched the historical median." if completed
                else f"Estimated chill matches the historical median through {date}.")
    else:
        direction = "behind" if difference > 0 else "ahead of"
        text = (f"For the completed season through {date}, estimated chill was about {abs(difference):g} hours {direction} the usual pace." if completed
                else f"About {abs(difference):g} estimated chill hours {direction} the usual pace through {date}.")
    if source != cutoff:
        text += (" Latest usable data was from " if completed else " Latest usable data is from ") + source.strftime("%b %d, %Y").replace(" 0", " ") + "."
    return text, {**values, "difference_hours": difference, "source_date": source.isoformat(), "comparison_date": cutoff.isoformat()}


WEATHER_INTENTS = {"temperature", "freshness", "station_findings", "temperature_normal", "weather_history_unavailable"}


def _weather_time(value):
    return utc(value).astimezone(PST).strftime("%b %d, %Y, %I:%M %p PST").replace(" 0", " ")


def _referenced_assessment(monitor, orchard_id, evidence_id):
    """Resolve an immutable assessment through a saved farm-owned reference."""
    with monitor.store.connect() as db:
        owned = db.execute("""SELECT 1 FROM revisions WHERE orchard=? AND json_extract(payload,'$.evidence_id')=?
            UNION ALL SELECT 1 FROM events WHERE orchard=? AND json_extract(payload,'$.evidence_id')=? LIMIT 1""",
            (orchard_id, evidence_id, orchard_id, evidence_id)).fetchone()
        if not owned and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tara_turns'").fetchone():
            owned = db.execute("""SELECT 1 FROM tara_turns WHERE thread=? AND
                (json_extract(payload,'$.data.local_facts.evidence_id')=? OR
                 json_extract(payload,'$.data.local_facts.temperature_evidence_id')=?) LIMIT 1""",
                (orchard_id, evidence_id, evidence_id)).fetchone()
        if not owned:
            raise ValueError("The referenced assessment does not belong to this farm")
        saved = db.execute("SELECT payload FROM evidence WHERE id=?", (evidence_id,)).fetchone()
    if not saved:
        raise ValueError("The referenced assessment is unavailable")
    assessment = json.loads(saved[0])
    # Reuse native mode, pin, unit, clock and reference validation; this does
    # not rerun QC or interpolation and does not read a replacement hour row.
    row = monitor._reading(monitor.store.orchard(orchard_id), assessment, utc(monitor.clock()))
    return assessment, {**row, "evidence_id": evidence_id}


def weather_answer(intent, monitor, orchard_id, *, evidence_id=None, referenced=False):
    """Describe the saved assessment; never fetch, rescore or diagnose a sensor.

    The monitor owns current availability, and ``eligible`` owns stored-reading
    eligibility. Retained temperatures remain explicitly dated when the latest
    assessment or source refresh cannot support a current answer.
    """
    if intent not in WEATHER_INTENTS:
        raise ValueError("Unsupported saved-weather request")
    if intent == "weather_history_unavailable":
        return "Historical weather comparisons aren't available here.", {"available": False, "reason": "historical_weather_comparison_not_supported"}
    if referenced and not evidence_id:
        return "That note doesn't have a saved temperature assessment to inspect.", {"referenced_snapshot": True, "evidence_id": None, "comparison_available": False}
    status = monitor.status(orchard_id)
    watch = monitor.store.get("watch:" + orchard_id, {})
    assessment = {}
    if referenced:
        assessment, row = _referenced_assessment(monitor, orchard_id, evidence_id)
        rows = [row]
        watch = {"last_observation": row["valid_utc"], "method_version": row["method_version"]}
    else:
        evidence_id = watch.get("latest_evidence_id")
        rows = monitor.store.hours(orchard_id)
    if evidence_id and not referenced:
        with monitor.store.connect() as db:
            saved = db.execute("SELECT payload FROM evidence WHERE id=?", (evidence_id,)).fetchone()
        if saved:
            assessment = json.loads(saved[0])
    estimate = assessment.get("location_estimate", {})
    stamp = watch.get("last_observation")
    # A retained estimate from another pin or estimator contract is not a
    # substitute for this farm's current evidence.
    usable = next((row for row in reversed(rows) if eligible(row)
                   and row.get("geometry_key") == status["orchard"]["geometry_key"]
                   and row.get("method_version") == watch.get("method_version")
                   and utc(row["valid_utc"]) <= utc(monitor.clock())), None)
    replay = status["orchard"]["mode"] != "current"
    supported = bool(estimate.get("status") == "ok" and usable and usable["valid_utc"] == stamp)
    available = supported if replay or referenced else bool(status["comparison_available"] and supported)
    stale = not replay and bool(estimate.get("status") == "stale" or usable
        and utc(monitor.clock()) - utc(usable["valid_utc"]) > timedelta(hours=CURRENT_MAX_AGE_HOURS))
    source_error = None if referenced else status.get("source_error")
    checked = assessment.get("query", {}).get("as_of_utc") if referenced else status.get("last_checked")
    findings = assessment.get("station_findings", [])
    health = assessment.get("data_health", [])
    facts = {"data_mode": status["orchard"]["mode"], "evidence_id": evidence_id,
             "observation_time": stamp, "last_checked": checked,
             "referenced_snapshot": referenced, "current_conditions_verified": not referenced and not replay and available,
             "comparison_available": available, "source_error": source_error,
             "stale": stale,
             "temperature_f": usable["temperature_f"] if usable else None,
             "temperature_time": usable["valid_utc"] if usable else None,
             "temperature_evidence_id": (evidence_id if usable["valid_utc"] == stamp else usable.get("evidence_id")) if usable else None,
             "estimate_status": estimate.get("status"), "used_station_ids": estimate.get("used_station_ids", []),
             "station_findings": [{k: finding[k] for k in ("station_id", "severity", "reason") if k in finding}
                                  for finding in findings],
             "data_health": [{k: item[k] for k in ("station_id", "reason", "source_quality") if k in item}
                             for item in health]}
    prefix = "Replay only: " if replay else "Saved assessment: " if referenced else ""
    at = _weather_time(stamp) if stamp else None
    if intent == "temperature":
        if referenced:
            text = (f"The referenced orchard estimate was {usable['temperature_f']:g}°F at {_weather_time(usable['valid_utc'])}."
                    if usable else "An orchard temperature estimate wasn't available in that assessment.")
            return prefix + text, facts
        if usable:
            when = _weather_time(usable["valid_utc"])
            if available and not source_error:
                text = f"Estimated orchard temperature is {usable['temperature_f']:g}°F as of {when}."
            else:
                text = f"The last usable orchard estimate was {usable['temperature_f']:g}°F at {when}. "
                text += ("The latest source refresh failed, and that estimate is stale." if source_error and stale else
                         "The latest source refresh failed." if source_error else
                         "That estimate is stale; current weather support is unavailable." if stale else
                         "Current weather support is unavailable." if not replay else "Weather support is unavailable at the replay hour.")
        else:
            text = "An orchard temperature estimate isn't available yet."
            if at:
                text += f" Weather support was insufficient at {at}."
        return prefix + text, facts
    if intent == "freshness":
        if referenced:
            text = f"That assessment used readings from {at}; it doesn't verify current conditions."
        elif not stamp:
            text = "No weather reading has been saved for this farm yet."
        elif replay:
            text = f"These are saved readings from {at}, not current weather."
        elif source_error:
            text = f"The latest saved reading is from {at}" + (" and is stale" if stale else "") + "; the latest source refresh failed."
        elif available:
            text = f"The latest usable estimate is from {at}, within the monitor's current age limit."
        elif stale:
            text = f"The saved estimate is from {at} and is stale; no current temperature is verified."
        else:
            text = f"The latest saved reading is from {at}; current weather support is unavailable."
        return prefix + text, facts
    ids = sorted({int(finding["station_id"]) for finding in findings if "station_id" in finding})
    if ids:
        label = ("Station " if len(ids) == 1 else "Stations ") + ", ".join(map(str, ids))
        text = f"{label} had a suspected temperature discrepancy" + (f" at {at}" if at else " in the saved assessment") + "; the cause is unconfirmed."
        if not available and not replay:
            text += " This doesn't verify current conditions."
    elif not available:
        text = ("That assessment didn't have enough usable weather support to judge a station discrepancy." if referenced else
                "There isn't enough usable weather support to judge a station discrepancy.")
    elif health:
        warned = sorted({int(item["station_id"]) for item in health if "station_id" in item})
        text = "No statistical station discrepancy was flagged"
        text += ("; source-quality warnings affect station " + ", ".join(map(str, warned)) if warned else "") + "."
    else:
        text = f"No supporting-station discrepancy was flagged as of {at}."
    if intent == "temperature_normal":
        text = "No calibrated normal-temperature range is saved for this farm. " + text
    return prefix + text, facts
