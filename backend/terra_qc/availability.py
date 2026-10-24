"""Deterministic hourly availability checks; no statistical clipping or gap filling.

Provider rows need explicit verified timestamps. Canonical analysis/synthetic
inputs may supply aware hourly timestamps without provider verification. Original
rows remain in ``raw_source_rows``; nonfinite originals are preserved as text so
the result and audit can be encoded as finite JSON. Recleaning uses those originals.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import json
import math
from numbers import Integral, Real

VERSION = "terra-availability-v1"
STATIONS = (6, 226, 139, 212, 235)
PROVIDERS = {"cimis", "fnric"}
ANALYSIS_SOURCES = {"analysis", "synthetic", "canonical"}
SOURCE_REASONS = {"missing_temperature": "missing_value", "invalid_temperature": "invalid_numeric",
    "nonfinite_temperature": "nonfinite_value", "physical_range": "physical_bounds",
    "unit_unverified": "unknown_unit", "qc_pending": "source_pending",
    # Structural exclusions survive a canonical QC/scenario handoff. Age/future
    # exclusions are deliberately recomputed against the newly supplied as_of.
    **{reason: reason for reason in ("source_missing", "source_warning", "source_qc_unknown", "conflicting_duplicate",
        "missing_value", "invalid_numeric", "nonfinite_value", "unknown_unit", "unit_conflict")}}


@dataclass(frozen=True)
class AvailabilityPolicy:
    temperature_min_f: float = -130.0
    temperature_max_f: float = 140.0
    max_age_hours: float | None = None

    def __post_init__(self):
        values = (self.temperature_min_f, self.temperature_max_f)
        if any(isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) for value in values):
            raise ValueError("Temperature bounds must be finite numbers")
        if self.temperature_min_f >= self.temperature_max_f:
            raise ValueError("Temperature bounds must be ordered")
        if self.max_age_hours is not None and (isinstance(self.max_age_hours, bool)
            or not isinstance(self.max_age_hours, Real) or not math.isfinite(self.max_age_hours) or self.max_age_hours < 0):
            raise ValueError("Maximum age must be a finite nonnegative hour count")


@dataclass
class CleanResult:
    rows: list[dict]
    quarantined: list[dict]
    audit: dict


def _safe(value):
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        return float(value) if math.isfinite(value) else ("NaN" if math.isnan(value) else "Infinity" if value > 0 else "-Infinity")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    return str(value)


def _stable(row):
    return json.dumps(_safe(row), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _stamp(value):
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None, "timestamp_invalid"
    try:
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            return None, "timestamp_naive"
        stamp = stamp.astimezone(timezone.utc)
        if stamp.minute or stamp.second or stamp.microsecond:
            return None, "timestamp_off_hour"
    except (TypeError, ValueError, OverflowError):
        return None, "timestamp_invalid"
    return stamp, None


def _station(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.removeprefix("CIMIS-")
        if not text.isdecimal():
            return None
        value = int(text)
    return int(value) if isinstance(value, Integral) and int(value) in STATIONS else None


def _numeric(value):
    if value is None or isinstance(value, str) and not value.strip():
        return None, "missing_value"
    if isinstance(value, bool):
        return None, "invalid_numeric"
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None, "invalid_numeric"
    return (number, None) if math.isfinite(number) else (None, "nonfinite_value")


def _unit(value):
    return str(value).strip().upper().replace("°", "") if value is not None else None


def _missing_metadata(value):
    return (value is None or isinstance(value, Real) and math.isnan(value)
            or isinstance(value, str) and value in {"NaN", "<NA>"}
            or type(value).__name__ == "NAType")


def _temperature(row):
    """temperature_f is already F; nested vendor raw units never convert it twice."""
    has_f, has_pair = "temperature_f" in row, "temperature_value" in row
    unit = _unit(row.get("temperature_unit"))
    if not has_f and not has_pair:
        return None, ["missing_value"], False
    if has_f:
        value, reason = _numeric(row["temperature_f"])
        reasons = [reason] if reason else []
        if has_pair:
            other, error = _numeric(row["temperature_value"])
            if unit not in {"F", "C"}:
                reasons.append("unknown_unit")
            elif error:
                reasons.append(error)
            elif value is not None:
                other = other * 9 / 5 + 32 if unit == "C" else other
                if not math.isfinite(other):
                    reasons.append("nonfinite_value")
                elif not math.isclose(value, other, rel_tol=1e-12, abs_tol=1e-9):
                    reasons.append("unit_conflict")
        elif unit is not None and unit != "F":
            reasons.append("unit_conflict" if unit == "C" else "unknown_unit")
        return value, reasons, False
    value, reason = _numeric(row["temperature_value"])
    if unit not in {"F", "C"}:
        return None, ["unknown_unit"], False
    if reason:
        return None, [reason], False
    converted = value * 9 / 5 + 32 if unit == "C" else value
    if not math.isfinite(converted):
        return None, ["nonfinite_value"], False
    return converted, [], unit == "C"


def _quality(row, source):
    quality = row.get("quality")
    quality = quality if quality is None or isinstance(quality, str) else "__UNKNOWN__"
    flag = row.get("source_flag")
    flag = None if _missing_metadata(flag) else str(flag).strip().upper()
    unknown = flag == "__UNKNOWN__" or flag is None and (source in PROVIDERS or quality == "flagged")
    if flag in {"M", "N"} or quality == "missing":
        return "missing", ["source_missing"]
    if flag == "P" or quality == "pending":
        return "pending", ["source_pending"]
    if quality == "invalid":
        return "invalid", ["source_invalid"]
    if unknown or quality not in {"usable", "flagged"}:
        return "flagged", ["source_qc_unknown"]
    if quality == "flagged" or flag:
        return "flagged", ["source_warning"]
    return "usable", []


def clean_hourly(rows, *, as_of=None, policy=AvailabilityPolicy()) -> CleanResult:
    """Normalize eligibility only. Invalid identity/time rows are quarantined without invented keys.

    Duplicate identity is (source, station, UTC instant). Equivalent observations
    collapse; disagreements in value, QC or trust make the entire group unavailable.
    Absolute age applies only when both as_of and max_age_hours are supplied.
    """
    if not isinstance(policy, AvailabilityPolicy):
        raise ValueError("Use an AvailabilityPolicy")
    if as_of is not None:
        try:
            as_of = as_of if isinstance(as_of, datetime) else datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            raise ValueError("as_of must be an aware UTC datetime") from None
        if as_of.tzinfo is None or as_of.utcoffset() != timedelta(0):
            raise ValueError("as_of must be an aware UTC datetime")
    supplied = list(rows)
    candidates = []
    for row in supplied:
        if isinstance(row, dict) and row.get("cleaner_version") == VERSION and isinstance(row.get("raw_source_rows"), list):
            candidates.extend(row["raw_source_rows"])
        else:
            candidates.append(row)
    groups, quarantined = defaultdict(list), []
    converted = 0
    for original in candidates:
        raw = _safe(original)
        if not isinstance(original, dict):
            quarantined.append({"row": raw, "reasons": ["invalid_row"]})
            continue
        source_value = original.get("source")
        station = _station(original.get("station_id"))
        source = "analysis" if _missing_metadata(source_value) else str(source_value).strip().lower()
        stamp, error = _stamp(original.get("timestamp"))
        errors = (["unknown_station"] if station is None else []) + ([error] if error else [])
        if errors:
            quarantined.append({"row": raw, "reasons": errors})
            continue
        temperature_row = original
        if _missing_metadata(original.get("temperature_unit")):
            temperature_row = {**original, "temperature_unit": None}
        value, reasons, conversion = _temperature(temperature_row)
        converted += int(conversion)
        quality, source_reasons = _quality(original, source)
        supplied_reason = original.get("availability_reason")
        supplied_reason = SOURCE_REASONS.get(supplied_reason) if isinstance(supplied_reason, str) else None
        if supplied_reason:
            reasons = [supplied_reason] + [reason for reason in reasons if reason != "missing_value"]
        if quality in {"missing", "pending"} or quality == "invalid" and reasons == ["missing_value"]:
            reasons = source_reasons + [reason for reason in reasons if reason != "missing_value"]
        else:
            reasons += source_reasons
        if value is not None and not policy.temperature_min_f <= value <= policy.temperature_max_f:
            reasons.append("physical_bounds")
        trust = original.get("timestamp_verified")
        trust = None if _missing_metadata(trust) else trust
        verified = trust is True or source in ANALYSIS_SOURCES and trust is None
        if not verified:
            reasons.append("timestamp_unverified")
        if as_of is not None:
            age = (as_of - stamp).total_seconds() / 3600
            if age < 0:
                reasons.append("future")
            elif policy.max_age_hours is not None and age > policy.max_age_hours:
                reasons.append("stale")
        reasons = list(dict.fromkeys(reasons))
        normalized = {**raw, "source": source, "station_id": station, "timestamp": stamp.isoformat(),
            "timestamp_verified": verified, "temperature_f": value, "temperature_unit": "F", "quality": quality,
            "available": not reasons, "usable_f": value if not reasons else None,
            "availability_reason": reasons[0] if reasons else "available", "availability_reasons": reasons,
            "cleaner_version": VERSION}
        flag = original.get("source_flag")
        signature = (value, quality, "" if _missing_metadata(flag) else str(flag).strip().upper(), verified, tuple(reasons))
        groups[(source, station, stamp)].append((signature, normalized, raw))
    output, duplicate_groups, duplicate_collapsed, conflicts = [], 0, 0, 0
    for (source, station, stamp), group in sorted(groups.items(), key=lambda item: (item[0][2], item[0][1], item[0][0])):
        group.sort(key=lambda item: _stable(item[2]))
        raws = [item[2] for item in group]
        if len(group) > 1:
            duplicate_groups += 1
            duplicate_collapsed += len(group) - 1
        if any(item[0] != group[0][0] for item in group[1:]):
            conflicts += 1
            row = {"source": source, "station_id": station, "timestamp": stamp.isoformat(),
                "timestamp_verified": all(item[1]["timestamp_verified"] for item in group),
                "temperature_f": None, "temperature_unit": "F", "quality": "invalid", "available": False,
                "usable_f": None, "availability_reason": "conflicting_duplicate",
                "availability_reasons": ["conflicting_duplicate"], "cleaner_version": VERSION}
            quarantined.extend({"row": raw, "reasons": ["conflicting_duplicate"], "timestamp": stamp.isoformat(),
                                "station_id": station} for raw in raws)
        else:
            row = group[0][1]
            serious = {"invalid_numeric", "nonfinite_value", "physical_bounds", "unknown_unit", "unit_conflict"}
            if serious.intersection(row["availability_reasons"]):
                quarantined.extend({"row": raw, "reasons": row["availability_reasons"],
                                    "timestamp": stamp.isoformat(), "station_id": station} for raw in raws)
        row["raw_source_rows"] = raws
        output.append(row)
    gaps, streams = [], defaultdict(list)
    for source, station, stamp in groups:
        streams[(source, station)].append(stamp)
    for (source, station), stamps in sorted(streams.items()):
        ordered = sorted(stamps)
        for before, after in zip(ordered, ordered[1:]):
            missing = int((after - before).total_seconds() / 3600) - 1
            if missing:
                gaps.append({"source": source, "station_id": station, "after": before.isoformat(),
                             "before": after.isoformat(), "missing_hours": missing})
    reasons = Counter(reason for row in output for reason in row["availability_reasons"] or ["available"])
    quarantine_reasons = Counter(reason for row in quarantined for reason in row["reasons"])
    examples = defaultdict(list)
    for row in output:
        for reason in row["availability_reasons"]:
            if len(examples[reason]) < 3:
                examples[reason].append({key: row[key] for key in ("source", "station_id", "timestamp", "temperature_f")})
    audit = {"version": VERSION, "input_rows": len(supplied), "candidate_rows": len(candidates),
        "output_rows": len(output), "available_rows": sum(row["available"] for row in output),
        "unavailable_rows": sum(not row["available"] for row in output), "quarantined_rows": len(quarantined),
        "reason_counts": dict(sorted(reasons.items())), "quarantine_reason_counts": dict(sorted(quarantine_reasons.items())),
        "quarantine_examples": [{"reasons": item["reasons"], **{key: item["row"].get(key)
            for key in ("source", "station_id", "timestamp")}} for item in quarantined[:5] if isinstance(item["row"], dict)],
        "examples": dict(examples), "converted_rows": converted, "duplicate_groups": duplicate_groups,
        "duplicates_collapsed": duplicate_collapsed, "conflicting_duplicate_groups": conflicts,
        "gap_hours": sum(item["missing_hours"] for item in gaps), "gap_intervals": len(gaps), "gap_examples": gaps[:5],
        "as_of": as_of.isoformat() if as_of is not None else None, "policy": asdict(policy),
        "input_contract": "Canonical analysis/synthetic aware UTC-hour observations are trusted; named providers require timestamp_verified=True",
        "statistical_clipping": False, "gap_filling": False}
    return CleanResult(output, sorted(quarantined, key=_stable), _safe(audit))
