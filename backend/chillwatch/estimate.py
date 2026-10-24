"""Hourly temperature estimates for a place with no station, in the Terra handoff's output shape.

One function, :func:`estimate_location`. The caller supplies the station observations, their
quality findings and the station geometry (for Terra: its normalized observations and QC
findings); nothing here fetches data or runs station QC. The estimate is the fitted physics
used for places with no station: 1/d^2 IDW over the k nearest stations, weight
exp(-(dz/scale)^2), and the hourly Theil-Sen lapse x shrink. The lapse is fitted only from
usable stations within ``max_km`` of the location, and only when they span enough elevation
for a slope to mean anything; the stations it used are listed in ``lapse_station_ids``.

An observation is used only if all of these hold:
* ``timestamp <= as_of`` and, when the column is given, ``visible_at <= as_of`` (a reading not
  yet visible is treated exactly like an absent one; unknown visibility counts as not visible);
* finite ``temperature_f`` inside the plausible range;
* ``quality == "usable"``;
* ``source_flag`` (stripped) in ``usable_flags``; blank only by default, matching Terra's QC;
* ``timestamp_verified`` is True, when that column is given;
* the station operates on the reading's PST hour-ending date;
* no finding for that station and hour (a finding is ignored only when its ``visible_at`` is
  after ``as_of``; unknown visibility counts as visible).

With a ``visible_at`` column, several rows for one station and hour are revisions and the latest
visible one is used; without it, such duplicates are an error. Stations in
``exclude_station_ids`` (the target, when testing at a station) are never read and are listed
in ``excluded`` as ``held_out_target``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

import numpy as np
import pandas as pd

from . import config
from .normalize import to_pst
from .qc.features import is_night
from .qc.neighbors import (
    adjusted_neighbor_temps,
    haversine_km,
    hour_local_dates,
    idw_baseline,
    neighbor_schedule,
    theil_sen_lapse,
)
from .repair import uncertainty as unc
from .timeutil import utc

ESTIMATE_COLUMNS = [
    "location_id", "lat", "lon", "elevation_ft", "geometry_source", "valid_utc", "temperature", "unit",
    "method", "method_version", "n_used", "used_station_ids", "excluded", "lapse_station_ids",
    "data_through_utc", "as_of_utc", "status", "status_reason", "p10", "p90",
]
METHOD_NAME = "chillwatch-idw"


def _params(usable_flags: Iterable[str] = ("",)) -> dict[str, Any]:
    th = config.thresholds()
    e = th.get("estimate", {})
    return {
        "k": int(th["neighbors"]["k"]),
        "max_km": float(th["neighbors"]["max_km"]),
        "min_distance_km": float(th["neighbors"]["min_distance_km"]),
        "lapse_min_stations": int(th["lapse"]["min_stations"]),
        "lapse_clip_f_per_kft": float(th["lapse"]["clip_f_per_kft"]),
        "lapse_shrink": float(th["baseline"].get("unseen_lapse_shrink", 1.0)),
        "dz_scale_ft": th["baseline"].get("unseen_dz_scale_ft"),
        "lapse_min_span_ft": float(e.get("lapse_min_span_ft", 0.0)),
        "min_peers": int(e.get("min_peers", 3)),
        "validated_months": sorted(int(m) for m in e.get("validated_months", range(1, 13))),
        "range_f": [float(th["range"]["min_f"]), float(th["range"]["max_f"])],
        "usable_flags": sorted({str(f).strip() for f in usable_flags}),
        "intervals": bool(e.get("intervals", False)),
        "interval_bins": _bins_hash() if e.get("intervals", False) else None,
    }


def _bins_hash() -> str | None:
    """Short hash of the p10/p90 bin table and its bin edges (None when not fitted yet)."""
    bins = unc.physics_bins()
    if bins is None:
        return None
    u = config.thresholds()["uncertainty"]
    blob = json.dumps({"table": bins, "distance_bins_km": u["distance_bins_km"], "dz_bins_ft": u["dz_bins_ft"]},
                      sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def method_version(params: dict[str, Any] | None = None) -> str:
    """Name plus a short hash of every parameter that changes the estimate."""
    p = params or _params()
    h = hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest()[:12]
    return f"{METHOD_NAME}/{h}"


def _visible(col: pd.Series, as_of: pd.Timestamp) -> pd.Series:
    return pd.to_datetime(col, utc=True) <= as_of  # NaT -> False


def _prepare_observations(observations: pd.DataFrame, stations: set[int], as_of: pd.Timestamp) -> pd.DataFrame:
    for c in ("station_id", "timestamp", "temperature_f", "quality", "source_flag"):
        if c not in observations:
            raise KeyError(f"observations need a {c!r} column")
    obs = observations.copy()
    obs["station_id"] = obs["station_id"].astype(int)
    obs["timestamp"] = pd.to_datetime(obs["timestamp"], utc=True)
    obs = obs[obs["station_id"].isin(stations) & (obs["timestamp"] <= as_of)]
    if "visible_at" in obs:
        obs["visible_at"] = pd.to_datetime(obs["visible_at"], utc=True)
        obs = obs[_visible(obs["visible_at"], as_of)]
        obs = obs.sort_values("visible_at", kind="stable").drop_duplicates(["station_id", "timestamp"], keep="last")
    elif obs.duplicated(["station_id", "timestamp"]).any():
        raise ValueError("observations have several rows for one station and hour; add visible_at to mark revisions")
    if "timestamp_verified" in obs and not pd.api.types.is_bool_dtype(obs["timestamp_verified"]):
        raise TypeError("timestamp_verified must be boolean")
    return obs


def _exclusion_reasons(obs: pd.DataFrame, findings: pd.DataFrame | None, as_of: pd.Timestamp,
                       usable_flags: list[str], range_f: list[float]) -> pd.Series:
    """Per observation row: "" if usable, else the first reason it is not."""
    t = pd.to_numeric(obs["temperature_f"], errors="coerce")
    flag = obs["source_flag"].fillna("").astype(str).str.strip()
    quality = obs["quality"].fillna("").astype(str)
    reason = pd.Series("", index=obs.index, dtype=object)

    def mark(mask: pd.Series, text: Any) -> None:
        m = mask & reason.eq("")
        reason[m] = text[m] if isinstance(text, pd.Series) else text

    mark(t.isna(), "missing")
    mark(quality.ne("usable"), "quality:" + quality)
    mark(~flag.isin(usable_flags), "source_flag:" + flag)
    if "timestamp_verified" in obs:
        mark(~obs["timestamp_verified"].fillna(False).astype(bool), "timestamp_unverified")
    mark((t < range_f[0]) | (t > range_f[1]), "out_of_range")
    if findings is not None and len(findings):
        f = findings.copy()
        if "visible_at" in f:
            f = f[~(pd.to_datetime(f["visible_at"], utc=True) > as_of)]  # NaT -> visible
        f = f.assign(station_id=f["station_id"].astype(int), timestamp=pd.to_datetime(f["timestamp"], utc=True),
                     reason=f["reason"].fillna("").astype(str) if "reason" in f else "")
        f = f.drop_duplicates(["station_id", "timestamp"])
        hit = pd.Series(f["reason"].to_numpy(), index=pd.MultiIndex.from_arrays([f["station_id"], f["timestamp"]]))
        hit = hit.reindex(pd.MultiIndex.from_arrays([obs["station_id"], obs["timestamp"]]))
        found = pd.Series(hit.notna().to_numpy(), index=obs.index)
        mark(found, "terra_finding:" + pd.Series(hit.fillna("").to_numpy(), index=obs.index))
    return reason


def estimate_location(
    lat: float,
    lon: float,
    elevation_ft: float,
    hours: Iterable[Any],
    as_of: Any,
    observations: pd.DataFrame,
    stations: pd.DataFrame,
    findings: pd.DataFrame | None = None,
    *,
    location_id: str | None = None,
    geometry_source: str = "unspecified",
    exclude_station_ids: Iterable[int] = (),
    usable_flags: Iterable[str] = ("",),
    max_age_hours: float | None = None,
) -> pd.DataFrame:
    """Estimated air temperature at (lat, lon, elevation_ft) for each UTC hour-ending ``hours``.

    ``observations``: station_id, timestamp (UTC hour-ending), temperature_f, quality, source_flag;
    optional timestamp_verified (bool), visible_at. ``stations``: station_id, lat, lon, elevation_ft;
    optional connect_date, disconnect_date. ``findings``: station_id, timestamp, reason; optional
    visible_at. Returns one row per distinct hour, ascending, with :data:`ESTIMATE_COLUMNS`.
    ``status`` is ``ok``, ``stale`` (valid hour older than ``max_age_hours`` before ``as_of``) or
    ``insufficient_data`` (temperature NaN). ``p10``/``p90`` are filled only when ``estimate.intervals``
    is on and calibrated bins exist (docs/TERRA_COMPATIBILITY.md gives their tested coverage).
    """
    if isinstance(exclude_station_ids, (str, bytes)):
        raise TypeError("exclude_station_ids must be a collection of station ids, not a string")
    as_of = utc(as_of) if as_of is not None else pd.NaT
    if pd.isna(as_of):
        raise ValueError("as_of is required")
    if not np.isfinite(float(elevation_ft)):
        raise ValueError("elevation_ft must be finite")
    p = _params(usable_flags)
    version = method_version(p)
    hrs = pd.DatetimeIndex(sorted({utc(h) for h in hours}), tz="UTC")
    location_id = location_id or f"TEST-{lat:.4f},{lon:.4f}"
    excl = {int(s) for s in exclude_station_ids}

    st = stations.copy()
    st["station_id"] = st["station_id"].astype(int)
    if st["station_id"].duplicated().any():
        raise ValueError("stations has duplicate station_id rows")
    if not np.isfinite(st["elevation_ft"].to_numpy(dtype="float64")).all():
        raise ValueError("every station needs a finite elevation_ft")
    for c in ("connect_date", "disconnect_date"):
        if c not in st:
            st[c] = pd.NaT
    d_all = haversine_km(lat, lon, st["lat"].to_numpy(dtype="float64"), st["lon"].to_numpy(dtype="float64"))
    held_out = [{"station_id": int(s), "reason": "held_out_target"}
                for s, d in zip(st["station_id"], d_all, strict=True) if int(s) in excl and d <= p["max_km"]]
    st = st[~st["station_id"].isin(excl)].sort_values("station_id").reset_index(drop=True)
    ids = st["station_id"].to_numpy()
    pos = {int(s): i for i, s in enumerate(ids)}
    H, S = len(hrs), len(ids)
    if H == 0:
        return pd.DataFrame(columns=ESTIMATE_COLUMNS)

    obs = _prepare_observations(observations, set(pos), as_of)
    if len(obs):
        # Apply the neighbor schedule's operating dates before both lapse fitting and
        # provenance. In particular, midnight PST ends the previous operating day.
        days = pd.Series(hour_local_dates(pd.DatetimeIndex(obs["timestamp"])), index=obs.index)
        station_dates = st.set_index("station_id")
        conn = pd.to_datetime(obs["station_id"].map(station_dates["connect_date"]))
        disc = pd.to_datetime(obs["station_id"].map(station_dates["disconnect_date"]))
        obs = obs[(conn.isna() | (conn <= days)) & (disc.isna() | (days <= disc))]
    why_row = _exclusion_reasons(obs, findings, as_of, p["usable_flags"], p["range_f"]) if len(obs) \
        else pd.Series(dtype=object)
    W = np.full((H, S), np.nan)
    why: dict[tuple[int, int], str] = {}
    through = pd.Series(dtype=object)
    if len(obs):
        ti = hrs.get_indexer(pd.DatetimeIndex(obs["timestamp"]))
        si = obs["station_id"].map(pos).to_numpy()
        ok = why_row.eq("").to_numpy()
        t = pd.to_numeric(obs["temperature_f"], errors="coerce").to_numpy(dtype="float64")
        on = ti >= 0
        W[ti[on & ok], si[on & ok]] = t[on & ok]
        for t_i, s_i, w in zip(ti[on & ~ok], si[on & ~ok], why_row.to_numpy()[on & ~ok], strict=True):
            why[(int(t_i), int(s_i))] = str(w)
        usable = obs[ok]
        if len(usable):
            through = usable.groupby("station_id")["timestamp"].max()

    # lapse: only stations within max_km of the location, gated on their elevation span
    z = st["elevation_ft"].to_numpy(dtype="float64")
    near = np.flatnonzero(haversine_km(lat, lon, st["lat"], st["lon"]) <= p["max_km"]) if S else np.array([], int)
    gamma = np.full(H, np.nan)
    lapse_valid = np.zeros((H, len(near)), dtype=bool)
    if len(near):
        Wn = W[:, near]
        lapse_valid = np.isfinite(Wn)
        gamma = theil_sen_lapse(Wn, z[near], min_stations=p["lapse_min_stations"], clip=p["lapse_clip_f_per_kft"])
        if p["lapse_min_span_ft"] > 0:
            zz = np.where(lapse_valid, z[near][None, :], np.nan)
            any_valid = lapse_valid.any(axis=1)
            span = np.zeros(H)
            span[any_valid] = np.nanmax(zz[any_valid], axis=1) - np.nanmin(zz[any_valid], axis=1)
            gamma = np.where(span >= p["lapse_min_span_ft"], gamma, np.nan)
    if not p["lapse_shrink"]:
        gamma = np.full(H, np.nan)

    if S:
        idx, dist, dz = neighbor_schedule(lat, lon, elevation_ft, st, hrs, pos, k=p["k"], max_km=p["max_km"])
        adj = adjusted_neighbor_temps(W, idx, dz, gamma, p["lapse_shrink"])
        est, _ = idw_baseline(adj, dist, min_neighbors=1, min_distance_km=p["min_distance_km"], dz_ft=dz,
                              dz_scale_ft=p["dz_scale_ft"])
    else:
        idx, adj, est = np.full((H, 0), -1), np.full((H, 0), np.nan), np.full(H, np.nan)

    # p10/p90 only when enabled (estimate.intervals) and the physics bins exist; NaN otherwise
    q10 = q90 = np.full(H, np.nan)
    bins = unc.physics_bins() if p["intervals"] else None
    if bins is not None and S:
        valid = np.isfinite(adj)
        first = np.where(valid.any(axis=1), valid.argmax(axis=1), -1)
        jj = np.clip(first, 0, None)
        rows_ = np.arange(H)
        nk = np.where(first >= 0, dist[rows_, jj], np.nan)
        nz = np.where(first >= 0, np.abs(dz[rows_, jj]), np.nan)
        _, q10, q90 = unc.lookup(bins, nk, nz, is_night(hrs))
    months = to_pst(hrs - pd.Timedelta(hours=1)).month  # hour-ending: 00:00 belongs to the previous day
    rows = []
    for h in range(H):
        used, excluded = [], list(held_out)
        for j in range(idx.shape[1]):
            col = int(idx[h, j])
            if col < 0:
                continue
            sid = int(ids[col])
            if np.isfinite(adj[h, j]):
                used.append(sid)
            else:
                excluded.append({"station_id": sid, "reason": why.get((h, col), "missing")})
        lapse_ids = [int(ids[near[i]]) for i in np.flatnonzero(lapse_valid[h])] if np.isfinite(gamma[h]) else []
        valid = hrs[h]
        if valid > as_of:
            status, reason = "insufficient_data", "after_as_of"
        elif int(months[h]) not in p["validated_months"]:
            status, reason = "insufficient_data", "outside_validated_months"
        elif len(used) < p["min_peers"]:
            status, reason = "insufficient_data", f"fewer_than_{p['min_peers']}_usable_peers"
        elif not np.isfinite(est[h]):
            status, reason = "insufficient_data", "no_finite_estimate"
        elif max_age_hours is not None and (as_of - valid) > pd.Timedelta(hours=float(max_age_hours)):
            status, reason = "stale", f"older_than_{max_age_hours}h_before_as_of"
        else:
            status, reason = "ok", ""
        rows.append({
            "location_id": location_id, "lat": float(lat), "lon": float(lon), "elevation_ft": float(elevation_ft),
            "geometry_source": geometry_source, "valid_utc": valid,
            "temperature": float(est[h]) if status != "insufficient_data" else np.nan,
            "unit": "degF", "method": "idw_lapse" if lapse_ids else "idw", "method_version": version,
            "n_used": len(used), "used_station_ids": used, "excluded": excluded, "lapse_station_ids": lapse_ids,
            "data_through_utc": {s: through[s].isoformat() for s in sorted(set(used) | set(lapse_ids))
                                 if s in through.index},
            "as_of_utc": as_of, "status": status, "status_reason": reason,
            "p10": float(est[h] + q10[h]) if status != "insufficient_data" else np.nan,
            "p90": float(est[h] + q90[h]) if status != "insufficient_data" else np.nan,
        })
    return pd.DataFrame(rows, columns=ESTIMATE_COLUMNS)
