"""Frozen statistical station references and causal residual alerts."""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from datetime import timedelta, timezone
import hashlib
import json
import platform
from pathlib import Path

import numpy as np
import pandas as pd

from terra_qc.availability import AvailabilityPolicy, VERSION as CLEANING_VERSION, clean_hourly

STATIONS = (6, 226, 139, 212, 235)
FEATURES = ("departure_f", "relative_change_f", "prior_six_hour_departure_f")
VERSION = "terra-qc-v3"
SPATIAL_VERSION = "terra-qc-v4"
SUPPORTED_VERSIONS = {VERSION, SPATIAL_VERSION}
PST = timezone(timedelta(hours=-8))
TRAIN_START = "2023-09-01T08:00:00Z"
TRAIN_END = "2025-09-01T08:00:00Z"
VALIDATION_END = "2025-12-01T08:00:00Z"
WINTER_END = "2026-03-01T08:00:00Z"
REPLAY_START = "2026-01-10T08:00:00Z"
REPLAY_END = "2026-01-14T08:00:00Z"
QUALITIES = {"usable", "missing", "invalid", "pending", "flagged"}
CURRENT_MAX_AGE_HOURS = 6


@dataclass
class Reference:
    baselines: dict
    manifest: dict


def availability_policy():
    """Versioned defaults; current-view age and historical scoring stay distinct."""
    return {"version": CLEANING_VERSION, "temperature_min_f": -130, "temperature_max_f": 140,
            "current_max_age_hours": CURRENT_MAX_AGE_HOURS,
            "historical_age_gate": False, "provider_time_verification_required": True,
            "duplicate_policy": "collapse equivalent; conflicting observations unavailable",
            "gap_policy": "no filling"}


def prepare_readings(frame, *, as_of=None, max_age_hours=None, policy=None):
    """Clean once at public entry points; keep raw candidates in the audit result."""
    if not {"station_id", "timestamp", "quality"}.issubset(frame.columns):
        raise ValueError("Readings require station_id, timestamp and explicit quality")
    settings = policy or availability_policy()
    result = clean_hourly(frame.to_dict("records"), as_of=as_of,
        policy=AvailabilityPolicy(temperature_min_f=settings["temperature_min_f"],
            temperature_max_f=settings["temperature_max_f"], max_age_hours=max_age_hours))
    if not result.rows:
        raise ValueError("Insufficient data: no valid timestamped hourly records; inspect the cleaning audit")
    data = pd.DataFrame(result.rows)
    data["timestamp"] = pd.to_datetime(data.timestamp, utc=True)
    if as_of is not None:
        # Future rows remain in the cleaning report, never in the causal grid.
        data = data[data.timestamp.le(pd.Timestamp(as_of))]
    if data.empty:
        raise ValueError("Insufficient data: no completed hourly records at the requested time")
    data.attrs["cleaning_audit"] = result.audit
    return data


def with_availability(reference):
    """New immutable identity for explicit cleaning, without changing calibration."""
    _validate_reference(reference)
    if reference.manifest.get("availability_policy") == availability_policy():
        return reference
    manifest = deepcopy(reference.manifest)
    manifest.update(availability_policy=availability_policy(), source_model_id=reference.manifest["model_id"],
        source_model_version=reference.manifest["model_version"],
        migration="Added explicit availability gates; baseline and residual bounds copied without refit")
    return _finish(deepcopy(reference.baselines), manifest)


def with_temporal_policy(reference, policy):
    """Snapshot operator thresholds/persistence without refitting the reference."""
    from terra_qc import temporal
    _validate_reference(reference)
    if reference.manifest["model_version"] != SPATIAL_VERSION:
        raise ValueError("Configurable temporal policy requires the spatial v4 reference")
    normalized = temporal.validate(policy)
    if reference.manifest.get("temporal_policy") == normalized:
        return reference
    manifest = deepcopy(reference.manifest)
    readings = normalized["variables"]["temperature_f"]["sustained_readings"]
    description = f"one extreme departure or {readings} consecutive readings beyond configured residual thresholds"
    manifest.update(temporal_policy=normalized,
        policy_base_reference_id=manifest.get("policy_base_reference_id", reference.manifest["model_id"]),
        temporal_policy_source="Explicit operator snapshot; original residual calibration unchanged",
        decision_policy=description)
    manifest["range_calibration"]["decision_policy"] = description
    return _finish(deepcopy(reference.baselines), manifest)


def _canonical(frame):
    required = {"station_id", "timestamp", "temperature_f", "quality"}
    if not required.issubset(frame.columns):
        raise ValueError(f"Readings require columns {sorted(required)}")
    columns = list(sorted(required)) + [name for name in
        ("source_flag", "source", "timestamp_verified", "temperature_unit", "available", "usable_f",
         "availability_reason", "cleaner_version") if name in frame]
    if "availability_reasons" in frame:
        columns.append("availability_reasons")
    data = frame[columns].copy()
    if data.empty:
        raise ValueError("No hourly readings supplied")
    if not data.station_id.isin(STATIONS).all():
        raise ValueError("Unknown station ID")
    if not isinstance(data.timestamp.dtype, pd.DatetimeTZDtype):
        raise ValueError("Timestamps must be timezone-aware; canonical UTC hour-ending")
    data["timestamp"] = data.timestamp.dt.tz_convert("UTC")
    if data.timestamp.isna().any() or not data.timestamp.eq(data.timestamp.dt.floor("h")).all():
        raise ValueError("Timestamps must be exact UTC hours")
    if data.duplicated(["station_id", "timestamp"]).any():
        raise ValueError("Resolve source duplicates/corrections before QC")
    if not data.quality.isin(QUALITIES).all():
        raise ValueError("Unknown source quality")
    data["temperature_f"] = pd.to_numeric(data.temperature_f, errors="raise")
    return data.sort_values(["timestamp", "station_id"]).reset_index(drop=True)


