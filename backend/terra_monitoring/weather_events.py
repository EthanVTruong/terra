"""Durable weather discrepancies and variable coverage, independent of chill QC."""
from datetime import timedelta

from .farm_ops.store import utc


def notification_current(event, state):
    facts, kind = event["facts"], event["type"]
    field = facts["variable"]
    if kind.startswith("weather_data_"):
        active = state.get("weather_health_incidents", {}).get(field)
        if kind == "weather_data_recovered":
            return not active and state.get("last_weather_recovery", {}).get("data:" + field) == event["id"]
        return bool(active and event["episode_id"] == active.get("episode_id"))
    key = field + ":" + str(facts["station_id"])
    active = state.get("weather_incidents", {}).get(key)
    if kind == "weather_discrepancy_recovered":
        return active is None and state.get("last_weather_recovery", {}).get(key) == event["id"]
    return bool(active and active.get("active") and active.get("severity") == event["severity"]
                and active.get("last") == state.get("last_observation")
                and event["episode_id"] == active.get("episode_id"))


def decide(profile, state, packet, now, evidence_id, make_event):
    weather = packet.get("weather", {})
    if not isinstance(weather, dict) or not weather.get("enabled"):
        return []
    events = []
    incidents = state.setdefault("weather_incidents", {})
    health = state.setdefault("weather_health_incidents", {})
    clock = utc(packet["query"]["valid_utc"])
    fresh = now - clock <= timedelta(hours=6)
    whole_source_limited = bool(state.get("health_incident")) or packet.get("location_estimate", {}).get("status") not in {None, "ok"}
    findings = {(f["variable"], f["station_id"]): f for f in weather.get("findings", [])} if fresh else {}
    for variable in weather.get("variables", []):
        field = variable["variable"]
        stations = variable.get("stations", [])
        usable = [s for s in stations if s.get("status") == "assessable"] if fresh else []
        available = bool(usable)
        previous_health = health.get(field)
        if not available:
            state.setdefault("last_weather_recovery", {}).pop("data:" + field, None)
            # Nighttime solar has valid zero observations but cannot be compared
            # using a daylight rule. It is not a missing-data incident.
            night = fresh and any(s.get("status") == "low_daylight" for s in stations)
            if not night and previous_health is None:
                health[field] = {"id": clock.isoformat(), "last": None, "good": 0, "suppressed": whole_source_limited}
                opening = make_event(profile, "weather_data_limited", "opening", "moderate", now,
                    {"variable": field, "variable_label": variable["label"], "unit": variable["unit"],
                     "reason": "insufficient_usable_weather_support", "observation_time": clock.isoformat()},
                    field + ":" + clock.isoformat(), evidence_id)
                if not whole_source_limited:
                    events.append(opening)
                health[field]["episode_id"] = opening["episode_id"]
            elif previous_health:
                previous_health.update(last=None, good=0)
        elif previous_health:
            last = previous_health.get("last")
            if clock <= utc(previous_health["id"]):
                previous_health.update(good=0, last=None)
            elif last != clock.isoformat():
                previous_health["good"] = previous_health["good"] + 1 if last and utc(last) == clock - timedelta(hours=1) else 1
                previous_health["last"] = clock.isoformat()
            if previous_health["good"] >= 3:
                recovered = make_event(profile, "weather_data_recovered", "recovery", "info", clock,
                    {"variable": field, "variable_label": variable["label"], "unit": variable["unit"],
                     "observation_time": clock.isoformat()}, field + ":" + previous_health["id"], evidence_id)
                if not previous_health.get("suppressed"):
                    events.append(recovered)
                state.setdefault("last_weather_recovery", {})["data:" + field] = recovered["id"]
                del health[field]
        for station in stations:
            key = field + ":" + str(station["station_id"])
            finding = findings.get((field, station["station_id"]))
            incident = incidents.get(key)
            facts = {"variable": field, "variable_label": variable["label"], "unit": variable["unit"],
                     "station_id": station["station_id"], "observation_time": clock.isoformat(),
                     "observed": station.get("observed"), "expected": station.get("expected"),
                     "departure": station.get("departure"), "policy_version": weather.get("policy_version")}
            normal = fresh and station.get("status") == "assessable" and station.get("level") == "none"
            if not normal:
                state.setdefault("last_weather_recovery", {}).pop(key, None)
            if finding:
                if incident is None:
                    incident = incidents[key] = {"id": finding["start_utc"], "severity": finding["severity"], "good": 0}
                    transition = "opening"
                elif finding["severity"] == "strong" and incident["severity"] != "strong":
                    incident["severity"] = "strong"
                    transition = "escalation"
                else:
                    transition = None
                incident.update(last=clock.isoformat(), good=0, active=True)
                if transition:
                    event = make_event(profile, "weather_discrepancy", transition, finding["severity"], clock,
                        facts, key + ":" + incident["id"], evidence_id)
                    events.append(event)
                    incident["episode_id"] = event["episode_id"]
            elif incident:
                # Absence of a finding is not proof of recovery. Require actual
                # fresh comparisons within the normal band for distinct hours.
                last = incident.get("last")
                if last != clock.isoformat():
                    incident["good"] = (incident.get("good", 0) + 1 if normal and last and utc(last) == clock - timedelta(hours=1)
                                        else 1 if normal else 0)
                    incident["last"] = clock.isoformat()
                elif not normal:
                    incident["good"] = 0
                incident["active"] = False
                if incident["good"] >= 3:
                    recovered = make_event(profile, "weather_discrepancy_recovered", "recovery", "info", clock,
                        facts, key + ":" + incident["id"], evidence_id)
                    events.append(recovered)
                    state.setdefault("last_weather_recovery", {})[key] = recovered["id"]
                    del incidents[key]
    return events
