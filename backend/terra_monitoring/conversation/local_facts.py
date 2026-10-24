"""Short local answers from saved application facts, without inference."""
from __future__ import annotations

from datetime import timedelta
import math

from ..farm_ops.season import METHOD, MIN_COVERAGE, MIN_REFERENCES, PST
from ..farm_ops.store import utc


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
