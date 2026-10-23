"""Fault injection: pure functions returning (modified copy, ground-truth labels).

Never touches real tables: inputs are DataFrames (``station_id, ts, temp_f, ...``) and
outputs are new DataFrames.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from ..timeutil import season_bounds, utc


@dataclass
class FaultLabel:
    station_id: int
    type: str
    start_ts: pd.Timestamp
    end_ts: pd.Timestamp
    params: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["start_ts"] = self.start_ts.isoformat()
        d["end_ts"] = self.end_ts.isoformat()
        return d


def _sel(df: pd.DataFrame, station_id: int, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    return (df["station_id"] == station_id) & (df["ts"] >= start) & (df["ts"] <= end)


def _mark(df: pd.DataFrame, m: pd.Series) -> None:
    # the injected values look like ordinary observations to the pipeline
    df.loc[m & df["temp_f"].notna(), ["qc_base", "qc_status"]] = "ok"
    df.loc[m & df["temp_f"].isna(), ["qc_base", "qc_status"]] = "missing"


def outage(df: pd.DataFrame, station_id: int, start: Any, days: float) -> tuple[pd.DataFrame, list[FaultLabel]]:
    s = utc(start)
    e = s + pd.Timedelta(hours=round(days * 24) - 1)
    out = df.copy()
    m = _sel(out, station_id, s, e)
    out.loc[m, "temp_f"] = np.nan
    for c in ("rh_pct", "dewpoint_f", "solar_wm2", "wind_mph"):
        if c in out:
            out.loc[m, c] = np.nan
    _mark(out, m)
    return out, [FaultLabel(station_id, "outage", s, e, {"days": days})]


def stuck(df: pd.DataFrame, station_id: int, start: Any, hours: int) -> tuple[pd.DataFrame, list[FaultLabel]]:
    s = utc(start)
    e = s + pd.Timedelta(hours=hours - 1)
    out = df.copy()
    m = _sel(out, station_id, s, e)
    prev = out[(out["station_id"] == station_id) & (out["ts"] <= s)]["temp_f"].dropna()
    val = float(prev.iloc[-1]) if len(prev) else 50.0
    out.loc[m, "temp_f"] = val
    _mark(out, m)
    return out, [FaultLabel(station_id, "stuck", s, e, {"hours": hours, "value_f": val})]


def spike(df: pd.DataFrame, station_id: int, start: Any, hours: int, delta_f: float
          ) -> tuple[pd.DataFrame, list[FaultLabel]]:
    s = utc(start)
    e = s + pd.Timedelta(hours=hours - 1)
    out = df.copy()
    m = _sel(out, station_id, s, e)
    out.loc[m, "temp_f"] = out.loc[m, "temp_f"] + delta_f
    _mark(out, m)
    return out, [FaultLabel(station_id, "spike", s, e, {"hours": hours, "delta_f": delta_f})]


def drift(df: pd.DataFrame, station_id: int, start: Any, max_delta_f: float, days: float,
          hold_days: float = 0.0) -> tuple[pd.DataFrame, list[FaultLabel]]:
    """Linear ramp 0 -> max over ``days``, then (optionally) held at max for ``hold_days``."""
    s = utc(start)
    ramp_end = s + pd.Timedelta(hours=round(days * 24) - 1)
    e = ramp_end + pd.Timedelta(hours=round(hold_days * 24))
    out = df.copy()
    m = _sel(out, station_id, s, e)
    frac = ((out.loc[m, "ts"] - s) / pd.Timedelta(hours=round(days * 24) - 1)).clip(0, 1)
    out.loc[m, "temp_f"] = out.loc[m, "temp_f"] + max_delta_f * frac
    _mark(out, m)
    return out, [FaultLabel(station_id, "drift", s, e, {"max_delta_f": max_delta_f, "days": days,
                                                         "hold_days": hold_days})]


def offset(df: pd.DataFrame, station_id: int, start: Any, delta_f: float, end: Any | None = None
           ) -> tuple[pd.DataFrame, list[FaultLabel]]:
    """Persistent step change from ``start`` (to ``end`` or the end of the data)."""
    s = utc(start)
    st = df[df["station_id"] == station_id]["ts"]
    e = utc(end) if end is not None else (st.max() if len(st) else s)
    out = df.copy()
    m = _sel(out, station_id, s, e)
    out.loc[m, "temp_f"] = out.loc[m, "temp_f"] + delta_f
    _mark(out, m)
    return out, [FaultLabel(station_id, "offset", s, e, {"delta_f": delta_f})]


def clock_shift(df: pd.DataFrame, station_id: int, start: Any, hours: int, duration_hours: int | None = None
                ) -> tuple[pd.DataFrame, list[FaultLabel]]:
    """Values are reported ``hours`` late (positive) or early (negative) from ``start``."""
    s = utc(start)
    st = df[df["station_id"] == station_id]["ts"]
    e = s + pd.Timedelta(hours=duration_hours - 1) if duration_hours else (st.max() if len(st) else s)
    out = df.copy()
    sm = out["station_id"] == station_id
    series = out.loc[sm].set_index("ts")["temp_f"]
    shifted = series.copy()
    shifted.index = shifted.index + pd.Timedelta(hours=hours)
    m = _sel(out, station_id, s, e)
    out.loc[m, "temp_f"] = out.loc[m, "ts"].map(shifted).to_numpy(dtype="float64")
    _mark(out, m)
    return out, [FaultLabel(station_id, "clock_shift", s, e, {"hours": hours})]


INJECTORS: dict[str, Callable[..., tuple[pd.DataFrame, list[FaultLabel]]]] = {"outage": outage, "stuck": stuck, "spike": spike, "drift": drift, "offset": offset,
             "clock_shift": clock_shift}


def inject(df: pd.DataFrame, faults: list[dict[str, Any]]) -> tuple[pd.DataFrame, list[FaultLabel]]:
    """Apply a list of ``{type, station, start, **params}`` faults in order."""
    out, labels = df, []
    for f in faults:
        f = dict(f)
        kind = f.pop("type")
        sid = int(f.pop("station"))
        start = f.pop("start")
        out, lab = INJECTORS[kind](out, sid, start, **f)
        labels.extend(lab)
    return out, labels


def random_faults(
    stations: list[int], start: Any, end: Any, n: int, seed: int = 42,
    types: tuple[str, ...] = ("outage", "stuck", "spike", "drift", "offset", "clock_shift"),
) -> list[dict[str, Any]]:
    """Seeded random fault specs with the benchmark's parameter ranges.

    outage 1-14 d, stuck 6-72 h, spike 1-3 h of +-15-30 F, drift to 3-6 F over 3-14 d,
    offset +-2-6 F (lasting 7-21 d), clock shift +-1-2 h (lasting 3-14 d).
    Faults on the same station never overlap.
    """
    rng = np.random.default_rng(seed)
    s0, e0 = utc(start), utc(end)
    span_h = int((e0 - s0) / pd.Timedelta(hours=1))
    busy: dict[int, list[tuple[pd.Timestamp, pd.Timestamp]]] = {}
    out: list[dict[str, Any]] = []
    tries = 0
    while len(out) < n and tries < n * 50:
        tries += 1
        kind = types[len(out) % len(types)]
        sid = int(rng.choice(stations))
        p: dict[str, Any]
        if kind == "outage":
            p = {"days": int(rng.integers(1, 15))}
            dur = p["days"] * 24
        elif kind == "stuck":
            p = {"hours": int(rng.integers(6, 73))}
            dur = p["hours"]
        elif kind == "spike":
            p = {"hours": int(rng.integers(1, 4)), "delta_f": float(rng.choice([-1, 1]) * rng.uniform(15, 30))}
            dur = p["hours"]
        elif kind == "drift":
            p = {"max_delta_f": float(rng.choice([-1, 1]) * rng.uniform(3, 6)), "days": int(rng.integers(3, 15)),
                 "hold_days": 3}
            dur = (p["days"] + 3) * 24
        elif kind == "offset":
            d = int(rng.integers(7, 22))
            p = {"delta_f": float(rng.choice([-1, 1]) * rng.uniform(2, 6))}
            dur = d * 24
        else:
            p = {"hours": int(rng.choice([-2, -1, 1, 2])), "duration_hours": int(rng.integers(3, 15)) * 24}
            dur = p["duration_hours"]
        margin = 24 * 3
        if span_h - dur - 2 * margin <= 0:
            continue
        st = s0 + pd.Timedelta(hours=int(rng.integers(margin, span_h - dur - margin)))
        en = st + pd.Timedelta(hours=dur)
        pad = pd.Timedelta(days=3)
        if any(not (en + pad < a or st - pad > b) for a, b in busy.get(sid, [])):
            continue
        if kind == "offset":
            p["end"] = en.isoformat()
        busy.setdefault(sid, []).append((st, en))
        out.append({"type": kind, "station": sid, "start": st.isoformat(), **p})
    return out


# =========================================================================== scenario
def pick_hill_station(stations: pd.DataFrame, near: int = 71, max_km: float = 80.0) -> int:
    """Highest-elevation active station within ``max_km`` of ``near`` (default Modesto)."""
    from ..qc.neighbors import haversine_km

    st = stations.dropna(subset=["lat", "lon"])
    c = st[st["station_id"] == near]
    if c.empty:
        return int(st.sort_values("elevation_ft").iloc[-1]["station_id"])
    d = haversine_km(c["lat"].iloc[0], c["lon"].iloc[0], st["lat"], st["lon"])
    cand = st[(d <= max_km) & (st["station_id"] != near)]
    if "is_active" in cand:
        cand = cand[cand["is_active"].fillna(True).astype(bool)]
    return int(cand.sort_values(["elevation_ft", "station_id"]).iloc[-1]["station_id"])


def scenario_faults(spec: dict[str, Any], stations: pd.DataFrame, season: str) -> list[dict[str, Any]]:
    from .. import config

    reg = config.region()
    s0, _ = season_bounds(season, reg["seasons"]["start_mmdd"], reg["seasons"]["end_mmdd"])
    day0 = s0 - pd.Timedelta(hours=1)  # 00:00 PST on the season's first day
    out = []
    for f in spec["faults"]:
        f = dict(f)
        day = int(f.pop("day"))
        station = f.pop("station")
        if station == "HILL_STATION":
            station = pick_hill_station(stations)
        kind = f.pop("type")
        out.append({"type": kind, "station": int(station), "start": (day0 + pd.Timedelta(days=day, hours=1)).isoformat(),
                    **f})
    return out


def apply_scenario(yaml_path: str | Path, readings: pd.DataFrame | None = None,
                   stations: pd.DataFrame | None = None, ctx: Any | None = None
                   ) -> tuple[pd.DataFrame, list[FaultLabel]]:
    """Inject the demo scenario's faults into a copy of the season's readings."""
    from .. import config
    from ..context import get_context

    spec = yaml.safe_load(Path(yaml_path).read_text())
    season = spec.get("season") or config.region()["demo_season"]
    if readings is None or stations is None:
        ctx = ctx or get_context()
        reg = config.region()
        s, e = season_bounds(season, reg["seasons"]["start_mmdd"], reg["seasons"]["end_mmdd"])
        stations = ctx.region_stations()
        readings = ctx.store.readings(s, e, stations["station_id"].tolist())
    faults = scenario_faults(spec, stations, season)
    for f in faults:
        if f["type"] == "offset":
            print(f"HILL_STATION offset -> station {f['station']}")
    return inject(readings, faults)
