"""Independent, causal CIMIS humidity/wind/solar discrepancy checks.

The temperature detector and location estimator remain separate. These initial
rules describe station departures from simultaneous peers; they do not diagnose
sensor failures, estimate orchard weather, or infer crop risk. Source/QC/coverage
issues are returned in ``data_health`` and never become numerical findings.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from numbers import Integral, Real
from pathlib import Path
from statistics import median
from typing import Any

SCHEMA_VERSION = "terra-weather-assessment-v1"
POLICY_VERSION = "terra-weather-policy-v1"
POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "terra" / "weather-policy-v1.json"
LY_PER_DAY_TO_W_M2 = 41840.0 / 86400.0
MPS_TO_MPH = 3600.0 / 1609.344
WEATHER_FIELDS = {
    "relative_humidity_pct": "HlyRelHum",
    "wind_speed_mph": "HlyWindSpd",
    "solar_radiation_w_m2": "HlySolRad",
}
VARIABLES = tuple(WEATHER_FIELDS)


def _safe(value: Any) -> Any:
    """Retain nonfinite vendor originals as text, never nonstandard JSON numbers."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        if math.isfinite(value):
            return float(value)
        return "NaN" if math.isnan(value) else "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    return str(value)


def _number(value: Any) -> tuple[float | None, str | None]:
    if value is None or isinstance(value, str) and not value.strip():
        return None, "missing_value"
    if isinstance(value, bool) or type(value).__name__ == "bool":
        return None, "invalid_numeric"
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None, "invalid_numeric"
    return (number, None) if math.isfinite(number) else (None, "nonfinite_value")


def _unit(value: Any) -> str:
    return str(value or "").strip().upper().replace(" ", "").replace("²", "2").strip("()")


def _conversion(variable: str, unit: Any) -> float | None:
    spelling = _unit(unit)
    if variable == "relative_humidity_pct":
        return 1.0 if spelling in {"%", "PCT", "PERCENT"} else None
    if variable == "wind_speed_mph":
        if spelling in {"MPH", "MI/H", "MILE/H", "MILES/HOUR"}:
            return 1.0
        if spelling in {"M/S", "MPS", "M/SEC"}:
            return MPS_TO_MPH
        if spelling in {"KM/H", "KPH", "KM/HR"}:
            return 1.0 / 1.609344
        return None
    if variable == "solar_radiation_w_m2":
        if spelling in {"W/M2", "WM-2", "W/M^2"}:
            return 1.0
        if spelling in {"LY/D", "LY/DAY", "LANGLEY/DAY", "LANGLEYS/DAY"}:
            return LY_PER_DAY_TO_W_M2
        return None
    raise ValueError("Unsupported weather variable")


def _physical_reason(variable: str, value: float | None) -> str | None:
    if value is None:
        return None
    if variable == "relative_humidity_pct" and not 0 <= value <= 100:
        return "physical_bounds"
    if variable == "wind_speed_mph" and value < 0:
        return "physical_bounds"
    if variable == "solar_radiation_w_m2" and value < 0:
        return "below_policy_minimum"
    return None


def normalize_cimis_weather(record: dict) -> dict:
    """Decode each measurement's own value, unit and QC without temperature coupling.

    ``record`` is one verified hourly CIMIS record. Station, hour-ending and
    publication visibility verification stays with the provider adapter. No absent
    units or absent QC codes are assumed trustworthy. Raw source fields survive.
    """
    if not isinstance(record, dict):
        raise ValueError("Provide a CIMIS hourly record")
    result = {}
    canonical_units = {"relative_humidity_pct": "%", "wind_speed_mph": "mph", "solar_radiation_w_m2": "W/m²"}
    for variable, key in WEATHER_FIELDS.items():
        item = record.get(key)
        item = item if isinstance(item, dict) else {}
        original, source_unit = item.get("Value"), item.get("Unit")
        flag = item.get("Qc")
        flag = None if flag is None else str(flag).strip().upper()
        value, error = _number(original)
        reasons = [error] if error else []
        factor = _conversion(variable, source_unit)
        if factor is None:
            reasons.append("unknown_unit")
            value = None
        elif value is not None:
            value *= factor
            if not math.isfinite(value):
                value = None
                reasons.append("nonfinite_value")
        physical = _physical_reason(variable, value)
        if physical:
            reasons.append(physical)
        if flag in {"M", "N"}:
            reasons.insert(0, "source_missing")
        elif flag == "P":
            reasons.append("source_pending")
        elif flag is None:
            reasons.append("source_qc_unknown")
        elif flag:
            reasons.append("source_warning")
        reasons = list(dict.fromkeys(reasons))
        quality = "usable" if not reasons else "missing" if "source_missing" in reasons or error == "missing_value" else "flagged" if reasons[0].startswith("source_") else "invalid"
        result.update({variable: value, variable + "_unit": canonical_units[variable],
            variable + "_source_value": _safe(original), variable + "_source_unit": _safe(source_unit),
            variable + "_source_flag": flag, variable + "_quality": quality,
            variable + "_available": not reasons,
            variable + "_availability_reason": reasons[0] if reasons else "available",
            variable + "_availability_reasons": reasons})
    return result


