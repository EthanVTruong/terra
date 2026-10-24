"""One read-only, finite-JSON assessment; no ingestion, repair, fitting or delivery.

The two expectations are deliberately separate: Terra's calibrated expectation
classifies its five observed stations; Nathan's estimator describes a location.
Unknown measured sites receive a numerical comparison, never invented thresholds.
"""
from __future__ import annotations

from datetime import date, datetime
import json
import math
from numbers import Integral, Real
from typing import Any

import numpy as np
import pandas as pd

from chillwatch.estimate import estimate_location
from chillwatch.qc.neighbors import active_on, haversine_km, hour_local_dates

SCHEMA_VERSION = "terra-location-assessment-v1"
DATA_MODES = {"historical_replay", "current", "injected", "synthetic"}
OPERATION_GATE_VERSION = "terra-station-operation-v1"


def _number(value: Any, label: str) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return float(value)


def _time(value: Any, label: str, *, hourly: bool = False) -> pd.Timestamp:
    if value is None or isinstance(value, (bool, int, float, np.number)):
        raise ValueError(f"{label} must be an aware timestamp")
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be an aware timestamp") from exc
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError(f"{label} must be an aware timestamp")
    stamp = stamp.tz_convert("UTC")
    if hourly and stamp != stamp.floor("h"):
        raise ValueError(f"{label} must be an exact UTC hour-ending timestamp")
    return stamp


def _station_id(value: Any) -> int:
    if isinstance(value, (bool, np.bool_)):
        raise ValueError("station_id must be a positive integer")
    if isinstance(value, str) and value.isdecimal():
        value = int(value)
    if not isinstance(value, Integral) or int(value) < 1:
        raise ValueError("station_id must be a positive integer")
    return int(value)


def _json(value: Any) -> Any:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (str, bool)):
        return value
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [_json(item) for item in value]
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        return float(value) if math.isfinite(value) else None
    raise TypeError(f"Unsupported assessment value: {type(value).__name__}")


