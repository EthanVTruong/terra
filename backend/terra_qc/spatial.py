"""Causal QC references at observed stations, with no location interpolation.

The caller slices training dates before ``fit_pairs``. Coordinates filter peers;
elevations are evidence only. Sparse month/hour support never uses a fallback.
Evidence uses compact tuples; serialize selected rows with ``evidence_payload``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta, timezone
import math
from numbers import Integral, Real

import numpy as np
import pandas as pd

VERSION = "terra-spatial-v1"
PST = timezone(timedelta(hours=-8))
PEER_FIELDS = ("station_id", "observed_f", "offset_f", "adjusted_f", "distance_km",
               "elevation_difference_m", "support")
EXCLUSION_FIELDS = ("station_id", "reason")


@dataclass(frozen=True)
class Policy:
    min_neighbors: int = 3
    min_pair_samples: int = 24
    max_distance_km: float = 75.0

    def __post_init__(self):
        if isinstance(self.min_neighbors, bool) or not isinstance(self.min_neighbors, Integral) or not 3 <= self.min_neighbors <= 4:
            raise ValueError("Require three or four simultaneous neighbors")
        if isinstance(self.min_pair_samples, bool) or not isinstance(self.min_pair_samples, Integral) or self.min_pair_samples < 1:
            raise ValueError("Pair sample minimum must be a positive integer")
        if isinstance(self.max_distance_km, bool) or not isinstance(self.max_distance_km, Real) or not math.isfinite(self.max_distance_km) or self.max_distance_km <= 0:
            raise ValueError("Peer radius must be finite and positive")


def _location(value):
    if not isinstance(value, dict):
        return None
    lat, lon = value.get("latitude"), value.get("longitude")
    if any(isinstance(number, bool) or not isinstance(number, Real) or not math.isfinite(number) for number in (lat, lon)):
        return None
    return (float(lat), float(lon)) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


def distance_km(a, b):
    """Great-circle distance from two geometry dictionaries, using mean Earth radius."""
    first, second = _location(a), _location(b)
    if first is None or second is None:
        raise ValueError("Distance needs finite latitude/longitude coordinates")
    lat1, lon1, lat2, lon2 = map(math.radians, (*first, *second))
    value = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(min(1., max(0., value))))


def _candidate(target, peer, geometry, policy):
    if target not in geometry:
        return None, "missing_target_geometry"
    if peer not in geometry:
        return None, "missing_peer_geometry"
    if _location(geometry[target]) is None:
        return None, "invalid_target_geometry"
    if _location(geometry[peer]) is None:
        return None, "invalid_peer_geometry"
    distance = distance_km(geometry[target], geometry[peer])
    return (None, "outside_radius") if distance > policy.max_distance_km else (distance, None)


def _frame(raw):
    required = {"station_id", "timestamp", "usable_f", "available"}
    if not required.issubset(raw.columns):
        raise ValueError("Spatial input needs a cleaned hourly QC grid")
    data = raw.copy()
    if data.empty:
        raise ValueError("No spatial observations")
    if not isinstance(data.timestamp.dtype, pd.DatetimeTZDtype):
        raise ValueError("Spatial timestamps must be aware")
    data["timestamp"] = data.timestamp.dt.tz_convert("UTC")
    if data.timestamp.isna().any() or not data.timestamp.eq(data.timestamp.dt.floor("h")).all():
        raise ValueError("Spatial timestamps must be exact UTC hours")
    if data.duplicated(["station_id", "timestamp"]).any():
        raise ValueError("Resolve station/hour duplicates before spatial comparison")
    if any(isinstance(station, bool) or not isinstance(station, Integral) for station in data.station_id.unique()):
        raise ValueError("Spatial station IDs must be integers")
    local = data.timestamp.dt.tz_convert(PST)
    for column, values in (("month", local.dt.month), ("hour", local.dt.hour)):
        if column in data and not data[column].eq(values).all():
            raise ValueError("Spatial month/hour must use fixed PST")
        data[column] = values
    numeric = pd.to_numeric(data.usable_f, errors="coerce")
    data["usable_f"] = numeric.where(data.available.eq(True).fillna(False) & np.isfinite(numeric))
    return data


def fit_pairs(raw, geometry, policy=Policy(), require_full_bins=True):
    """Learn target-minus-peer medians from caller-supplied training rows only.

    Real/full-bin fitting requires all 288 bins with at least 24 observations
    per bin (or the larger policy minimum). Synthetic sparse fitting retains
    only supported bins and uses the explicit policy sample minimum.
    """
    if not isinstance(policy, Policy):
        raise ValueError("Use a spatial Policy")
    data = _frame(raw)
    wide = data.pivot(index="timestamp", columns="station_id", values="usable_f").sort_index()
    local = wide.index.tz_convert(PST)
    groups = [local.month, local.hour]
    minimum = max(24, policy.min_pair_samples) if require_full_bins else policy.min_pair_samples
    baselines = {int(station): {"pairs": {}} for station in wide.columns}
    for target in wide.columns:
        for peer in wide.columns:
            if target == peer or _candidate(target, peer, geometry, policy)[1] is not None:
                continue
            grouped = (wide[target] - wide[peer]).groupby(groups)
            counts = grouped.count()
            medians = grouped.median()
            supported = counts.ge(minimum) & medians.notna()
            if require_full_bins and (len(counts) != 288 or not supported.all()):
                continue
            bins = {tuple(map(int, key)): float(value) for key, value in medians[supported].items()}
            if bins:
                baselines[int(target)]["pairs"][int(peer)] = {"bins": bins,
                    "counts": {tuple(map(int, key)): int(counts[key]) for key in medians[supported].index}}
    return baselines


def _elevation_difference(target, peer):
    values = (target.get("elevation_m"), peer.get("elevation_m"))
    if any(isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) for value in values):
        return None
    return float(values[0] - values[1])  # Target minus peer; never a temperature correction.


def evidence_payload(facts):
    """Selected-row evidence as JSON-safe dictionaries with explicit stable field IDs."""
    return [dict(zip(PEER_FIELDS, fact, strict=True)) for fact in facts]


def exclusions_payload(exclusions):
    return [dict(zip(EXCLUSION_FIELDS, item, strict=True)) for item in exclusions]


def features(raw, baselines, geometry, policy=Policy()):
    """Combine supported, available, same-hour peers; never carry a reading forward."""
    if not isinstance(policy, Policy):
        raise ValueError("Use a spatial Policy")
    data = _frame(raw)
    stations = sorted(set(data.station_id.unique()) | set(baselines))
    wide = data.pivot(index="timestamp", columns="station_id", values="usable_f").reindex(columns=stations)
    parts = []
    for target in sorted(data.station_id.unique()):
        part = data[data.station_id.eq(target)].sort_values("timestamp").copy()
        times = pd.DatetimeIndex(part.timestamp)
        peers = [station for station in stations if station != target]
        observed = wide.reindex(times)[peers].to_numpy(dtype=float)
        part["available_neighbor_count"] = np.isfinite(observed).sum(axis=1)
        adjusted = np.full(observed.shape, np.nan)
        eligible_observed = np.full(observed.shape, np.nan)
        row_facts, row_exclusions = [[] for _ in range(len(part))], [[] for _ in range(len(part))]
        keys = pd.MultiIndex.from_arrays([part.month, part.hour])
        target_pairs = baselines.get(target, {}).get("pairs", {})
        for column, peer in enumerate(peers):
            distance, exclusion = _candidate(target, peer, geometry, policy)
            pair = target_pairs.get(peer)
            if exclusion is not None or pair is None:
                fact = (int(peer), exclusion or "unsupported_pair")
                for items in row_exclusions:
                    items.append(fact)
                continue
            offsets = pd.Series(pair["bins"], dtype=float).reindex(keys).to_numpy()
            counts = pd.Series(pair["counts"], dtype=float).reindex(keys).fillna(0).to_numpy()
            supported = np.isfinite(offsets) & (counts >= policy.min_pair_samples)
            available = np.isfinite(observed[:, column])
            eligible = supported & available
            adjusted[eligible, column] = observed[eligible, column] + offsets[eligible]
            eligible_observed[eligible, column] = observed[eligible, column]
            elevation = _elevation_difference(geometry[target], geometry[peer])
            for index in np.flatnonzero(eligible):
                row_facts[index].append((int(peer), float(observed[index, column]), float(offsets[index]),
                    float(adjusted[index, column]), distance, elevation, int(counts[index])))
            for index in np.flatnonzero(~eligible):
                reason = "unsupported_month_hour" if not np.isfinite(offsets[index]) else "insufficient_pair_support" if not supported[index] else "peer_unavailable"
                row_exclusions[index].append((int(peer), reason))
        count = np.isfinite(adjusted).sum(axis=1)
        valid = count >= policy.min_neighbors
        expected, median = np.full(len(part), np.nan), np.full(len(part), np.nan)
        expected[valid] = np.nanmedian(adjusted[valid], axis=1)
        median[valid] = np.nanmedian(eligible_observed[valid], axis=1)
        part["neighbor_count"], part["neighbor_f"], part["expected_f"] = count, median, expected
        part["expected_departure_f"] = expected - median
        part["residual"] = part.usable_f.to_numpy(dtype=float) - expected
        part["reference_method"] = "pair_adjusted_median"
        part["peer_evidence"] = [tuple(items) for items in row_facts]
        part["peer_exclusions"] = [tuple(items) for items in row_exclusions]
        parts.append(part)
    return pd.concat(parts, ignore_index=True).sort_values(["timestamp", "station_id"]).reset_index(drop=True)