def _time(value: Any, label: str, *, hourly: bool = False) -> datetime:
    if value is None or isinstance(value, (bool, int, float)):
        raise ValueError(f"{label} must be an aware timestamp")
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("naive")
        stamp = stamp.astimezone(timezone.utc)
        if hourly and (stamp.minute or stamp.second or stamp.microsecond):
            raise ValueError("off hour")
        return stamp
    except (ValueError, TypeError, OverflowError):
        raise ValueError(f"{label} must be an aware{' hourly' if hourly else ''} timestamp") from None


def _station_id(value: Any) -> int:
    if isinstance(value, str) and value.isdecimal():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 1:
        raise ValueError("station_id must be a positive integer")
    return int(value)


def _records(value: Any, label: str) -> list[dict]:
    if hasattr(value, "to_dict"):
        value = value.to_dict("records")
    if not isinstance(value, (list, tuple)) or any(not isinstance(row, dict) for row in value):
        raise ValueError(f"{label} must be rows or a DataFrame")
    return list(value)


def load_policy() -> dict:
    """Load the checked-in finite rule policy, never a fitted model or executable file."""
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    if policy.get("version") != POLICY_VERSION or set(policy.get("variables", {})) != set(VARIABLES):
        raise ValueError("Unsupported weather policy")
    return policy


def _distance(a: dict, b: dict) -> float:
    lat1, lat2 = math.radians(a["lat"]), math.radians(b["lat"])
    dlat, dlon = lat2 - lat1, math.radians(b["lon"] - a["lon"])
    chord = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(min(1.0, max(0.0, chord))))


def _stations(rows: list[dict]) -> dict[int, dict]:
    result = {}
    for source in rows:
        sid = _station_id(source.get("station_id"))
        lat, error_lat = _number(source.get("lat"))
        lon, error_lon = _number(source.get("lon"))
        if error_lat or error_lon or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError("Weather stations need finite valid lat/lon")
        if sid in result:
            raise ValueError("Weather stations contain duplicate IDs")
        result[sid] = {**source, "station_id": sid, "lat": lat, "lon": lon}
    return result


def _operating(station: dict, stamp: datetime) -> bool:
    # Hour ending 2400 belongs to the preceding completed local day.
    local_day = (stamp.astimezone(timezone(timedelta(hours=-8))) - timedelta(microseconds=1)).date()
    for key, before in (("connect_date", True), ("disconnect_date", False)):
        value = station.get(key)
        if value is None or str(value) in {"NaT", "nan", "None"}:
            continue
        try:
            boundary = date.fromisoformat(str(value)[:10])
        except (TypeError, ValueError):
            return False
        if (before and local_day < boundary) or (not before and local_day > boundary):
            return False
    return True


def _measurement(row: dict, variable: str) -> dict:
    value, error = _number(row.get(variable))
    reasons = [error] if error else []
    unit = row.get(variable + "_unit")
    expected_unit = {"relative_humidity_pct": "%", "wind_speed_mph": "mph", "solar_radiation_w_m2": "W/m²"}[variable]
    # Canonical values are already normalized. Never apply their raw vendor unit twice.
    if _unit(unit) != _unit(expected_unit):
        reasons.append("unknown_unit" if not unit else "unit_conflict")
    physical = _physical_reason(variable, value)
    if physical:
        reasons.append(physical)
    flag = row.get(variable + "_source_flag")
    flag = None if flag is None or isinstance(flag, Real) and math.isnan(flag) else str(flag).strip().upper()
    if flag in {"M", "N"}:
        reasons.insert(0, "source_missing")
    elif flag == "P":
        reasons.append("source_pending")
    elif flag is None:
        reasons.append("source_qc_unknown")
    elif flag:
        reasons.append("source_warning")
    quality = row.get(variable + "_quality")
    if quality != "usable" and quality is not None:
        reasons.append("source_quality")
    given = row.get(variable + "_availability_reasons")
    if isinstance(given, (list, tuple)):
        supplied = [str(reason) for reason in given if reason != "available"]
        if supplied:
            # A rejected raw NaN, boolean, bad unit or malformed number becomes
            # canonical None; preserve its original reason instead of relabeling
            # that None as a newly missing observation.
            reasons = supplied + [reason for reason in reasons if reason != "missing_value"]
    elif row.get(variable + "_available") is False:
        reasons.append(str(row.get(variable + "_availability_reason") or "source_unavailable"))
    if row.get("timestamp_verified") is not True and type(row.get("timestamp_verified")).__name__ != "bool_":
        reasons.append("timestamp_unverified")
    elif not bool(row.get("timestamp_verified")):
        reasons.append("timestamp_unverified")
    reasons = list(dict.fromkeys(reasons))
    return {"observed": value, "available": not reasons, "reason": reasons[0] if reasons else "available",
        "reasons": reasons, "source_flag": flag, "source_value": _safe(row.get(variable + "_source_value", row.get(variable))),
        "source_unit": _safe(row.get(variable + "_source_unit")), "quality": _safe(quality)}


