"""Synthetic Central-Valley-like station network, for tests and offline dry runs.

NOT real data. Anything computed on it is labeled ``synthetic`` and must never be shown as
a ChillWatch result. It exists so the pipeline can be exercised without CIMIS access.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..normalize import to_pst
from ..store import MemoryStore

SEED_IDS = [6, 70, 71, 39, 5, 12]


def make_stations(n: int = 30, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ids = list(SEED_IDS)
    pool = [i for i in range(1, 260) if i not in ids]
    ids += sorted(rng.choice(pool, size=max(0, n - len(ids)), replace=False).tolist())
    # valley axis from Shafter (35.5, -119.3) to Durham (39.6, -121.8)
    t = rng.uniform(0, 1, size=n)
    lat = 35.5 + 4.1 * t + rng.normal(0, 0.12, n)
    lon = -119.3 - 2.5 * t + rng.normal(0, 0.25, n)
    elev = np.where(rng.random(n) < 0.75, rng.uniform(20, 400, n), rng.uniform(600, 2800, n))
    fixed = {6: (38.536, -121.776, 60), 70: (37.834, -121.223, 35), 71: (37.645, -121.188, 85),
             39: (36.598, -119.504, 337), 5: (35.533, -119.282, 360), 12: (39.609, -121.825, 130)}
    for i, sid in enumerate(ids):
        if sid in fixed:
            lat[i], lon[i], elev[i] = fixed[sid]
    # make sure there is at least one foothill station near Modesto (the "hill station")
    if n > 7:
        lat[7], lon[7], elev[7] = 37.75, -120.85, 1450.0
    df = pd.DataFrame({
        "station_id": ids,
        "source": "cimis",
        "source_station_id": [str(i) for i in ids],
        "name": [f"SYN-{i}" for i in ids],
        "county": "synthetic",
        "lat": lat,
        "lon": lon,
        "elevation_ft": np.round(elev),
        "is_active": True,
        "connect_date": pd.Timestamp("1985-01-01"),
        "disconnect_date": pd.NaT,
        "in_region": True,
        "tz": "America/Los_Angeles",
    })
    df.loc[df["station_id"] == 12, ["disconnect_date", "is_active"]] = [pd.Timestamp("2025-07-15"), False]
    return df


def make_readings(stations: pd.DataFrame, start: Any, end: Any, seed: int = 0,
                  missing_rate: float = 0.002) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    hours = pd.date_range(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"), freq="h")
    H, S = len(hours), len(stations)
    local = to_pst(hours)
    hod = local.hour.to_numpy() + 0.0
    doy = local.day_of_year.to_numpy()
    day = (local - pd.Timedelta(hours=1)).normalize()
    days, day_idx = np.unique(day.to_numpy(), return_inverse=True)
    D = len(days)
    # day-scale weather: synoptic anomaly, clearness, fog
    anom = np.zeros(D)
    clear = np.zeros(D)
    a, c = 0.0, 0.5
    for i in range(D):
        a = 0.8 * a + rng.normal(0, 2.5)
        c = np.clip(0.6 * c + 0.4 * rng.random(), 0, 1)
        anom[i], clear[i] = a, c
    fog = (rng.random(D) < 0.12) & (clear > 0.4)
    seasonal = 55.0 + 14.0 * np.cos(2 * np.pi * (doy - 200) / 365.25)
    A = anom[day_idx]
    C = clear[day_idx]
    F = fog[day_idx]
    diurnal = np.cos(2 * np.pi * (hod - 15) / 24.0)
    night_w = (1 - np.cos(2 * np.pi * (hod - 15) / 24.0)) / 2  # 1 at 03:00, 0 at 15:00
    gamma = (-3.5 * (1 - night_w) + (1.0 + 5.0 * C) * night_w)  # F per 1000 ft
    lat = stations["lat"].to_numpy()
    elev = stations["elevation_ft"].to_numpy()
    # station microclimate is a property of the station, not of the year
    srng = np.random.default_rng(1000 + int(stations["station_id"].sum()))
    bias = srng.normal(0, 0.8, S)
    amp_scale = srng.uniform(0.85, 1.15, S)
    amp = (7.0 + 8.0 * C)[:, None] * amp_scale[None, :]
    low = elev < 500
    amp = np.where(F[:, None] & low[None, :], 1.5, amp)
    fog_cool = np.where(F[:, None] & low[None, :], -3.0, 0.0)
    T = (seasonal[:, None] + A[:, None] - 1.5 * (lat[None, :] - 37.0) + amp * diurnal[:, None]
         + gamma[:, None] * elev[None, :] / 1000.0 + bias[None, :] + fog_cool)
    noise = np.zeros((H, S))
    e = np.zeros(S)
    for t in range(H):
        e = 0.7 * e + rng.normal(0, 0.45, S)
        noise[t] = e
    T = np.round(T + noise, 1)
    miss = rng.random((H, S)) < missing_rate
    T[miss] = np.nan
    ids = stations["station_id"].to_numpy()
    df = pd.DataFrame({
        "station_id": np.repeat(ids[None, :], H, axis=0).ravel(),
        "ts": np.repeat(hours.to_numpy()[:, None], S, axis=1).ravel(),
        "temp_f": T.ravel(),
    })
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    rh = np.clip(55 + 30 * F[:, None] - 20 * diurnal[:, None] + rng.normal(0, 5, (H, S)), 5, 100)
    df["rh_pct"] = np.round(rh.ravel())
    df["dewpoint_f"] = np.round((T - (100 - rh) / 5 * 9 / 5).ravel(), 1)
    df["solar_wm2"] = np.round(np.clip(600 * np.cos(2 * np.pi * (hod - 12) / 24), 0, None)[:, None]
                               * (0.3 + 0.7 * C)[:, None] * np.ones((1, S)), 1).ravel()
    df["wind_mph"] = np.round(np.abs(rng.normal(4 - 3 * C, 1.5)[:, None] + rng.normal(0, 1, (H, S))), 1).ravel()
    df["qc_source"] = None
    df["qc_base"] = np.where(df["temp_f"].isna(), "missing", "ok")
    df["qc_status"] = df["qc_base"]
    df["ingested_at"] = pd.Timestamp("2026-01-01", tz="UTC")
    # stations only report while connected
    disc = stations.set_index("station_id")["disconnect_date"]
    for sid, d in disc.dropna().items():
        cut = pd.Timestamp(d).tz_localize("UTC") + pd.Timedelta(days=1, hours=8)
        df = df[~((df["station_id"] == sid) & (df["ts"] > cut))]
    return df.reset_index(drop=True)


def make_store(n_stations: int = 30, start: Any = "2025-10-01 09:00", end: Any = "2025-12-31 08:00",
               seed: int = 0) -> MemoryStore:
    st = make_stations(n_stations, seed)
    return MemoryStore(stations=st, readings=make_readings(st, start, end, seed))
