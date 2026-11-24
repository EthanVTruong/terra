"""Reading/station storage: an in-memory store persisted as Parquet.

The daily job, the bench replay, the logger pipeline and the tests share this small API.
(The PostgreSQL store was removed in the ENG 3 chill-monitor refactor; Parquet snapshots
cover a single orchard's logger plus its reference stations.)
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pandas as pd

from .timeutil import utc

STATION_TABLE_COLUMNS = [
    "station_id",
    "source",
    "source_station_id",
    "name",
    "county",
    "lat",
    "lon",
    "elevation_ft",
    "is_active",
    "connect_date",
    "disconnect_date",
    "in_region",
    "tz",
]

OBS_COLUMNS = [
    "temp_f",
    "qc_source",
    "qc_base",
    "qc_status",
    "rh_pct",
    "dewpoint_f",
    "solar_wm2",
    "wind_mph",
    "raw_ref",
    "ingested_at",
]
EST_COLUMNS = ["est_temp_f", "est_sd", "used_temp_f", "is_estimated", "est_method"]
READING_COLUMNS = ["station_id", "ts", *OBS_COLUMNS, *EST_COLUMNS]

_DTYPES: dict[str, Any] = {
    "temp_f": "float64",
    "rh_pct": "float64",
    "dewpoint_f": "float64",
    "solar_wm2": "float64",
    "wind_mph": "float64",
    "est_temp_f": "float64",
    "est_sd": "float64",
    "used_temp_f": "float64",
    "is_estimated": "bool",
    "qc_source": object,
    "qc_base": object,
    "qc_status": object,
    "raw_ref": object,
    "est_method": object,
}


def assign_station_id(source: str, source_station_id: str, existing: dict[tuple[str, str], int]) -> int:
    """CIMIS stations keep their CIMIS number; other sources get ids from 100000 up."""
    key = (source, str(source_station_id))
    if key in existing:
        return existing[key]
    if source == "cimis":
        return int(source_station_id)
    used = [v for v in existing.values() if v >= 100000]
    return max(used, default=99999) + 1


def coerce_readings(df: pd.DataFrame) -> pd.DataFrame:
    """Return a frame with every reading column present and typed."""
    out = df.copy()
    for c in READING_COLUMNS:
        if c not in out.columns:
            if c == "is_estimated":
                out[c] = False
            elif c in ("qc_base", "qc_status"):
                out[c] = np.where(out["temp_f"].isna(), "missing", "ok") if "temp_f" in out else "missing"
            elif c == "ingested_at":
                out[c] = pd.Timestamp.now(tz="UTC")
            else:
                out[c] = np.nan if _DTYPES.get(c) == "float64" else None
    out["station_id"] = out["station_id"].astype("int64")
    out["ts"] = pd.to_datetime(out["ts"], utc=True)
    out["ingested_at"] = pd.to_datetime(out["ingested_at"], utc=True)
    for c, t in _DTYPES.items():
        if t == "bool":
            out[c] = out[c].fillna(False).astype(bool)
        elif t == "float64":
            out[c] = pd.to_numeric(out[c], errors="coerce").astype("float64")
        else:
            out[c] = out[c].astype(object).where(out[c].notna(), None)
    return out[READING_COLUMNS]


def _reset_estimates(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    ok = df["qc_status"].eq("ok") | df["qc_status"].eq("suspect")
    df["used_temp_f"] = df["temp_f"].where(ok)
    df["est_temp_f"] = np.nan
    df["est_sd"] = np.nan
    df["is_estimated"] = False
    df["est_method"] = None
    return df


class Store(Protocol):
    def stations(self) -> pd.DataFrame: ...

    def upsert_stations(self, df: pd.DataFrame) -> None: ...

    def readings(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        station_ids: Iterable[int] | None = None,
    ) -> pd.DataFrame: ...

    def upsert_readings(self, df: pd.DataFrame) -> int: ...

    def update_readings(self, df: pd.DataFrame, columns: list[str]) -> int: ...

    def last_observed(self, before: datetime) -> pd.Series: ...

    def offsets(self) -> pd.DataFrame: ...

    def add_offset(self, row: dict[str, Any]) -> None: ...


def _coerce_offsets(df: pd.DataFrame) -> pd.DataFrame:
    """Offset rows with fixed dtypes so they survive a Parquet round trip."""
    df = df.copy()
    df["station_id"] = df["station_id"].astype("int64")
    df["kind"] = df["kind"].astype(str)
    for c in ("night_f", "day_f"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    df["hourly_f"] = df["hourly_f"].map(lambda v: v if isinstance(v, str) else None).astype(object)
    for c in ("window_start", "window_end", "created_at"):
        df[c] = pd.to_datetime(df[c], utc=True)
    return df.reset_index(drop=True)


# ============================================================================ memory
class MemoryStore:
    """Per-station DataFrames indexed by ts. The CLI loads it from and saves it to the Parquet
    snapshot in ``data/parquet`` (see ``ingest.export_parquet``); tests and replays use it directly."""

    def __init__(self, stations: pd.DataFrame | None = None, readings: pd.DataFrame | None = None) -> None:
        self._stations = pd.DataFrame(columns=STATION_TABLE_COLUMNS)
        self._data: dict[int, pd.DataFrame] = {}
        self._offsets = pd.DataFrame(
            columns=["station_id", "kind", "night_f", "day_f", "hourly_f", "window_start", "window_end", "created_at"]
        )
        if stations is not None:
            self.upsert_stations(stations)
        if readings is not None and len(readings):
            self._load(coerce_readings(readings))

    def _load(self, df: pd.DataFrame) -> None:
        for sid, g in df.groupby("station_id", sort=False):
            self._data[int(sid)] = g.drop(columns="station_id").set_index("ts").sort_index()

    # stations
    def stations(self) -> pd.DataFrame:
        return self._stations.copy()

    def upsert_stations(self, df: pd.DataFrame) -> None:
        df = df.copy()
        for c in STATION_TABLE_COLUMNS:
            if c not in df.columns:
                df[c] = True if c == "in_region" else None
        merged = pd.concat([self._stations, df[STATION_TABLE_COLUMNS]], ignore_index=True)
        merged = merged.drop_duplicates("station_id", keep="last").sort_values("station_id")
        merged["station_id"] = merged["station_id"].astype("int64")
        for c in ("connect_date", "disconnect_date"):
            merged[c] = pd.to_datetime(merged[c])
        self._stations = merged.reset_index(drop=True)

    # readings
    def readings(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        station_ids: Iterable[int] | None = None,
    ) -> pd.DataFrame:
        ids = list(self._data) if station_ids is None else [int(s) for s in station_ids]
        frames = []
        s = utc(start) if start is not None else None
        e = utc(end) if end is not None else None
        for sid in ids:
            f = self._data.get(sid)
            if f is None or f.empty:
                continue
            f = f.loc[s:e] if (s is not None or e is not None) else f
            if f.empty:
                continue
            f = f.reset_index()
            f.insert(0, "station_id", sid)
            frames.append(f)
        if not frames:
            return coerce_readings(pd.DataFrame({"station_id": [], "ts": [], "temp_f": []}))
        return pd.concat(frames, ignore_index=True)[READING_COLUMNS]

    def upsert_readings(self, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        df = _reset_estimates(coerce_readings(df))
        df = df.sort_values("ingested_at", kind="stable").drop_duplicates(["station_id", "ts"], keep="last")
        for sid, g in df.groupby("station_id", sort=False):
            new = g.drop(columns="station_id").set_index("ts")
            cur = self._data.get(int(sid))
            if cur is None:
                self._data[int(sid)] = new.sort_index()
            else:
                cur = cur[~cur.index.isin(new.index)]
                self._data[int(sid)] = pd.concat([cur, new]).sort_index()
        return len(df)

    def update_readings(self, df: pd.DataFrame, columns: list[str]) -> int:
        n = 0
        for sid, g in df.groupby("station_id", sort=False):
            cur = self._data.get(int(sid))
            if cur is None:
                continue
            g = g.set_index("ts")
            idx = g.index.intersection(cur.index)
            if len(idx) == 0:
                continue
            for c in columns:
                vals = g.loc[idx, c]
                if cur[c].dtype == bool:
                    vals = vals.astype(bool)
                elif cur[c].dtype == object:
                    vals = vals.astype(object)
                cur.loc[idx, c] = vals
            n += len(idx)
        return n

    def last_observed(self, before: datetime) -> pd.Series:
        b = utc(before)
        out = {}
        for sid, f in self._data.items():
            obs = f.loc[:b, "temp_f"].dropna()
            out[sid] = obs.index[-1] if len(obs) else pd.NaT
        return pd.Series(out, dtype="datetime64[ns, UTC]")

    def offsets(self) -> pd.DataFrame:
        return self._offsets.copy()

    def add_offset(self, row: dict[str, Any]) -> None:
        self._offsets = pd.concat([self._offsets, pd.DataFrame([row])], ignore_index=True)

    # parquet
    def to_parquet(self, readings_path: str | Path, stations_path: str | Path | None = None) -> None:
        readings_path = Path(readings_path)
        readings_path.parent.mkdir(parents=True, exist_ok=True)
        self.readings().to_parquet(readings_path, index=False)
        if stations_path:
            self.stations().to_parquet(stations_path, index=False)

    @classmethod
    def from_parquet(cls, readings_path: str | Path, stations_path: str | Path,
                     offsets_path: str | Path | None = None) -> MemoryStore:
        store = cls(stations=pd.read_parquet(stations_path), readings=pd.read_parquet(readings_path))
        if offsets_path is not None and Path(offsets_path).exists():
            store._offsets = _coerce_offsets(pd.read_parquet(offsets_path))
        return store

    def offsets_to_parquet(self, path: str | Path) -> None:
        _coerce_offsets(self._offsets).to_parquet(path, index=False)

    def copy(self) -> MemoryStore:
        new = MemoryStore()
        new._stations = self._stations.copy()
        new._data = {k: v.copy() for k, v in self._data.items()}
        new._offsets = self._offsets.copy()
        return new