def _raw(frame):
    data = _canonical(frame)
    hours = pd.date_range(data.timestamp.min(), data.timestamp.max(), freq="h")
    grid = pd.MultiIndex.from_product([hours, STATIONS], names=["timestamp", "station_id"])
    data = data.set_index(["timestamp", "station_id"]).reindex(grid).reset_index()
    data["quality"] = data.quality.fillna("missing")
    if "available" in data:
        data["available"] = data.available.fillna(False).astype(bool)
    else:
        data["available"] = data.quality.eq("usable") & np.isfinite(data.temperature_f)
    data["availability_reason"] = data.get("availability_reason", pd.Series(index=data.index, dtype=object)).fillna("missing_hour")
    if "timestamp_verified" in data:
        data["timestamp_verified"] = data.timestamp_verified.fillna(False).astype(bool)
    if "cleaner_version" in data:
        data["cleaner_version"] = data.cleaner_version.fillna(CLEANING_VERSION)
    usable = data.available & data.quality.eq("usable") & np.isfinite(data.temperature_f)
    # Scenario edits change temperature_f; never reuse an earlier usable_f copy.
    data["usable_f"] = data.temperature_f.where(usable)
    values = data.pivot(
        index="timestamp", columns="station_id", values="usable_f")
    local = hours.tz_convert(PST)
    pieces = []
    for station_id in STATIONS:
        part = data[data.station_id.eq(station_id)].set_index("timestamp").copy()
        peers = values.drop(columns=station_id)
        part["neighbor_count"] = peers.count(axis=1)
        part["neighbor_f"] = peers.median(axis=1).where(part.neighbor_count.ge(3))
        part["raw_departure_f"] = values[station_id] - part.neighbor_f
        part["month"] = local.month
        part["hour"] = local.hour
        pieces.append(part.reset_index())
    return pd.concat(pieces, ignore_index=True)


def _features(raw, baselines, manifest=None):
    if manifest and manifest.get("model_version") == SPATIAL_VERSION:
        from terra_qc import spatial
        geometry = _geometry_stations(manifest["station_geometry"])
        result = spatial.features(raw, baselines, geometry, spatial.Policy(**manifest["spatial_policy"]))
        pieces = []
        for station in STATIONS:
            part = result[result.station_id.eq(station)].sort_values("timestamp").copy()
            valid = part.available & part.expected_f.notna()
            part["raw_departure_f"] = part.usable_f - part.neighbor_f
            part["temperature_change_f"] = part.temperature_f.where(valid).diff()
            part["neighbor_change_f"] = part.neighbor_f.diff()
            part["expected_change_f"] = part.expected_f.diff()
            part[FEATURES[0]] = part.residual
            part[FEATURES[1]] = part.residual.diff()
            part[FEATURES[2]] = part.residual.shift(1).rolling(6, min_periods=6).mean()
            pieces.append(part)
        return pd.concat(pieces, ignore_index=True).sort_values(["timestamp", "station_id"])
    parts = []
    for station_id in STATIONS:
        part = raw[raw.station_id.eq(station_id)].sort_values("timestamp").copy()
        baseline = baselines[station_id]
        expected = [baseline["bins"].get((int(m), int(h)), baseline["fallback"])
                    for m, h in zip(part.month, part.hour)]
        part["expected_departure_f"] = expected
        part["residual"] = part.raw_departure_f - part.expected_departure_f
        part["temperature_change_f"] = part.temperature_f.where(part.raw_departure_f.notna()).diff()
        part["neighbor_change_f"] = part.neighbor_f.diff()
        part[FEATURES[0]] = part.residual
        part[FEATURES[1]] = part.residual.diff()
        # The grid contains every hour; NaN breaks both history and comparison.
        part[FEATURES[2]] = part.residual.shift(1).rolling(6, min_periods=6).mean()
        parts.append(part)
    return pd.concat(parts, ignore_index=True).sort_values(["timestamp", "station_id"])


def _digest(data):
    canonical = _canonical(data)
    return hashlib.sha256(canonical.to_csv(index=False, float_format="%.12g").encode()).hexdigest()


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        if not np.isfinite(value):
            raise ValueError("Reference payload must contain finite numbers")
        return float(value)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise ValueError("Unsupported reference payload value")


def _encoded_baselines(baselines):
    if all("pairs" in baselines[station] for station in STATIONS):
        return {str(station): {"pairs": {str(peer): {"bins": [
            [int(month), int(hour), float(offset), int(pair["counts"][(month, hour)])]
            for (month, hour), offset in sorted(pair["bins"].items())]}
            for peer, pair in sorted(baselines[station]["pairs"].items())}} for station in STATIONS}
    return {str(station): {"fallback": float(baselines[station]["fallback"]),
        "bins": [[int(month), int(hour), float(offset)]
                 for (month, hour), offset in sorted(baselines[station]["bins"].items())]}
        for station in STATIONS}


