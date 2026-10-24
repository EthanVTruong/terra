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


VARIABLE_INTENTS = {"humidity": "relative_humidity_pct", "wind": "wind_speed_mph", "solar_radiation": "solar_radiation_w_m2"}
WEATHER_INTENTS = {"temperature", "freshness", "station_findings", "temperature_normal", "weather_history_unavailable", "weather_variables", *VARIABLE_INTENTS}

_VARIABLE_LABELS = {"relative_humidity_pct": ("Relative humidity", "%"), "wind_speed_mph": ("Wind speed", "mph"),
                    "solar_radiation_w_m2": ("Solar radiation", "W/m²")}
_HEALTH_LABELS = {"source_warning": "a source-quality warning", "source_qc_unknown": "unknown source quality",
                  "missing_value": "a missing reading", "missing_observation": "a missing reading",
                  "missing_target_observation": "a missing reading", "stale": "a stale reading",
                  "insufficient_neighbors": "insufficient usable neighboring readings",
                  "low_daylight": "insufficient daylight for a solar comparison"}


def _variable_weather_answer(intent, weather, facts, *, source_error=False, replay=False, referenced=False):
    """Use station observations only; never relabel them as orchard estimates.

    Labels and status prose are application-owned. Source warnings are never
    treated as numerical discrepancies, and missing observations stay absent.
    """
    weather = weather if isinstance(weather, dict) else {}
    selected = [VARIABLE_INTENTS[intent]] if intent in VARIABLE_INTENTS else list(_VARIABLE_LABELS)
    facts = {**facts, "weather_schema_version": weather.get("schema_version"),
             "weather_policy_version": weather.get("policy_version"), "weather_variables": [],
             "weather_findings": [], "weather_data_health": [], "current_conditions_verified": False}
    stamp = weather.get("valid_time_utc")
    try:
        at = _weather_time(stamp) if stamp else None
    except (ValueError, TypeError, OverflowError):
        at = None
    if weather.get("schema_version") != "terra-weather-assessment-v1" or not at:
        labels = ", ".join(_VARIABLE_LABELS[key][0].lower() for key in selected)
        return f"Saved {labels} measurements aren't available for this farm yet.", facts
    phrases = []
    variables = weather.get("variables", [])
    findings = [item for item in weather.get("findings", []) if isinstance(item, dict)
                and item.get("variable") in selected]
    health = [item for item in weather.get("data_health", []) if isinstance(item, dict)
              and item.get("variable") in selected]
    for key in selected:
        label, unit = _VARIABLE_LABELS[key]
        variable = next((item for item in variables if isinstance(item, dict) and item.get("variable") == key), {})
        stations = []
        for row in variable.get("stations", []):
            if (not isinstance(row, dict) or not isinstance(row.get("station_id"), int)
                    or isinstance(row["station_id"], bool) or row["station_id"] < 1):
                continue
            stations.append({field: row.get(field) for field in ("station_id", "observed", "expected", "departure", "peer_count", "status", "reason", "classification")})
        departure_unit = "percentage points" if key == "relative_humidity_pct" else unit
        facts["weather_variables"].append({"variable": key, "unit": unit, "departure_unit": departure_unit, "stations": stations})
        readings = []
        for row in stations[:5]:
            value, state, reason = row.get("observed"), row.get("status"), row.get("reason")
            # Unusable values retain their raw evidence elsewhere; no warning
            # value is presented here as an accepted weather observation.
            usable = (_finite(value) and value >= 0 and (key != "relative_humidity_pct" or value <= 100)
                      and state in {"assessable", "low_daylight"})
            if usable:
                detail = f"{value:g}{unit}" if unit == "%" else f"{value:g} {unit}"
                if row.get("classification") == "suspected_discrepancy":
                    detail += " (suspected station discrepancy)"
                    if _finite(row.get("expected")) and _finite(row.get("departure")):
                        expected = f"{row['expected']:g}{unit}" if unit == "%" else f"{row['expected']:g} {unit}"
                        detail += f", peer reference {expected}, departure {row['departure']:+g} {departure_unit}"
                elif state == "low_daylight":
                    detail += " (solar comparison withheld in low daylight)"
            else:
                detail = "unavailable"
                if reason:
                    detail += " (" + _HEALTH_LABELS.get(reason, "a data-quality or comparison limitation") + ")"
            readings.append(f"station {row['station_id']}: {detail}")
        phrases.append(label + (" — " + "; ".join(readings) + "." if readings else " measurements are unavailable."))
    fields = ("variable", "station_id", "classification", "severity", "observed", "expected", "departure", "unit", "departure_unit", "start_utc", "end_utc", "detected_at_utc")
    facts["weather_findings"] = [{k: item[k] for k in fields if k in item} for item in findings]
    facts["weather_data_health"] = [{k: item[k] for k in ("variable", "station_id", "valid_utc", "reason", "source_flag") if k in item} for item in health]
    text = " ".join(phrases) + f" Readings from {at}."
    if findings:
        text += " These are provisional station-to-neighbor discrepancies; the cause is unconfirmed."
    elif any(row.get("status") == "assessable" for item in facts["weather_variables"] for row in item["stations"]):
        text += " No discrepancy was flagged by the provisional station comparison."
    if health:
        text += " Missing readings and source-quality issues are recorded separately from discrepancies."
    if source_error:
        text += " The latest source refresh failed; these saved readings don't verify current conditions."
    elif facts.get("stale") and not replay and not referenced:
        text += " These saved readings are stale; current conditions aren't verified."
    return text, facts


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
    weather_packet = assessment.get("weather")
    weather_packet = weather_packet if isinstance(weather_packet, dict) else {}
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
    if intent in VARIABLE_INTENTS or intent == "weather_variables":
        text, facts = _variable_weather_answer(intent, weather_packet, facts,
            source_error=bool(source_error), replay=replay, referenced=referenced)
        return prefix + text, facts
    if intent == "station_findings" and weather_packet.get("schema_version") == "terra-weather-assessment-v1":
        weather_flags = [item for item in weather_packet.get("findings", []) if isinstance(item, dict)
                         and item.get("variable") in _VARIABLE_LABELS and isinstance(item.get("station_id"), int)]
        if weather_flags:
            details = [f"station {item['station_id']} ({_VARIABLE_LABELS[item['variable']][0].lower()})" for item in weather_flags]
            details.extend(f"station {item['station_id']} (temperature)" for item in findings if isinstance(item.get("station_id"), int))
            facts["weather_findings"] = [{k: item[k] for k in ("variable", "station_id", "severity", "detected_at_utc") if k in item} for item in weather_flags]
            text = "Suspected discrepancies were flagged at " + ", ".join(dict.fromkeys(details)) + (f" at {at}." if at else ".")
            text += " The additional-variable rules are provisional station comparisons; the cause is unconfirmed."
            if not available and not replay:
                text += " Current conditions aren't verified."
            return prefix + text, facts
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
