"""Physics layer: neighbor graph, hourly lapse rate (Theil-Sen) and IDW + lapse baseline.

Sign conventions
* ``dz_ft = target elevation - neighbor elevation``.
* ``gamma_h`` is dT/dz in degF per 1,000 ft (negative = normal lapse, positive = inversion).
* A neighbor's lapse-adjusted temperature is ``T_i + gamma_h * dz_i / 1000``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .. import config
from ..normalize import to_pst

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: Any, lon1: Any, lat2: Any, lon2: Any) -> np.ndarray:
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(x, dtype="float64")) for x in (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def active_on(stations: pd.DataFrame, day: Any) -> np.ndarray:
    """Station operating on PST calendar ``day`` per connect/disconnect dates."""
    d = pd.Timestamp(day).tz_localize(None) if pd.Timestamp(day).tzinfo else pd.Timestamp(day)
    d = d.normalize()
    conn = pd.to_datetime(stations["connect_date"])
    disc = pd.to_datetime(stations["disconnect_date"])
    ok = (conn.isna() | (conn <= d)) & (disc.isna() | (d <= disc))
    return ok.to_numpy()


def nearest_neighbors(
    lat: float,
    lon: float,
    elevation_ft: float,
    stations: pd.DataFrame,
    day: Any | None = None,
    k: int | None = None,
    max_km: float | None = None,
    exclude: set[int] | None = None,
) -> pd.DataFrame:
    """k nearest stations within ``max_km`` (active on ``day`` if given)."""
    th = config.thresholds()["neighbors"]
    k = k or int(th["k"])
    max_km = max_km or float(th["max_km"])
    st = stations
    mask = np.ones(len(st), dtype=bool)
    if day is not None:
        mask &= active_on(st, day)
    if exclude:
        mask &= ~st["station_id"].isin(list(exclude)).to_numpy()
    d = haversine_km(lat, lon, st["lat"].to_numpy(), st["lon"].to_numpy())
    mask &= d <= max_km
    out = pd.DataFrame(
        {
            "neighbor_id": st["station_id"].to_numpy()[mask].astype(int),
            "distance_km": d[mask],
            "dz_ft": float(elevation_ft) - st["elevation_ft"].to_numpy(dtype="float64")[mask],
        }
    )
    return out.sort_values(["distance_km", "neighbor_id"]).head(k).reset_index(drop=True)


def hour_local_dates(hours: pd.DatetimeIndex) -> np.ndarray:
    """PST calendar date (as datetime64[D]) each hour-ending timestamp belongs to."""
    return (to_pst(hours) - pd.Timedelta(hours=1)).normalize().to_numpy().astype("datetime64[D]")


def neighbor_schedule(
    lat: float,
    lon: float,
    elevation_ft: float,
    stations: pd.DataFrame,
    hours: pd.DatetimeIndex,
    station_pos: dict[int, int],
    k: int | None = None,
    max_km: float | None = None,
    exclude: set[int] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-hour neighbor columns, distances and dz, respecting connect/disconnect dates.

    Returns ``(idx, dist_km, dz_ft)``, each shaped (len(hours), k); idx is a column into
    the field matrix or -1.
    """
    th = config.thresholds()["neighbors"]
    k = k or int(th["k"])
    H = len(hours)
    idx = np.full((H, k), -1, dtype=np.int64)
    dist = np.full((H, k), np.nan)
    dz = np.full((H, k), np.nan)
    if H == 0:
        return idx, dist, dz
    days = hour_local_dates(hours)
    # change points: any connect/disconnect date inside the period
    bounds = {days[0]}
    for c in ("connect_date", "disconnect_date"):
        for v in pd.to_datetime(stations[c]).dropna():
            d0 = np.datetime64(v.normalize().date(), "D")
            if c == "disconnect_date":
                d0 = d0 + np.timedelta64(1, "D")  # inactive from the day after
            if days[0] < d0 <= days[-1]:
                bounds.add(d0)
    edges = sorted(bounds)
    for i, b in enumerate(edges):
        lo = np.searchsorted(days, b, side="left")
        hi = np.searchsorted(days, edges[i + 1], side="left") if i + 1 < len(edges) else H
        if hi <= lo:
            continue
        nb = nearest_neighbors(
            lat, lon, elevation_ft, stations, day=pd.Timestamp(b), k=k, max_km=max_km, exclude=exclude
        )
        nb = nb[nb["neighbor_id"].isin(station_pos.keys())]
        for j, row in enumerate(nb.itertuples(index=False)):
            idx[lo:hi, j] = station_pos[int(row.neighbor_id)]
            dist[lo:hi, j] = row.distance_km
            dz[lo:hi, j] = row.dz_ft
    return idx, dist, dz