def _model_id(baselines, manifest):
    content = {"baselines": _encoded_baselines(baselines),
               "manifest": {key: value for key, value in manifest.items() if key != "model_id"}}
    return manifest["model_version"] + "-" + hashlib.sha256(json.dumps(_json_safe(content), sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()[:16]


def _geometry_stations(snapshot):
    if snapshot.get("version") != "terra-station-geometry-v1" or snapshot.get("datum") != "WGS84":
        raise ValueError("Unsupported station geometry")
    if type(snapshot.get("historical_epochs_verified")) is not bool:
        raise ValueError("Station geometry requires an explicit historical-epoch status")
    if type(snapshot.get("datum_verified", False)) is not bool:
        raise ValueError("Station datum verification must be explicit when supplied")
    stations = _station_map(snapshot["stations"])
    for item in stations.values():
        latitude, longitude = item["latitude"], item["longitude"]
        if not _finite_number(latitude) or not -90 <= latitude <= 90 or not _finite_number(longitude) or not -180 <= longitude <= 180:
            raise ValueError("Invalid station coordinates")
        for key in ("elevation_m", "elevation_ft"):
            if item.get(key) is not None and not _finite_number(item[key]):
                raise ValueError("Invalid station elevation")
        if item.get("elevation_ft") is not None and item.get("elevation_m") is not None and not np.isclose(
            item["elevation_ft"] * .3048, item["elevation_m"], rtol=0, atol=1e-6):
            raise ValueError("Station elevation units disagree")
        if not snapshot["historical_epochs_verified"] and item.get("effective_from") is not None:
            raise ValueError("Unverified historical epochs cannot have an asserted effective date")
    return stations


def _geometry_digest(snapshot):
    return hashlib.sha256(json.dumps(_json_safe(snapshot), sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def station_geometry():
    snapshot = json.loads(Path(__file__).with_name("station_geometry.json").read_text(encoding="utf-8"))
    _geometry_stations(snapshot)
    return snapshot


def _station_map(value):
    if not isinstance(value, dict):
        raise ValueError("Station values must be a mapping")
    result = {}
    for key, item in value.items():
        if isinstance(key, bool) or str(key) not in {str(station) for station in STATIONS}:
            raise ValueError("Unknown station key in reference")
        station = int(key)
        if station in result:
            raise ValueError("Duplicate station key in reference")
        result[station] = item
    if set(result) != set(STATIONS):
        raise ValueError("Reference must contain all five stations")
    return result


def _finite_number(value):
    return not isinstance(value, (bool, np.bool_)) and isinstance(value, (int, float, np.integer, np.floating)) and np.isfinite(value)


def _validate_spatial_baselines(baselines, manifest):
    from terra_qc import spatial
    if manifest.get("spatial_method") != "pair-adjusted-median-v1" or manifest.get("feature_contract") != "terra-hourly-features-v4":
        raise ValueError("Unsupported spatial reference method")
    geometry = _geometry_stations(manifest["station_geometry"])
    if manifest.get("geometry_sha256") != _geometry_digest(manifest["station_geometry"]):
        raise ValueError("Station geometry digest disagrees with snapshot")
    policy = spatial.Policy(**manifest["spatial_policy"])
    for station, baseline in baselines.items():
        if set(baseline) != {"pairs"} or not isinstance(baseline["pairs"], dict):
            raise ValueError("Invalid spatial baseline")
        if len(baseline["pairs"]) < policy.min_neighbors:
            raise ValueError("Insufficient learned peer relationships")
        for peer, pair in baseline["pairs"].items():
            if type(peer) is not int or peer not in STATIONS or peer == station:
                raise ValueError("Invalid spatial peer")
            if spatial.distance_km(geometry[station], geometry[peer]) > policy.max_distance_km:
                raise ValueError("Learned peer outside frozen radius")
            if set(pair) != {"bins", "counts"} or set(pair["bins"]) != set(pair["counts"]):
                raise ValueError("Pair bins and support disagree")
            for key, offset in pair["bins"].items():
                if (not isinstance(key, tuple) or len(key) != 2 or any(type(v) is not int for v in key)
                    or not 1 <= key[0] <= 12 or not 0 <= key[1] <= 23 or not _finite_number(offset)):
                    raise ValueError("Invalid pair offset bin")
                count = pair["counts"][key]
                minimum_support = max(24, policy.min_pair_samples) if manifest["data_kind"] == "real" else policy.min_pair_samples
                if type(count) is not int or count < minimum_support:
                    raise ValueError("Invalid pair training support")
            if manifest["data_kind"] == "real" and len(pair["bins"]) != 288:
                raise ValueError("Real learned peer requires all supported month/hour bins")


def _validate_reference(reference):
    if not isinstance(reference, Reference):
        raise ValueError("Use a statistical Reference")
    manifest = reference.manifest
    if manifest.get("model_version") not in SUPPORTED_VERSIONS or not manifest.get("dataset_id"):
        raise ValueError("Invalid reference version or dataset identity")
    if manifest.get("data_kind") not in {"real", "synthetic"}:
        raise ValueError("Invalid reference data kind")
    policy = manifest.get("availability_policy")
    if policy is not None:
        if policy.get("version") != CLEANING_VERSION:
            raise ValueError("Unsupported availability policy")
        AvailabilityPolicy(temperature_min_f=policy["temperature_min_f"],
            temperature_max_f=policy["temperature_max_f"], max_age_hours=policy["current_max_age_hours"])
    dates = [pd.Timestamp(manifest[key]) for key in
             ("train_start", "train_end_exclusive", "validation_end_exclusive")]
    if any(date.tzinfo is None for date in dates) or not dates[0] < dates[1] < dates[2]:
        raise ValueError("Reference periods must be ordered and timezone-aware")
    baselines = _station_map(reference.baselines)
    if any(type(key) is not int for key in reference.baselines):
        raise ValueError("Internal reference station keys must be integers")
    if manifest["model_version"] == SPATIAL_VERSION:
        _validate_spatial_baselines(baselines, manifest)
    for baseline in baselines.values() if manifest["model_version"] == VERSION else ():
        if not _finite_number(baseline["fallback"]):
            raise ValueError("Nonfinite baseline fallback")
        for key, value in baseline["bins"].items():
            if not isinstance(key, tuple) or len(key) != 2 or any(isinstance(x, bool) or not isinstance(x, int) for x in key):
                raise ValueError("Baseline bin keys must be integer (month,hour) pairs")
            if not 1 <= key[0] <= 12 or not 0 <= key[1] <= 23 or not _finite_number(value):
                raise ValueError("Invalid baseline bin")
        if manifest["data_kind"] == "real" and len(baseline["bins"]) != 288:
            raise ValueError("Real reference requires all month/hour bins")
    calibration = manifest.get("range_calibration", {})
    if calibration.get("start") != manifest["train_end_exclusive"] or calibration.get("end_exclusive") != manifest["validation_end_exclusive"]:
        raise ValueError("Calibration period disagrees with reference")
    if any(type(key) is not int for key in calibration.get("bounds", {})):
        raise ValueError("Internal calibration station keys must be integers")
    for bounds in _station_map(calibration.get("bounds")).values():
        low, high, outer = (bounds[key] for key in
            ("typical_low_residual_f", "typical_high_residual_f", "extreme_abs_residual_f"))
        if not all(_finite_number(value) for value in (low, high, outer)) or not low < high or outer <= 0 or outer < max(abs(low), abs(high)):
            raise ValueError("Invalid or non-enclosing residual bounds")
    temporal_policy = manifest.get("temporal_policy")
    if temporal_policy is not None:
        from terra_qc import temporal
        if manifest["model_version"] != SPATIAL_VERSION or temporal.validate(temporal_policy) != temporal_policy:
            raise ValueError("Temporal policy must be normalized and use a spatial v4 reference")
        for station, bounds in calibration["bounds"].items():
            temporal.resolve(temporal_policy, station, bounds)
    _json_safe(manifest)
    if manifest.get("model_id") != _model_id(reference.baselines, manifest):
        raise ValueError("Reference identity does not match its contents")


def reference_payload(reference: Reference):
    """Finite, deterministic JSON-compatible values; no executable model objects."""
    _validate_reference(reference)
    schema = "terra-reference-v2" if reference.manifest["model_version"] == SPATIAL_VERSION else "terra-reference-v1"
    return _json_safe({"schema_version": schema, "baselines": _encoded_baselines(reference.baselines),
                       "manifest": reference.manifest})


def reference_from_payload(payload):
    """Validate a saved reference and restore integer station and month/hour keys."""
    try:
        return _reference_from_payload(payload)
    except (KeyError, TypeError, AttributeError, OverflowError) as error:
        raise ValueError("Malformed reference payload") from error


def _reference_from_payload(payload):
    if not isinstance(payload, dict) or payload.get("schema_version") not in {"terra-reference-v1", "terra-reference-v2"}:
        raise ValueError("Unsupported reference payload schema")
    _json_safe(payload)
    baselines = {}
    for station, baseline in _station_map(payload.get("baselines")).items():
        if payload["schema_version"] == "terra-reference-v2":
            pairs = {}
            for peer_key, pair in baseline["pairs"].items():
                if isinstance(peer_key, bool) or str(peer_key) not in {str(s) for s in STATIONS}:
                    raise ValueError("Unknown spatial peer")
                peer = int(peer_key)
                if peer in pairs:
                    raise ValueError("Duplicate spatial peer")
                bins, counts = {}, {}
                for row in pair["bins"]:
                    if not isinstance(row, list) or len(row) != 4:
                        raise ValueError("Invalid serialized pair bin")
                    month, hour, offset, support = row
                    if any(isinstance(key, bool) or not isinstance(key, int) for key in (month, hour)) or (month, hour) in bins:
                        raise ValueError("Invalid or duplicate pair bin")
                    bins[(month, hour)], counts[(month, hour)] = offset, support
                pairs[peer] = {"bins": bins, "counts": counts}
            baselines[station] = {"pairs": pairs}
            continue
        bins = {}
        for row in baseline.get("bins", []):
            if not isinstance(row, list) or len(row) != 3:
                raise ValueError("Invalid serialized baseline bin")
            month, hour, value = row
            if any(isinstance(key, bool) or not isinstance(key, int) for key in (month, hour)) or (month, hour) in bins:
                raise ValueError("Invalid or duplicate serialized baseline bin")
            bins[(month, hour)] = value
        baselines[station] = {"bins": bins, "fallback": baseline["fallback"]}
    manifest = deepcopy(payload["manifest"])
    expected_version = SPATIAL_VERSION if payload["schema_version"] == "terra-reference-v2" else VERSION
    if manifest.get("model_version") != expected_version:
        raise ValueError("Reference schema and model version disagree")
    calibration = manifest.get("range_calibration", {})
    calibration["bounds"] = _station_map(calibration.get("bounds"))
    if "counts" in calibration:
        calibration["counts"] = _station_map(calibration["counts"])
    if "counts" in manifest:
        manifest["counts"] = _station_map(manifest["counts"])
    reference = Reference(baselines, manifest)
    _validate_reference(reference)
    return reference


def _finish(baselines, manifest):
    manifest["model_id"] = _model_id(baselines, manifest)
    reference = Reference(baselines, manifest)
    _validate_reference(reference)
    return reference


def _window_counts(window, lo, hi, minimum_neighbors=3):
    valid = window.dropna(subset=["residual"])
    expected = int((hi - lo) / pd.Timedelta(hours=1))
    hours = pd.date_range(lo, hi, freq="h", inclusive="left").tz_convert(PST)
    month_expected = pd.Series(1, index=hours.strftime("%Y-%m")).groupby(level=0).sum()
    month_actual = valid.groupby(valid.timestamp.dt.tz_convert(PST).dt.strftime("%Y-%m")).size().reindex(month_expected.index, fill_value=0)
    quality = {str(key): int(value) for key, value in window.quality.value_counts().items()}
    quality["missing"] = quality.get("missing", 0) + expected - len(window)
    usable = window.quality.eq("usable") & np.isfinite(window.temperature_f)
    return {"current_comparison_hours": len(valid), "expected_hours": expected,
        "reference_coverage": len(valid) / expected, "monthly_valid_hours": {
            str(month): {"valid_hours": int(month_actual[month]), "expected_hours": int(month_expected[month]),
                         "reference_coverage": float(month_actual[month] / month_expected[month])}
            for month in month_expected.index}, "source_quality_counts": quality,
        "source_excluded_hours": int(expected - usable.sum()),
        "availability_excluded_hours": int(expected - window.available.sum()),
        "availability_reason_counts": {str(key): int(value) for key, value in window.availability_reason.value_counts().items()},
        "insufficient_neighbors_hours": int((usable & window.available & window.neighbor_count.lt(minimum_neighbors)).sum())}


def train(frame, dataset_id: str, data_kind="real", train_end=TRAIN_END,
          validation_end=VALIDATION_END, *, train_start=TRAIN_START):
    """Fit offsets on training only and empirical residual ranges on validation only."""
    if data_kind not in {"real", "synthetic"} or not dataset_id:
        raise ValueError("Use an immutable dataset ID and real/synthetic data kind")
    start, cutoff, frozen = map(pd.Timestamp, (train_start, train_end, validation_end))
    if any(date.tzinfo is None for date in (start, cutoff, frozen)) or not start < cutoff < frozen:
        raise ValueError("Training and validation periods must be ordered and timezone-aware")
    data = _canonical(prepare_readings(frame))
    fit_data = data[data.timestamp.ge(start) & data.timestamp.lt(frozen)]
    raw = _raw(fit_data)
    fitting = raw[raw.timestamp.ge(start) & raw.timestamp.lt(cutoff)]
    baselines = {}
    for station in STATIONS:
        observed = fitting[fitting.station_id.eq(station)].dropna(subset=["raw_departure_f"])
        grouped = observed.groupby(["month", "hour"]).raw_departure_f
        bins = grouped.median().to_dict()
        if observed.empty or (data_kind == "real" and (len(bins) != 288 or grouped.count().min() < 24)):
            raise ValueError(f"Station {station}: insufficient training month/hour coverage")
        baselines[station] = {"bins": bins, "fallback": float(observed.raw_departure_f.median())}
    features = _features(raw, baselines)
    return _finish(baselines, _training_manifest(fit_data, features, dataset_id, data_kind, start, cutoff, frozen))


def train_spatial(frame, dataset_id: str, data_kind="real", train_end=TRAIN_END,
                  validation_end=VALIDATION_END, *, train_start=TRAIN_START,
                  geometry=None, spatial_policy=None):
    """Training-only pair offsets; validation-only residual ranges; no fallback."""
    from dataclasses import asdict
    from terra_qc import spatial
    if data_kind not in {"real", "synthetic"} or not dataset_id:
        raise ValueError("Use an immutable dataset ID and real/synthetic data kind")
    start, cutoff, frozen = map(pd.Timestamp, (train_start, train_end, validation_end))
    if any(date.tzinfo is None for date in (start, cutoff, frozen)) or not start < cutoff < frozen:
        raise ValueError("Training and validation periods must be ordered and timezone-aware")
    geometry = deepcopy(geometry) if geometry is not None else station_geometry()
    stations = _geometry_stations(geometry)
    policy = spatial_policy or spatial.Policy()
    if not isinstance(policy, spatial.Policy):
        raise ValueError("Use a spatial Policy")
    data = _canonical(prepare_readings(frame))
    fit_data = data[data.timestamp.ge(start) & data.timestamp.lt(frozen)]
    raw = _raw(fit_data)
    training = raw[raw.timestamp.ge(start) & raw.timestamp.lt(cutoff)]
    baselines = spatial.fit_pairs(training, stations, policy, require_full_bins=data_kind == "real")
    settings = {"model_version": SPATIAL_VERSION, "station_geometry": geometry,
                "spatial_policy": asdict(policy)}
    features = _features(raw, baselines, settings)
    method = "same-hour median of eligible peers adjusted by frozen training target/peer fixed-PST month/hour offsets"
    manifest = _training_manifest(fit_data, features, dataset_id, data_kind, start, cutoff, frozen,
        model_version=SPATIAL_VERSION, feature_contract="terra-hourly-features-v4", reference_method=method,
        minimum_neighbors=policy.min_neighbors)
    manifest.update(settings, spatial_method="pair-adjusted-median-v1", geometry_sha256=_geometry_digest(geometry),
        time_match="exact verified UTC hour; no nearest-time or carried-forward substitution",
        historical_fallback="disabled; historical-only reliability has not been established",
        elevation_handling="descriptive only; no fixed lapse-rate correction; pair offsets learn stable site differences",
        reference_selection="Pair median selected on reused autumn validation; distance weighting did not improve all stations",
        threshold_selection="Inherited empirical quantile policy recalibrated for new residuals on autumn validation only")
    manifest["range_calibration"]["reference_method"] = method
    return _finish(baselines, manifest)


def _training_manifest(data, features, dataset_id, data_kind, start, cutoff, frozen, *,
                       model_version=VERSION, feature_contract="terra-hourly-features-v3",
                       reference_method="current usable peer median plus frozen training fixed-PST month/hour offset",
                       minimum_neighbors=3):
    counts, bounds, calibration_counts = {}, {}, {}
    for station in STATIONS:
        selected = features[features.station_id.eq(station)]
        counts[station] = {}
        for label, lo, hi, minimum in (("train", start, cutoff, 120), ("validation", cutoff, frozen, 72)):
            window = selected[selected.timestamp.ge(lo) & selected.timestamp.lt(hi)]
            info = _window_counts(window, lo, hi, minimum_neighbors)
            if info["current_comparison_hours"] < minimum or (data_kind == "real" and
                (info["reference_coverage"] < .8 or min(month["reference_coverage"] for month in info["monthly_valid_hours"].values()) < .5)):
                raise ValueError(f"Station {station}: insufficient {label} reference coverage")
            counts[station][label] = info
        values = selected[selected.timestamp.ge(cutoff) & selected.timestamp.lt(frozen)].residual.dropna().to_numpy()
        low, high = map(float, np.quantile(values, [.025, .975]))
        raw_outer = float(np.quantile(np.abs(values), .999))
        bounds[station] = {"typical_low_residual_f": low, "typical_high_residual_f": high,
            "extreme_abs_residual_f": max(raw_outer, abs(low), abs(high)), "raw_abs_residual_p999_f": raw_outer}
        calibration_counts[station] = {"current_comparison_hours": len(values),
            "expected_hours": counts[station]["validation"]["expected_hours"],
            "coverage": counts[station]["validation"]["reference_coverage"]}
    manifest = {"model_version": model_version, "dataset_id": dataset_id, "data_kind": data_kind,
        "feature_contract": feature_contract, "train_start": start.isoformat(),
        "train_end_exclusive": cutoff.isoformat(), "validation_end_exclusive": frozen.isoformat(),
        "fit_data_sha256": _digest(data), "reference_method": reference_method,
        "decision_policy": "one extreme departure or three consecutive departures outside empirical residual bounds", "counts": counts,
        "minimum_real_reference_coverage": .8, "minimum_real_month_coverage": .5,
        "availability_policy": availability_policy(),
        "python_version": platform.python_version(), "pandas_version": pd.__version__, "numpy_version": np.__version__,
        "range_calibration": {"version": "empirical-residual-v1", "start": cutoff.isoformat(),
            "end_exclusive": frozen.isoformat(), "calibration_date": frozen.date().isoformat(),
            "calibration_date_basis": "validation data cutoff", "data_sha256": _digest(data[data.timestamp.ge(cutoff)]),
            "typical_quantiles": [.025, .975], "extreme_abs_quantile": .999, "bounds": bounds, "counts": calibration_counts,
            "expected_definition": reference_method,
            "decision_policy": "one extreme departure or three consecutive unusual departures",
            "boundary_policy": "strict outside bounds; outer absolute bound encloses both typical edges",
            "caveat": "Empirical autumn marginal ranges; no guaranteed winter, hourly or sensor-failure coverage",
            "winter_status": "Previously inspected winter is regression evidence, not untouched evaluation"}}
    return manifest


def score(frame, reference: Reference, *, as_of=None, max_age_hours=None):
    """Current peer comparison is available independently of temporal-history gaps.

    Supplying as_of selects current eligibility with the frozen age default;
    retrospective scoring without as_of never ages records against today.
    """
    if not isinstance(reference, Reference):
        raise ValueError("Use a statistical Reference")
    _validate_reference(reference)
    from terra_qc import temporal
    policy = reference.manifest.get("availability_policy", availability_policy())
    if as_of is not None and max_age_hours is None:
        max_age_hours = policy["current_max_age_hours"]
    prepared = prepare_readings(frame, as_of=as_of, max_age_hours=max_age_hours, policy=policy)
    result = _features(_raw(prepared), reference.baselines, reference.manifest)
    if "expected_f" not in result:
        result["expected_f"] = result.neighbor_f + result.expected_departure_f
    result["level"], result["status"] = "none", "source_unusable"
    result["range_status"], result["reference_status"] = "unavailable", "unavailable"
    decision = reference.manifest.get("temporal_policy", temporal.default_policy(3))
    result["required_readings"] = decision["variables"]["temperature_f"]["sustained_readings"]
    result["reference_id"] = reference.manifest["model_id"]
    result["temporal_policy_version"] = decision["version"] if "temporal_policy" in reference.manifest else "implicit-calibrated-three-readings"
    usable = result.available & result.quality.eq("usable") & np.isfinite(result.temperature_f)
    result.loc[~usable, "status"] = result.loc[~usable, "availability_reason"]
    minimum_neighbors = reference.manifest.get("spatial_policy", {}).get("min_neighbors", 3)
    result.loc[usable & result.neighbor_count.lt(minimum_neighbors), "status"] = "insufficient_neighbors"
    for column in ("typical_low_f", "typical_high_f", "extreme_low_f", "extreme_high_f", "sustained_low_f", "sustained_high_f",
                   "sustained_low_residual_f", "sustained_high_residual_f", "extreme_abs_residual_f"):
        result[column] = np.nan
    for station in STATIONS:
        limits = reference.manifest["range_calibration"]["bounds"][station]
        selected = temporal.resolve(decision, station, limits)
        valid = result.station_id.eq(station) & usable & result.neighbor_count.ge(minimum_neighbors) & result.expected_f.notna()
        for column, offset in (("typical_low_f", limits["typical_low_residual_f"]),
            ("typical_high_f", limits["typical_high_residual_f"]),
            ("extreme_low_f", -selected["extreme_abs_residual_f"]), ("extreme_high_f", selected["extreme_abs_residual_f"]),
            ("sustained_low_f", selected["sustained_low_residual_f"]), ("sustained_high_f", selected["sustained_high_residual_f"])):
            result.loc[valid, column] = result.loc[valid, "expected_f"] + offset
        for field in temporal.THRESHOLDS:
            result.loc[valid, field] = selected[field]
        result.loc[valid, ["status", "reference_status", "range_status"]] = ["reference_available", "available", "typical"]
        unusual = valid & (result.temperature_f.lt(result.typical_low_f) | result.temperature_f.gt(result.typical_high_f))
        extreme = valid & (result.temperature_f.lt(result.extreme_low_f) | result.temperature_f.gt(result.extreme_high_f))
        sustained = valid & (result.temperature_f.lt(result.sustained_low_f) | result.temperature_f.gt(result.sustained_high_f))
        result.loc[unusual, "range_status"] = "unusual"
        result.loc[sustained, "level"] = "moderate"
        result.loc[extreme, ["range_status", "level"]] = ["extreme", "strong"]
    result = result.reset_index(drop=True)
    result.attrs["cleaning_audit"] = prepared.attrs["cleaning_audit"]
    return result


def episodes(scored, station_id):
    """Immediate extremes or configured contiguous departures; revealed evidence only."""
    rows = scored[scored.station_id.eq(station_id)].sort_values("timestamp")
    return _episodes_for_rows(rows.to_dict("records"), station_id)


def _episodes_for_rows(rows, station_id):
    """Same rules for preconverted, sorted single-station replay prefixes."""
    findings, run, active, previous, previous_signature = [], [], None, None, None
    for row in rows:
        now = pd.Timestamp(row["timestamp"])
        contiguous = previous is not None and now - previous == pd.Timedelta(hours=1)
        required = row.get("required_readings", 3)
        if isinstance(required, (bool, np.bool_)) or not isinstance(required, (int, np.integer)) or required < 2:
            raise ValueError("Scored persistence must be an integer of at least two")
        signature = (row.get("reference_id"), int(required))
        reference = row.get("reference_status")
        eligible = reference == "available" if reference is not None else row["status"] == "scored"
        eligible = eligible and row["status"] not in {"source_unusable", "insufficient_neighbors"}
        anomalous = eligible and row["level"] in {"moderate", "strong"}
        if not contiguous or not anomalous or signature != previous_signature:
            run, active = [], None
        if anomalous:
            run.append(row)
            if active is not None:
                active["end"] = now.isoformat()
                active["evidence"].append(_evidence(row))
                if row["level"] == "strong":
                    active["severity"] = "strong"
            elif row["level"] == "strong" or len(run) >= required:
                active = {"station_id": int(station_id), "start": pd.Timestamp(run[0]["timestamp"]).isoformat(),
                          "end": now.isoformat(), "detected_at": now.isoformat(),
                          "severity": "strong" if row["level"] == "strong" else "moderate",
                          "evidence": [_evidence(item) for item in run]}
                findings.append(active)
            if active is not None:
                evidence = active["evidence"]
                peak = max(evidence, key=lambda e: abs(e["residual_f"]))
                active["reason"] = "extreme_departure" if active["severity"] == "strong" else "sustained_departure"
                active["reason_detail"] = ("A reading exceeded the outer residual limit." if active["severity"] == "strong"
                    else f"{required} consecutive usable readings exceeded the configured residual band.")
                active["policy"] = {"version": row.get("temporal_policy_version", "implicit-three-readings"),
                    "variable": "temperature_f", "units": "F", "sustained_readings": int(required), "extreme_readings": 1,
                    **{field: _number(row.get(field)) for field in ("sustained_low_residual_f", "sustained_high_residual_f", "extreme_abs_residual_f")}}
                active["as_of"] = now.isoformat()
                active["summary"] = {"observed_f": peak["temperature_f"], "expected_f": peak["expected_f"],
                    "typical_low_f": peak["typical_low_f"], "typical_high_f": peak["typical_high_f"],
                    "departure_f": peak["residual_f"], "peer_actual_f": peak["neighbor_f"],
                    "peer_change_f": peak["neighbor_change_f"], "target_change_f": peak["temperature_change_f"],
                    "neighbor_count": peak["neighbor_count"], "duration_hours": len(evidence),
                    "duration_readings": len(evidence), "required_readings": int(required),
                    "duration_basis": "consecutive hourly readings", "start": active["start"],
                    "detected_at": active["detected_at"], "as_of": active["as_of"], "reason": active["reason"],
                    "original_source_flag": peak["original_source_flag"], "source_quality": peak["source_quality"],
                    "source_flag_known": peak["source_flag_known"],
                    "user_action": "Check station reading against nearby stations before relying on it"}
                reference_text = (f"Expected {peak['expected_f']:.1f}°F; typical range "
                    f"{peak['typical_low_f']:.1f}–{peak['typical_high_f']:.1f}°F. "
                    if peak["typical_low_f"] is not None else "")
                active["message"] = (f"Suspected temperature anomaly: station {station_id} read "
                    f"{peak['temperature_f']:.1f}°F ({peak['residual_f']:+.1f}°F departure). "
                    f"{reference_text}Peer median was {peak['neighbor_f']:.1f}°F from "
                    f"{peak['neighbor_count']} usable peers; {len(evidence)} consecutive hourly readings supported "
                    f"this finding. Hourly changes: station {_change(peak['temperature_change_f'])}; "
                    f"peer median {_change(peak['neighbor_change_f'])}. "
                    "Check station reading against nearby stations before relying on it. "
                    "This is unusual behavior, not confirmed sensor failure.")
        previous = now
        previous_signature = signature
    return findings


def _number(value):
    return float(value) if value is not None and not pd.isna(value) and np.isfinite(value) else None


def _change(value):
    return f"{value:+.1f}°F" if value is not None else "unavailable"


def _evidence(row):
    source_flag = row.get("source_flag")
    source_flag = str(source_flag) if source_flag is not None and not pd.isna(source_flag) else None
    peers = row.get("peer_evidence")
    exclusions = row.get("peer_exclusions")
    if peers is not None:
        from terra_qc.spatial import evidence_payload, exclusions_payload
        peers = evidence_payload(peers)
        exclusions = exclusions_payload(exclusions)
    return {"timestamp": pd.Timestamp(row["timestamp"]).isoformat(),
            "reference_id": row.get("reference_id"),
            "temporal_policy_version": row.get("temporal_policy_version"),
            "required_readings": int(row.get("required_readings", 3)),
            **{field: _number(row.get(field)) for field in ("sustained_low_f", "sustained_high_f",
                "sustained_low_residual_f", "sustained_high_residual_f", "extreme_abs_residual_f")},
            "reference_method": row.get("reference_method", "peer_median_plus_station_offset"),
            "peer_evidence": peers,
            "peer_exclusions": exclusions,
            "expected_change_f": _number(row.get("expected_change_f")),
            "observation_available": bool(row.get("available", True)),
            "availability_reason": row.get("availability_reason", "available"),
            "cleaner_version": row.get("cleaner_version"),
            "temperature_f": float(row["temperature_f"]), "neighbor_f": float(row["neighbor_f"]),
            "neighbor_count": int(row["neighbor_count"]), "residual_f": float(row["residual"]),
            "temperature_change_f": _number(row.get("temperature_change_f")),
            "neighbor_change_f": _number(row.get("neighbor_change_f")),
            "expected_f": _number(row.get("expected_f")), "typical_low_f": _number(row.get("typical_low_f")),
            "typical_high_f": _number(row.get("typical_high_f")), "extreme_low_f": _number(row.get("extreme_low_f")),
            "extreme_high_f": _number(row.get("extreme_high_f")), "range_state": row.get("range_status"),
            "range_status": row.get("range_status"),
            "trigger": "extreme_departure" if row["level"] == "strong" else
                "outside_sustained_threshold" if row.get("temporal_policy_version") == "terra-temporal-v1" else "outside_typical_range",
            "original_source_flag": source_flag, "source_flag_known": source_flag is not None,
            "source_quality": row.get("quality", "unknown"), "level": row["level"]}


def inject_scenario(frame, station_id, scenario, *, start=REPLAY_START, end=REPLAY_END):
    """Return a separate deterministic copy plus explicit event provenance."""
    aliases = {"unmodified weather": "unmodified", "minor fluctuation": "minor",
               "sharp spike": "spike", "gradual drift": "drift"}
    scenario = aliases.get(scenario.lower(), scenario.lower())
    if scenario not in {"unmodified", "minor", "spike", "drift"} or station_id not in STATIONS:
        raise ValueError("Unknown scenario or station")
    data = _canonical(prepare_readings(frame))
    event = {"scenario": scenario, "simulated": scenario != "unmodified", "station_id": int(station_id),
             "start": None, "end": None, "injected_hours": 0, "version": "terra-scenarios-v1"}
    if scenario == "unmodified":
        return data, event
    onset = pd.Timestamp(start) + pd.Timedelta(hours=24)
    eligible = data.station_id.eq(station_id) & data.timestamp.ge(onset) & data.timestamp.lt(pd.Timestamp(end))
    eligible &= data.quality.eq("usable") & np.isfinite(data.temperature_f)
    eligible &= data.available
    indices = data.index[eligible]
    changes = {"minor": np.array([.4, 1., .4]), "spike": np.array([24., 24.]),
               "drift": np.linspace(.5, 18., 36)}[scenario]
    indices = indices[:len(changes)]
    if len(indices) != len(changes) or not data.loc[indices, "timestamp"].diff().iloc[1:].eq(pd.Timedelta(hours=1)).all():
        raise ValueError("Scenario needs a contiguous usable event interval after 24 warmup hours")
    data.loc[indices, "temperature_f"] += changes
    event.update(start=data.loc[indices[0], "timestamp"].isoformat(),
                 end=data.loc[indices[-1], "timestamp"].isoformat(), injected_hours=len(indices),
                 max_added_f=float(max(changes)))
    return data, event


def evaluate(frame, reference: Reference):
    """Report actual held-out volume and controlled event response, without tuning."""
    frozen = pd.Timestamp(reference.manifest["validation_end_exclusive"])
    winter = pd.Timestamp(WINTER_END)
    data = _canonical(prepare_readings(frame))
    held_data = data[data.timestamp.ge(frozen) & data.timestamp.lt(winter)]
    if held_data.empty:
        raise ValueError("No held-out winter readings provided")
    context = data[data.timestamp.ge(frozen - pd.Timedelta(hours=7)) & data.timestamp.lt(winter)]
    scored = score(context, reference)
    held = scored[scored.timestamp.ge(frozen) & scored.timestamp.lt(winter)]
    report = {"model_id": reference.manifest["model_id"], "data_kind": reference.manifest["data_kind"],
              "evaluation_sha256": _digest(held_data),
              "start": frozen.isoformat(), "end_exclusive": winter.isoformat(),
              "tuned_on_winter": False,
              "evaluation_status": "inspected_winter_regression" if "range_calibration" in reference.manifest else "temporal_evaluation",
              "decision_policy_developed_after_winter_inspection": "range_calibration" in reference.manifest,
              "independently_held_out": False,
              "evaluation_use": "regression on previously inspected winter; not an untouched holdout",
              "coverage_metric": "current reference availability",
              "unmodified": {}, "synthetic_events": []}
    baseline_findings = {}
    for station_id in STATIONS:
        station = held[held.station_id.eq(station_id)]
        baseline_findings[station_id] = episodes(held, station_id)
        expected = int((winter - frozen) / pd.Timedelta(hours=1))
        report["unmodified"][station_id] = {"hours": len(station), "expected_hours": expected,
            "reference_hours": int(station.reference_status.eq("available").sum()),
            "reference_coverage": float(station.reference_status.eq("available").sum() / expected),
            "source_flagged_hours": int(station.quality.eq("flagged").sum()),
            "source_quality_counts": {str(key): int(value) for key, value in station.quality.value_counts().items()},
            "source_flags_are_fault_labels": False,
            "flagged_hours": int(station.level.isin(["moderate", "strong"]).sum()),
            "episodes": len(baseline_findings[station_id])}
    # Event experiments reuse untouched January readings, always labeled synthetic.
    for station_id in STATIONS:
        for scenario in ("minor", "spike", "drift"):
            try:
                changed, event = inject_scenario(context, station_id, scenario)
            except ValueError as error:
                report["synthetic_events"].append({"scenario": scenario, "station_id": station_id,
                                                    "status": "unavailable", "reason": str(error)})
                continue
            event_findings = episodes(score(changed, reference), station_id)
            start, end = pd.Timestamp(event["start"]), pd.Timestamp(event["end"])
            related = [f for f in event_findings if pd.Timestamp(f["end"]) >= start
                       and pd.Timestamp(f["start"]) <= end]
            baseline_window_alert = any(pd.Timestamp(f["end"]) >= start
                and pd.Timestamp(f["start"]) <= end for f in baseline_findings[station_id])
            # Existing episodes do not count as a new detection caused by injection.
            detection = min((pd.Timestamp(f["detected_at"]) for f in related
                             if pd.Timestamp(f["detected_at"]) >= start), default=None)
            report["synthetic_events"].append({**event, "status": "evaluated", "detected": detection is not None,
                "baseline_window_alert": baseline_window_alert,
                "incremental_detection": detection is not None and not baseline_window_alert,
                "detection_delay_hours": (detection - start).total_seconds() / 3600 if detection is not None else None})
    return report
