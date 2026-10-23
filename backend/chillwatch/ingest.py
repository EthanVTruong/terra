"""Ingestion: station sync, resumable backfill and the daily re-fetch.

Every station is processed independently: one failing station (bad response, parse error)
is logged and skipped, it never stops the others.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import config
from .normalize import complete_hours, finalize_hourly, pst_day_hours
from .qc.neighbors import haversine_km
from .sources.base import StationSource
from .store import Store, assign_station_id
from .timeutil import season_bounds

log = logging.getLogger(__name__)


def select_region(stations: pd.DataFrame, region: dict[str, Any]) -> pd.Series:
    """True for stations within ``radius_km`` of a seed (active or closed since the cutoff), plus includes."""
    st = stations
    sid = st["station_id"].astype(int)
    seeds = st[sid.isin(region.get("seed_stations", []))]
    points = [(float(s["lat"]), float(s["lon"])) for _, s in seeds.iterrows()]
    points += [(float(c["lat"]), float(c["lon"])) for c in region.get("seed_coords", [])]
    near = np.zeros(len(st), dtype=bool)
    for lat, lon in points:
        near |= haversine_km(lat, lon, st["lat"].to_numpy(), st["lon"].to_numpy()) <= float(region["radius_km"])
    cutoff = pd.Timestamp(region.get("recently_closed_since", "1900-01-01"))
    disc = pd.to_datetime(st["disconnect_date"])
    alive = st["is_active"].fillna(False).astype(bool).to_numpy() | (disc.isna() | (disc >= cutoff)).to_numpy()
    return pd.Series((near & alive) | sid.isin(region.get("include_stations", [])).to_numpy(), index=st.index)


def sync_stations(source: StationSource, store: Store, region_path: str | Path | None = None) -> pd.DataFrame:
    region = config.region(region_path)
    src = source.list_stations()
    existing = {(r.source, str(r.source_station_id)): int(r.station_id) for r in store.stations().itertuples()}
    ids = []
    for r in src.itertuples():
        sid = assign_station_id(r.source, r.source_station_id, existing)
        existing[(r.source, str(r.source_station_id))] = sid
        ids.append(sid)
    st = src.assign(station_id=ids)
    # a closed station's "current" disconnect date in the far future means still open
    far = pd.to_datetime(st["disconnect_date"]) > pd.Timestamp.now() + pd.Timedelta(days=365)
    st.loc[far, "disconnect_date"] = pd.NaT
    st["in_region"] = select_region(st, region).to_numpy()
    st["tz"] = "America/Los_Angeles"
    store.upsert_stations(st)
    return st[st["in_region"]].reset_index(drop=True)


def season_ranges(start: date, end: date, region: dict[str, Any]) -> list[tuple[date, date]]:
    """Intersect [start, end] with the Oct 1 - Mar 31 chill seasons."""
    rs = region["seasons"]
    out = []
    for y in range(start.year - 1, end.year + 1):
        s = f"{y}-{str(y + 1)[2:]}"
        a, b = season_bounds(s, rs["start_mmdd"], rs["end_mmdd"])
        a_d = (a - pd.Timedelta(hours=9)).date()
        b_d = (b - pd.Timedelta(hours=9)).date()
        lo, hi = max(a_d, start), min(b_d, end)
        if lo <= hi:
            out.append((lo, hi))
    return out


def _clip_to_station(st: pd.Series, lo: date, hi: date) -> tuple[date, date] | None:
    c = pd.to_datetime(st.get("connect_date"))
    d = pd.to_datetime(st.get("disconnect_date"))
    if pd.notna(c):
        lo = max(lo, c.date())
    if pd.notna(d):
        hi = min(hi, d.date())
    return (lo, hi) if lo <= hi else None


def ingest_station(source: Any, store: Store, st: pd.Series, lo: date, hi: date, refresh: bool = False) -> int:
    rng = _clip_to_station(st, lo, hi)
    if rng is None:
        return 0
    lo, hi = rng
    raw = source.fetch_hourly(str(st["source_station_id"]), lo, hi, refresh=refresh) if refresh else \
        source.fetch_hourly(str(st["source_station_id"]), lo, hi)
    key = (str(st["source"]), str(st["source_station_id"]))
    df = finalize_hourly(raw, {key: int(st["station_id"])})  # also fine for an empty window
    hours = pst_day_hours(lo, hi)
    if len(df):
        df = df[(df["ts"] >= hours[0]) & (df["ts"] <= hours[-1])]
        df = df.sort_values("ingested_at").drop_duplicates(["station_id", "ts"], keep="last")
    base = df.drop(columns=["source", "source_station_id"], errors="ignore")
    full = complete_hours(base, hours, keys=["station_id"], station_ids=[(int(st["station_id"]),)])
    full["ingested_at"] = full["ingested_at"].fillna(pd.Timestamp.now(tz="UTC"))
    return store.upsert_readings(full)


def run_per_station(stations: pd.DataFrame, fn: Any) -> dict[int, Any]:
    """Call ``fn(station_row)`` for every station; failures are logged, never raised."""
    out: dict[int, Any] = {}
    for _, st in stations.iterrows():
        sid = int(st["station_id"])
        try:
            out[sid] = fn(st)
        except Exception as e:  # isolate: one bad station must not stop the rest
            log.error("station %s failed: %s", sid, e)
            out[sid] = e
    return out


def backfill(source: Any, store: Store, start: date, end: date, station_ids: list[int] | None = None,
             region_path: str | Path | None = None) -> dict[int, Any]:
    region = config.region(region_path)
    st = store.stations()
    st = st[st["in_region"].fillna(True).astype(bool)] if "in_region" in st else st
    if station_ids:
        st = st[st["station_id"].isin(station_ids)]
    ranges = season_ranges(start, end, region)

    def one(row: pd.Series) -> int:
        n = 0
        for lo, hi in ranges:
            n += ingest_station(source, store, row, lo, hi)
        return n

    return run_per_station(st, one)


def ingest_recent(source: Any, store: Store, days: int = 3, today: date | None = None,
                  station_ids: list[int] | None = None) -> dict[int, Any]:
    """Re-fetch the last ``days`` PST days (late deliveries are upserted idempotently)."""
    today = today or (datetime.utcnow() - timedelta(hours=8)).date()
    lo, hi = today - timedelta(days=days), today - timedelta(days=1)
    st = store.stations()
    st = st[st["in_region"].fillna(True).astype(bool)] if "in_region" in st else st
    active = st["is_active"].fillna(True).astype(bool) | pd.to_datetime(st["disconnect_date"]).ge(pd.Timestamp(lo))
    st = st[active]
    if station_ids:
        st = st[st["station_id"].isin(station_ids)]
    return run_per_station(st, lambda row: ingest_station(source, store, row, lo, hi, refresh=True))


def export_parquet(store: Store, region_name: str | None = None, out_dir: Path | None = None) -> tuple[Path, Path]:
    region_name = region_name or config.region().get("name", "region")  # per CHILLWATCH_REGION
    out_dir = out_dir or config.data_dir() / "parquet"
    out_dir.mkdir(parents=True, exist_ok=True)
    rp, sp = out_dir / f"readings_{region_name}.parquet", out_dir / f"stations_{region_name}.parquet"
    store.readings().to_parquet(rp, index=False)
    store.stations().to_parquet(sp, index=False)
    return rp, sp