def _observations(frame: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    required = {"station_id", "timestamp", "temperature_f", "quality", "source_flag"}
    if not isinstance(frame, pd.DataFrame) or not required.issubset(frame.columns):
        raise ValueError("observations need station_id, timestamp, temperature_f, quality and source_flag")
    out = frame.copy()
    out["station_id"] = [_station_id(s) for s in out.station_id]
    out["timestamp"] = pd.to_datetime([_time(t, "observation timestamp", hourly=True) for t in out.timestamp], utc=True)
    out = out[out.timestamp.le(as_of)].copy()
    if "visible_at" in out:
        out["visible_at"] = [pd.NaT if pd.isna(v) else _time(v, "observation visible_at") for v in out.visible_at]
        out["visible_at"] = pd.to_datetime(out.visible_at, utc=True)
        out = out[out.visible_at.le(as_of)].copy()
        if out.duplicated(["station_id", "timestamp", "visible_at"]).any():
            raise ValueError("Resolve observations with ambiguous equal-visibility revisions")
        out = out.sort_values("visible_at", kind="stable").drop_duplicates(["station_id", "timestamp"], keep="last")
    elif out.duplicated(["station_id", "timestamp"]).any():
        raise ValueError("Resolve duplicate station-hours or supply revision visible_at")
    if "timestamp_verified" not in out:
        out["timestamp_verified"] = False
    elif not out.timestamp_verified.map(lambda v: pd.isna(v) or isinstance(v, (bool, np.bool_))).all():
        raise ValueError("timestamp_verified must contain booleans")
    out["timestamp_verified"] = out.timestamp_verified.fillna(False).astype(bool)
    return out.sort_values(["timestamp", "station_id"]).reset_index(drop=True)


def _stations(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"station_id", "lat", "lon", "elevation_ft"}
    if not isinstance(frame, pd.DataFrame) or not required.issubset(frame.columns):
        raise ValueError("stations need station_id, lat, lon and elevation_ft")
    out = frame.copy()
    out["station_id"] = [_station_id(s) for s in out.station_id]
    if out.station_id.duplicated().any():
        raise ValueError("stations contain duplicate station IDs")
    for column in ("lat", "lon", "elevation_ft"):
        out[column] = [_number(v, f"station {column}") for v in out[column]]
    if not out.lat.between(-90, 90).all() or not out.lon.between(-180, 180).all():
        raise ValueError("station coordinates are out of range")
    for column in ("connect_date", "disconnect_date"):
        if column not in out:
            out[column] = pd.NaT
    return out.sort_values("station_id").reset_index(drop=True)


def _clean(frame: pd.DataFrame, calibrated: set[int]) -> pd.DataFrame:
    from terra_qc.availability import clean_hourly

    known = frame[frame.station_id.isin(calibrated)]
    rows = clean_hourly(known.to_dict("records")).rows if len(known) else []
    for row in rows:
        row["source_quality"] = row["quality"]
        if not row["available"]:
            row["quality"] = "invalid"
    # Other stations can contribute to the location estimate, but do not acquire
    # station-specific Terra calibration. Inputs are explicitly normalized F.
    for source in frame[~frame.station_id.isin(calibrated)].to_dict("records"):
        row = dict(source)
        reasons = []
        value = row["temperature_f"]
        finite = isinstance(value, Real) and not isinstance(value, (bool, np.bool_)) and math.isfinite(value)
        if not finite:
            reasons.append("missing_or_nonfinite_value")
            # Keep the numeric JSON contract even for an unavailable unknown
            # sensor. The caller's original frame remains untouched.
            row["temperature_f"] = None
        elif not -130 <= value <= 140:
            reasons.append("physical_bounds")
        if not isinstance(row["quality"], str) or row["quality"] != "usable":
            reasons.append("source_quality")
        if row["source_flag"] is None or pd.isna(row["source_flag"]):
            reasons.append("source_qc_unknown")
        elif str(row["source_flag"]).strip():
            reasons.append("source_warning")
        if row.get("temperature_unit", "F") != "F":
            reasons.append("unit_conflict")
        if not row["timestamp_verified"]:
            reasons.append("timestamp_unverified")
        if row.get("available") is False and not reasons:
            reasons.append(str(row.get("availability_reason") or "unavailable"))
        row.update(source_quality=row["quality"], available=not reasons,
                   availability_reason=reasons[0] if reasons else "available", availability_reasons=reasons)
        if reasons:
            row["quality"] = "invalid"
        rows.append(row)
    columns = list(dict.fromkeys([*frame.columns, "available", "availability_reason", "availability_reasons", "source_quality"]))
    out = pd.DataFrame(rows) if rows else pd.DataFrame(columns=columns)
    out["timestamp"] = pd.to_datetime(out.timestamp, utc=True)
    out["timestamp_verified"] = out.timestamp_verified.fillna(False).astype(bool)
    return out.sort_values(["timestamp", "station_id"]).reset_index(drop=True)


def _score(frame: pd.DataFrame, reference: Any) -> pd.DataFrame:
    from terra_qc import qc

    return qc.score(frame, reference) if len(frame) else pd.DataFrame()


def _operation_gate(cleaned: pd.DataFrame, stations: pd.DataFrame) -> pd.DataFrame:
    """Apply caller-supplied inclusive operating dates using Nathan's PST days.

    Missing dates retain the estimator's permissive behavior. Do not mutate
    source quality or raw evidence; operation eligibility is a separate gate.
    """
    out = cleaned.copy(deep=True)
    out["operation_available"] = True
    if out.empty:
        return out
    days = hour_local_dates(pd.DatetimeIndex(out.timestamp))
    for day in np.unique(days):
        inactive = set(stations.loc[~active_on(stations, pd.Timestamp(day)), "station_id"])
        mask = (days == day) & out.station_id.isin(inactive)
        out.loc[mask, "operation_available"] = False
        out.loc[mask, "available"] = False
        out.loc[mask, "availability_reason"] = "station_not_operating"
        for index in out.index[mask]:
            out.at[index, "availability_reasons"] = list(dict.fromkeys(
                ["station_not_operating", *out.at[index, "availability_reasons"]]))
    return out


def _findings(scored: pd.DataFrame, observations: pd.DataFrame) -> list[dict]:
    from terra_qc import qc

    result = []
    if scored.empty:
        return result
    publication = {}
    if "visible_at" in observations:
        publication = {(int(row.station_id), pd.Timestamp(row.timestamp)): pd.Timestamp(row.visible_at)
                       for row in observations[["station_id", "timestamp", "visible_at"]].itertuples(index=False)
                       if pd.notna(row.visible_at)}
    for station in qc.STATIONS:
        for event in qc.episodes(scored, station):
            trigger = pd.Timestamp(event["detected_at"])
            visible = trigger
            # Detection visibility depends only on observations that actually
            # support its opening. Unused stations and continuing evidence
            # after the trigger cannot move the original opening's visibility.
            for evidence in event["evidence"]:
                stamp = pd.Timestamp(evidence["timestamp"])
                if stamp > trigger:
                    continue
                participants = {station, *(int(peer["station_id"]) for peer in evidence.get("peer_evidence") or [])}
                for participant in participants:
                    published = publication.get((participant, stamp))
                    if published is not None:
                        visible = max(visible, published)
            result.append({**event, "visible_at": visible})
    return sorted(result, key=lambda e: (e["station_id"], e["start"]))


def _comparison(target: int | None, valid: pd.Timestamp, as_of: pd.Timestamp, max_age: float,
                cleaned: pd.DataFrame, scored: pd.DataFrame, events: list[dict], estimate: dict) -> dict:
    base = {"station_id": target, "observed_f": None, "expected_f": None, "departure_f": None,
            "typical_low_f": None, "typical_high_f": None,
            "classification": None, "level": None, "reference_id": None, "expected_method": None}
    if target is None:
        return {**base, "status": "unassessable", "reason": "no_independent_target_observation"}
    original = cleaned[cleaned.station_id.eq(target) & cleaned.timestamp.eq(valid)]
    if original.empty:
        return {**base, "status": "unavailable", "reason": "missing_target_observation"}
    row = original.iloc[0]
    base["observed_f"] = row.temperature_f
    if not row.available:
        return {**base, "status": "unavailable", "reason": row.availability_reason}
    selected = scored[scored.station_id.eq(target) & scored.timestamp.eq(valid)] if len(scored) else pd.DataFrame()
    if len(selected):
        target_row = selected.iloc[0]
        base.update(expected_f=target_row.expected_f, departure_f=target_row.residual,
                    typical_low_f=target_row.typical_low_f, typical_high_f=target_row.typical_high_f,
                    expected_method="terra-pair-adjusted-median", reference_id=target_row.reference_id,
                    level=target_row.level)
        if target_row.reference_status != "available":
            return {**base, "status": "unavailable", "reason": target_row.status}
        base["classification"] = "suspected_anomaly" if any(
            e["station_id"] == target and any(pd.Timestamp(p["timestamp"]) == valid for p in e["evidence"])
            for e in events) else "no_alert"
        status, reason = "assessable", "frozen_station_policy"
    else:
        base.update(expected_f=estimate["temperature"], expected_method=estimate["method"])
        if estimate["status"] == "insufficient_data":
            return {**base, "status": "unavailable", "reason": estimate["status_reason"]}
        base["departure_f"] = row.temperature_f - estimate["temperature"]
        status, reason = "comparison_only", "no_calibrated_station_policy"
    if as_of - valid > pd.Timedelta(hours=max_age):
        return {**base, "classification": None, "status": "stale", "reason": "observation_older_than_age_limit"}
    return {**base, "status": status, "reason": reason}


def assess_location(*, lat: float, lon: float, elevation_ft: float, valid_utc: Any, as_of: Any,
                    observations: pd.DataFrame, stations: pd.DataFrame, snapshot_id: str, data_mode: str,
                    location_id: str | None = None, target_station_id: int | None = None,
                    max_age_hours: float = 6.0, reference: Any = None,
                    geometry_source: str = "caller-supplied") -> dict:
    """Join one location/hour estimate and causal source flags into strict JSON data.

    ``data_mode`` is explicit; no clock is inferred from the host. Source rows
    require aware UTC hours and explicit timestamp verification. Missing
    publication visibility means snapshot-only evidence, not a reconstructed
    historical publication record. This function writes nothing.
    """
    from terra_qc import qc
    from terra_qc.reference import REFERENCE_ID, REFERENCE_SHA256, load_reference

    lat, lon = _number(lat, "lat"), _number(lon, "lon")
    elevation_ft, max_age_hours = _number(elevation_ft, "elevation_ft"), _number(max_age_hours, "max_age_hours")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180 or max_age_hours < 0:
        raise ValueError("coordinates or max_age_hours are out of range")
    if not isinstance(snapshot_id, str) or not snapshot_id.strip():
        raise ValueError("snapshot_id is required")
    if data_mode not in DATA_MODES:
        raise ValueError("data_mode must explicitly identify current, replay, injected or synthetic data")
    if location_id is not None and (not isinstance(location_id, str) or not location_id.strip()):
        raise ValueError("location_id must be nonempty")
    if not isinstance(geometry_source, str) or not geometry_source.strip():
        raise ValueError("geometry_source must be nonempty")
    valid, clock = _time(valid_utc, "valid_utc", hourly=True), _time(as_of, "as_of")
    if valid > clock:
        raise ValueError("valid_utc must not be after as_of")
    if reference is None:
        reference = load_reference()
    else:
        qc.reference_payload(reference)  # validate identity even when there are no calibrated rows
    if reference.manifest["model_id"] != REFERENCE_ID:
        raise ValueError("This integration requires the selected frozen Terra reference")
    calibrated = set(qc.STATIONS)
    st = _stations(stations)
    target = None if target_station_id is None else _station_id(target_station_id)
    if target is not None:
        match = st[st.station_id.eq(target)]
        if len(match) != 1 or not all(math.isclose(a, b, rel_tol=0, abs_tol=1e-6) for a, b in
                                     zip((lat, lon, elevation_ft), match.iloc[0][["lat", "lon", "elevation_ft"]], strict=True)):
            raise ValueError("Target station geometry must match the queried location")
    # The calibrated station identity includes its geometry. A silently moved
    # station cannot retain an old calibrated interpretation.
    frozen_geometry = reference.manifest["station_geometry"]["stations"]
    for row in st[st.station_id.isin(calibrated)].to_dict("records"):
        frozen = frozen_geometry[str(row["station_id"])]
        if not all(math.isclose(row[c], frozen[f], rel_tol=0, abs_tol=1e-6) for c, f in
                   (("lat", "latitude"), ("lon", "longitude"), ("elevation_ft", "elevation_ft"))):
            raise ValueError("Calibrated station geometry differs from the frozen reference")
    observed = _observations(observations, clock)
    cleaned = _operation_gate(_clean(observed, calibrated), st)
    # The frozen cleaner deliberately reconstructs source eligibility from raw
    # evidence. Keep operation-ineligible rows out of that pass rather than
    # altering their source quality or changing frozen QC mathematics.
    known = cleaned[cleaned.station_id.isin(calibrated) & cleaned.operation_available].copy()
    # Preserve canonical source quality for E1; availability remains explicit.
    known["quality"] = known["source_quality"]
    scored = _score(known, reference)
    full_events = _findings(scored, observed)
    if target in calibrated:
        # Fixed target holdout also excludes its influence on peer QC flags;
        # this is not iterative network repair or a changed calibration.
        peer_known = known[known.station_id.ne(target)]
        peer_events = _findings(_score(peer_known, reference), observed[observed.station_id.ne(target)])
        estimation_events = peer_events
    else:
        estimation_events = full_events
    exclusion_rows = [{"station_id": e["station_id"], "timestamp": p["timestamp"], "reason": e["reason"],
                       "visible_at": e["visible_at"]} for e in estimation_events for p in e["evidence"]]
    exclusions = pd.DataFrame(exclusion_rows, columns=["station_id", "timestamp", "reason", "visible_at"])
    operation_day = pd.Timestamp(hour_local_dates(pd.DatetimeIndex([valid]))[0])
    operating = active_on(st, operation_day)
    inactive_ids = set(st.loc[~operating, "station_id"])
    estimate_frame = estimate_location(lat, lon, elevation_ft, [valid], clock,
        cleaned[~cleaned.station_id.isin(inactive_ids)], st[operating], exclusions,
        location_id=location_id, geometry_source=geometry_source, exclude_station_ids=() if target is None else (target,),
        max_age_hours=max_age_hours)
    estimate = estimate_frame.iloc[0].to_dict()
    # Filtering geometry also prevents inactive stations from entering the
    # estimator's lapse fit. Expose nearby exclusions instead of silently
    # dropping them from the caller's evidence.
    from chillwatch import config
    radius = float(config.thresholds()["neighbors"]["max_km"])
    nearby = haversine_km(lat, lon, st.lat.to_numpy(), st.lon.to_numpy()) <= radius
    estimate["excluded"].extend({"station_id": int(s), "reason": "station_not_operating"}
                              for s in st.loc[~operating & nearby, "station_id"])
    relevant = set(estimate["used_station_ids"]) | set(estimate["lapse_station_ids"])
    relevant.update(x["station_id"] for x in estimate["excluded"])
    flags = [{"station_id": e["station_id"], "reason": e["reason"], "severity": e["severity"],
              "start_utc": e["start"], "end_utc": e["end"], "detected_at_utc": e["detected_at"],
              "evidence_peak_utc": max(e["evidence"], key=lambda p: abs(p["residual_f"]))["timestamp"],
              "visible_at_utc": e["visible_at"], "reference_id": REFERENCE_ID, "policy": e["policy"],
              "summary": e["summary"], "evidence": e["evidence"], "message": e["message"],
              "freshness": "stale" if clock - valid > pd.Timedelta(hours=max_age_hours) else "within_age_limit"}
             for e in full_events if e["station_id"] in relevant and e["station_id"] not in inactive_ids and e["visible_at"] <= clock
             and any(pd.Timestamp(p["timestamp"]) == valid for p in e["evidence"])]
    health = [{"station_id": int(r.station_id), "valid_utc": valid, "reason": r.availability_reason,
               "reasons": r.availability_reasons, "source_quality": r.source_quality, "source_flag": r.source_flag}
              for r in cleaned[cleaned.station_id.isin(relevant) & cleaned.timestamp.eq(valid) & ~cleaned.available].itertuples()]
    result = _json({"schema_version": SCHEMA_VERSION, "data_mode": data_mode,
        "query": {"location_id": estimate["location_id"], "lat": lat, "lon": lon, "elevation_ft": elevation_ft,
                  "valid_utc": valid, "as_of_utc": clock, "target_station_id": target, "max_age_hours": max_age_hours},
        "location_estimate": estimate,
        "target_comparison": _comparison(target, valid, clock, max_age_hours, cleaned, scored, full_events, estimate),
        "station_findings": sorted(flags, key=lambda e: (e["station_id"], e["start_utc"])), "data_health": health,
        "estimator_qc_exclusions": [{"station_id": e["station_id"], "reason": e["reason"],
            "severity": e["severity"], "detected_at_utc": e["detected_at"], "visible_at_utc": e["visible_at"],
            "basis": "target_held_out" if target in calibrated else "causal_station_qc"}
            for e in estimation_events if e["station_id"] in relevant and e["station_id"] not in inactive_ids and e["visible_at"] <= clock
            and any(pd.Timestamp(p["timestamp"]) == valid for p in e["evidence"])],
        "provenance": {"qc_reference_id": REFERENCE_ID, "qc_reference_sha256": REFERENCE_SHA256,
                       "data_snapshot_id": snapshot_id, "estimator_method_version": estimate["method_version"],
                       "adapter_operation_gate_version": OPERATION_GATE_VERSION,
                       "operation_day_pst": operation_day.date().isoformat(),
                       "publication_visibility": "explicit_visible_at" if "visible_at" in observations else "snapshot_only",
                       "peer_exclusion_basis": "target_held_out" if target in calibrated else "causal_station_qc",
                       "side_effects": "none"}})
    # These checks have their own units, eligibility and policy. They neither
    # change the frozen temperature reference nor exclude peers from its estimate.
    from .weather import VARIABLES, assess_weather
    weather_stations = st[operating & (haversine_km(lat, lon, st.lat.to_numpy(), st.lon.to_numpy()) <= 75)].copy()
    weather = assess_weather(observed[observed.station_id.isin(weather_stations.station_id)],
        weather_stations, valid_time_utc=valid, as_of_utc=clock, max_age_hours=max_age_hours)
    weather["enabled"] = any(field in observations.columns for field in VARIABLES)
    result["weather"] = _json(weather)
    json.dumps(result, allow_nan=False, sort_keys=True)
    return result