def theil_sen_lapse(
    W: np.ndarray, elevation_ft: np.ndarray, min_stations: int | None = None, clip: float | None = None,
    chunk: int = 2048,
) -> np.ndarray:
    """Hourly Theil-Sen slope of temperature vs elevation, degF per 1,000 ft.

    ``W`` is (hours, stations) with NaN for invalid values. NaN where fewer than
    ``min_stations`` valid stations.
    """
    th = config.thresholds()["lapse"]
    min_stations = int(min_stations or th["min_stations"])
    clip = float(clip if clip is not None else th["clip_f_per_kft"])
    z = np.asarray(elevation_ft, dtype="float64")
    S = W.shape[1]
    ii, jj = np.triu_indices(S, k=1)
    dzp = z[jj] - z[ii]
    keep = np.isfinite(dzp) & (dzp != 0)
    ii, jj, dzp = ii[keep], jj[keep], dzp[keep]
    out = np.full(W.shape[0], np.nan)
    if len(ii) == 0:
        return out
    for s in range(0, W.shape[0], chunk):
        block = W[s : s + chunk]
        slopes = (block[:, jj] - block[:, ii]) / dzp * 1000.0
        med = row_nanmedian(slopes)
        n = np.isfinite(block).sum(axis=1)
        med[n < min_stations] = np.nan
        out[s : s + chunk] = med
    return np.clip(out, -clip, clip)