def assess_weather(observations, stations, valid_time_utc, as_of_utc, *, max_age_hours=6) -> dict:
    """Assess the requested hour with visible simultaneous peers and causal history.

    No interpolation, gap filling, location estimation or training occurs here.
    Each variable is independently eligible. Conflicting duplicates affect that
    variable alone; explicit revisions use only the latest visible publication.
    """
    valid = _time(valid_time_utc, "valid_time_utc", hourly=True)
    as_of = _time(as_of_utc, "as_of_utc")
    if valid > as_of:
        raise ValueError("valid_time_utc must not be later than as_of_utc")
    age, error = _number(max_age_hours)
    if error or age < 0:
        raise ValueError("max_age_hours must be finite and nonnegative")
    policy = load_policy()
    geometry = _stations(_records(stations, "stations"))
    supplied = _records(observations, "observations")
    groups, quarantine = defaultdict(list), []
    discarded = Counter()
    for row in supplied:
        sid = None
        try:
            sid = _station_id(row.get("station_id"))
            stamp = _time(row.get("timestamp"), "observation timestamp", hourly=True)
            if sid not in geometry:
                discarded["unknown_station"] += 1
                continue
            if stamp > as_of or stamp > valid:
                discarded["future_observation"] += 1
                continue
            visibility = row.get("visible_at")
            visible = _time(visibility, "visible_at") if visibility is not None else None
            if visible is not None and visible > as_of:
                discarded["not_yet_visible"] += 1
                continue
        except (TypeError, ValueError):
            quarantine.append({"station_id": sid, "reason": "invalid_identity_or_timestamp", "row": _safe(row)})
            continue
        groups[(sid, stamp)].append((visible, row))
    cleaned, duplicates, conflicts = {}, 0, 0
    for (sid, stamp), items in sorted(groups.items()):
        visible_values = [item[0] for item in items if item[0] is not None]
        latest = max(visible_values) if visible_values else None
        current = [row for visible, row in items if visible == latest]
        duplicates += max(0, len(current) - 1)
        for variable in VARIABLES:
            measures = [_measurement(row, variable) for row in current]
            signatures = {json.dumps({k: value for k, value in item.items() if k not in {"source_value", "source_unit"}}, sort_keys=True, allow_nan=False) for item in measures}
            if len(signatures) > 1:
                measure = {"observed": None, "available": False, "reason": "conflicting_duplicate",
                    "reasons": ["conflicting_duplicate"], "source_flag": None, "quality": "invalid"}
                conflicts += 1
            else:
                measure = measures[0]
            measure["raw_source_rows"] = [_safe(row) for row in current]
            if not _operating(geometry[sid], stamp):
                measure.update(available=False, reason="station_not_operating",
                    reasons=["station_not_operating", *measure["reasons"]])
            cleaned[(sid, stamp, variable)] = measure

    neighbors = {sid: [other for other in sorted(geometry) if other != sid
        and _distance(station, geometry[other]) <= policy["maximum_peer_distance_km"]]
        for sid, station in sorted(geometry.items())}
    needed = policy["sustained_readings"]
    history = [valid - timedelta(hours=i) for i in reversed(range(needed))]
    comparisons = {}
    for stamp in history:
        for variable in VARIABLES:
            variable_policy = policy["variables"][variable]
            for sid in sorted(geometry):
                measure = cleaned.get((sid, stamp, variable))
                current = {"station_id": sid, "observed": measure["observed"] if measure else None,
                    "expected": None, "departure": None, "peer_count": 0, "used_station_ids": [],
                    "status": "unavailable", "reason": measure["reason"] if measure else "missing_observation",
                    "classification": None, "level": "none"}
                if measure and measure["available"]:
                    peers = [(other, cleaned[(other, stamp, variable)]["observed"]) for other in neighbors[sid]
                        if (other, stamp, variable) in cleaned and cleaned[(other, stamp, variable)]["available"]]
                    current["peer_count"], current["used_station_ids"] = len(peers), [other for other, _ in peers]
                    if len(peers) < policy["minimum_peers"]:
                        current["reason"] = "insufficient_peers"
                    else:
                        expected = float(median(value for _, value in peers))
                        departure = current["observed"] - expected
                        current.update(expected=expected, departure=departure)
                        if variable == "solar_radiation_w_m2" and expected < variable_policy["minimum_peer_daylight"]:
                            current.update(status="low_daylight", reason="insufficient_daylight_context")
                        else:
                            level = "strong" if abs(departure) >= variable_policy["extreme_departure"] else "moderate" if abs(departure) >= variable_policy["sustained_departure"] else "none"
                            current.update(status="assessable", reason="peer_comparison", classification="no_alert", level=level)
                comparisons[(sid, stamp, variable)] = current

    variables, findings, health = [], [], []
    stale = (as_of - valid).total_seconds() > age * 3600
    for variable in VARIABLES:
        variable_policy = policy["variables"][variable]
        station_results = []
        for sid in sorted(geometry):
            current = comparisons[(sid, valid, variable)]
            measure = cleaned.get((sid, valid, variable))
            if stale:
                current.update(status="stale", reason="observation_older_than_age_limit", classification=None)
            sequence = [comparisons[(sid, stamp, variable)] for stamp in history]
            same_direction = all(item["departure"] is not None and item["departure"] * current["departure"] > 0 for item in sequence) if current["departure"] is not None else False
            sustained = same_direction and all(item["status"] == "assessable" and item["level"] in {"moderate", "strong"} for item in sequence)
            if current["status"] == "assessable" and (current["level"] == "strong" or sustained):
                current["classification"] = "suspected_discrepancy"
                evidence_rows = sequence if sustained else [current]
                evidence_stamps = history if sustained else [valid]
                evidence = [{"timestamp": stamp.isoformat(), **{key: item[key] for key in
                    ("observed", "expected", "departure", "peer_count", "used_station_ids")}}
                    for stamp, item in zip(evidence_stamps, evidence_rows, strict=True)]
                start = evidence_stamps[0].isoformat()
                identity = json.dumps([POLICY_VERSION, sid, variable, start, valid.isoformat()], separators=(",", ":"))
                findings.append({"id": "weather-" + hashlib.sha256(identity.encode()).hexdigest()[:24],
                    "variable": variable, "label": variable_policy["label"], "station_id": sid,
                    "classification": "suspected_discrepancy", "reason": "station_peer_departure",
                    "severity": "strong" if current["level"] == "strong" else "moderate",
                    "observed": current["observed"], "expected": current["expected"], "departure": current["departure"],
                    "unit": variable_policy["unit"], "departure_unit": variable_policy["departure_unit"],
                    "start_utc": start, "end_utc": valid.isoformat(), "detected_at_utc": valid.isoformat(),
                    "policy_version": POLICY_VERSION, "validation_status": policy["validation_status"],
                    "cause": "unconfirmed", "evidence": evidence})
            if current["status"] in {"unavailable", "stale"}:
                health.append({"variable": variable, "station_id": sid, "valid_utc": valid.isoformat(),
                    "reason": current["reason"], "reasons": ([current["reason"]] if stale or not measure else measure["reasons"] or [current["reason"]]),
                    "source_flag": measure.get("source_flag") if measure else None,
                    "source_value": measure.get("source_value") if measure else None,
                    "source_unit": measure.get("source_unit") if measure else None,
                    "raw_source_rows": measure["raw_source_rows"] if measure else []})
            station_results.append({**current, **{key: measure.get(key) if measure else None
                for key in ("source_flag", "source_value", "source_unit", "quality")}})
        variables.append({"variable": variable, "label": variable_policy["label"],
            "unit": variable_policy["unit"], "departure_unit": variable_policy["departure_unit"],
            "thresholds": {key: value for key, value in variable_policy.items()
                if key in {"sustained_departure", "extreme_departure", "minimum_peer_daylight"}},
            "stations": station_results})
    result = {"schema_version": SCHEMA_VERSION, "policy_version": POLICY_VERSION,
        "validation_status": policy["validation_status"], "valid_time_utc": valid.isoformat(),
        "as_of_utc": as_of.isoformat(), "maximum_age_hours": age,
        "policy": {key: policy[key] for key in ("minimum_peers", "maximum_peer_distance_km", "sustained_readings")},
        "scope": "observed_station_weather_only", "variables": variables,
        "findings": findings, "data_health": health,
        "audit": {"input_rows": len(supplied), "station_hours": len(groups), "duplicate_rows_collapsed": duplicates,
            "conflicting_variable_groups": conflicts, "discarded_rows": dict(sorted(discarded.items())),
            "quarantined": quarantine, "gap_filling": False, "temperature_policy_changed": False}}
    json.dumps(result, allow_nan=False, sort_keys=True)
    return result