def row_nanmedian(a: np.ndarray) -> np.ndarray:
    """Median of each row ignoring NaN (sort-based; much faster than np.nanmedian on 2-D)."""
    srt = np.sort(a, axis=1)  # NaN sort last
    n = np.isfinite(srt).sum(axis=1)
    lo = np.clip((n - 1) // 2, 0, None)
    hi = np.clip(n // 2, 0, None)
    rows = np.arange(a.shape[0])
    med = (srt[rows, lo] + srt[rows, hi]) / 2.0
    med[n == 0] = np.nan
    return med


def adjusted_neighbor_temps(
    W: np.ndarray, idx: np.ndarray, dz: np.ndarray, gamma: np.ndarray
) -> np.ndarray:
    """(H, k) lapse-adjusted neighbor temperatures (NaN where neighbor missing/invalid)."""
    H = W.shape[0]
    rows = np.arange(H)[:, None]
    vals = np.where(idx >= 0, W[rows, np.clip(idx, 0, None)], np.nan)
    g = np.where(np.isfinite(gamma), gamma, 0.0)[:, None]
    return vals + g * dz / 1000.0


def idw_baseline(
    adj: np.ndarray, dist_km: np.ndarray, min_neighbors: int | None = None, min_distance_km: float | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """``T_base = sum(w_i * adj_i) / sum(w_i)``, ``w_i = 1/d_i^2`` over valid neighbors.

    Returns (T_base, n_valid); T_base is NaN when fewer than ``min_neighbors`` are valid.
    """
    th = config.thresholds()
    min_neighbors = int(min_neighbors or th["baseline"]["min_neighbors"])
    dmin = float(min_distance_km if min_distance_km is not None else th["neighbors"]["min_distance_km"])
    valid = np.isfinite(adj) & np.isfinite(dist_km)
    w = np.where(valid, 1.0 / np.maximum(np.nan_to_num(dist_km, nan=1.0), dmin) ** 2, 0.0)
    num = np.where(valid, adj, 0.0) * w
    sw = w.sum(axis=1)
    n = valid.sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        tb = num.sum(axis=1) / sw
    tb[n < min_neighbors] = np.nan
    return tb, n


@dataclass
class Field:
    """All stations' hourly values on a common hour grid (the input to physics and the model)."""

    hours: pd.DatetimeIndex
    stations: pd.DataFrame
    station_ids: np.ndarray
    pos: dict[int, int]
    W: np.ndarray  # valid temps (hours, stations)
    raw: np.ndarray  # observed temps regardless of status
    gamma: np.ndarray
    extras: dict[str, np.ndarray] = field(default_factory=dict)
    _gamma_ex: dict[frozenset[int], np.ndarray] = field(default_factory=dict, repr=False)

    def gamma_excluding(self, exclude: set[int] | None) -> np.ndarray:
        """Hourly lapse rate fitted without the given stations (so a target never informs its own features)."""
        cols = [self.pos[s] for s in (exclude or ()) if s in self.pos]
        if not cols:
            return self.gamma
        key = frozenset(cols)
        if key not in self._gamma_ex:
            W2 = self.W.copy()
            W2[:, cols] = np.nan
            self._gamma_ex[key] = theil_sen_lapse(W2, self.elevation)
        return self._gamma_ex[key]

    @property
    def elevation(self) -> np.ndarray:
        return self.stations["elevation_ft"].to_numpy(dtype="float64")

    def schedule(
        self, lat: float, lon: float, elevation_ft: float, exclude: set[int] | None = None
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        return neighbor_schedule(lat, lon, elevation_ft, self.stations, self.hours, self.pos, exclude=exclude)

    def station_row(self, station_id: int) -> pd.Series:
        return self.stations.iloc[self.pos[int(station_id)]]


def build_field(
    readings: pd.DataFrame,
    stations: pd.DataFrame,
    start: Any,
    end: Any,
    valid_col: str = "qc_base",
    valid_values: tuple[str, ...] = ("ok",),
    extras: tuple[str, ...] = ("rh_pct", "dewpoint_f", "solar_wm2", "wind_mph"),
    drop_ids: set[int] | None = None,
) -> Field:
    """Pivot readings onto an hourly grid [start, end] and fit the hourly lapse rate."""
    from ..timeutil import utc

    hours = pd.date_range(utc(start), utc(end), freq="h")
    st = stations.copy()
    if drop_ids:
        st = st[~st["station_id"].isin(list(drop_ids))]
    st = st.dropna(subset=["lat", "lon"]).sort_values("station_id").reset_index(drop=True)
    ids = st["station_id"].astype(int).to_numpy()
    pos = {int(s): i for i, s in enumerate(ids)}
    H, S = len(hours), len(ids)
    W = np.full((H, S), np.nan)
    raw = np.full((H, S), np.nan)
    ex = {c: np.full((H, S), np.nan) for c in extras if c in readings.columns}
    rng = config.thresholds()["range"]
    if len(readings):
        r = readings[readings["station_id"].isin(ids)]
        ti = hours.get_indexer(pd.DatetimeIndex(r["ts"]))
        keep = ti >= 0
        r = r[keep]
        ti = ti[keep]
        si = np.array([pos[int(s)] for s in r["station_id"].to_numpy()], dtype=np.int64)
        t = r["temp_f"].to_numpy(dtype="float64")
        raw[ti, si] = t
        ok = r[valid_col].isin(valid_values).to_numpy() & (t >= rng["min_f"]) & (t <= rng["max_f"])
        W[ti[ok], si[ok]] = t[ok]
        for c in ex:
            ex[c][ti, si] = r[c].to_numpy(dtype="float64")
    gamma = theil_sen_lapse(W, st["elevation_ft"].to_numpy(dtype="float64"))
    return Field(hours=hours, stations=st, station_ids=ids, pos=pos, W=W, raw=raw, gamma=gamma, extras=ex)


def baseline_for(fld: Field, station_id: int, exclude_self: bool = True) -> pd.DataFrame:
    """T_base for one station over the field's hours (diagnostic helper)."""
    row = fld.station_row(station_id)
    idx, dist, dz = fld.schedule(row.lat, row.lon, row.elevation_ft, exclude={int(station_id)})
    gamma = fld.gamma_excluding({int(station_id)})
    adj = adjusted_neighbor_temps(fld.W, idx, dz, gamma)
    tb, n = idw_baseline(adj, dist)
    return pd.DataFrame(
        {"ts": fld.hours, "t_base": tb, "n_neighbors": n, "gamma_h": gamma,
         "gamma_missing": ~np.isfinite(gamma)}
    )
