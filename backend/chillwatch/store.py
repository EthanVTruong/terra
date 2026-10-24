"""Reading/station storage: an in-memory (Parquet-backed) store and a PostgreSQL store.

Both expose the same small API so the daily job, the bench replay and tests share code.
"""

from __future__ import annotations

import io
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


# ============================================================================ memory
class MemoryStore:
    """Per-station DataFrames indexed by ts. Used by tests, the demo replay and the bench."""

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
    def from_parquet(cls, readings_path: str | Path, stations_path: str | Path) -> MemoryStore:
        return cls(stations=pd.read_parquet(stations_path), readings=pd.read_parquet(readings_path))

    def copy(self) -> MemoryStore:
        new = MemoryStore()
        new._stations = self._stations.copy()
        new._data = {k: v.copy() for k, v in self._data.items()}
        new._offsets = self._offsets.copy()
        return new


# ============================================================================ postgres
class SqlStore:
    """PostgreSQL store (tables from the Alembic migration)."""

    def __init__(self, url: str) -> None:
        from sqlalchemy import create_engine

        self.engine = create_engine(url, future=True)

    def stations(self) -> pd.DataFrame:
        df = pd.read_sql("select * from stations order by station_id", self.engine)
        for c in ("connect_date", "disconnect_date"):
            df[c] = pd.to_datetime(df[c])
        return df[STATION_TABLE_COLUMNS]

    def upsert_stations(self, df: pd.DataFrame) -> None:
        from sqlalchemy import text

        df = df.copy()
        for c in STATION_TABLE_COLUMNS:
            if c not in df.columns:
                df[c] = True if c == "in_region" else None
        cols = STATION_TABLE_COLUMNS
        sets = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "station_id")
        sql = text(
            f"insert into stations ({', '.join(cols)}) values ({', '.join(':' + c for c in cols)}) "
            f"on conflict (station_id) do update set {sets}"
        )
        recs = []
        for r in df[cols].to_dict("records"):
            for c in ("connect_date", "disconnect_date"):
                r[c] = None if pd.isna(r[c]) else pd.Timestamp(r[c]).date()
            for k, v in list(r.items()):
                if isinstance(v, float) and np.isnan(v):
                    r[k] = None
                elif isinstance(v, np.generic):
                    r[k] = v.item()
            recs.append(r)
        with self.engine.begin() as conn:
            conn.execute(sql, recs)

    def readings(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        station_ids: Iterable[int] | None = None,
    ) -> pd.DataFrame:
        from sqlalchemy import text

        where: list[str] = []
        params: dict[str, Any] = {}
        if start is not None:
            where.append("ts >= :start")
            params["start"] = utc(start).to_pydatetime()
        if end is not None:
            where.append("ts <= :end")
            params["end"] = utc(end).to_pydatetime()
        if station_ids is not None:
            where.append("station_id = any(:ids)")
            params["ids"] = [int(s) for s in station_ids]
        sql = f"select {', '.join(READING_COLUMNS)} from readings"
        if where:
            sql += " where " + " and ".join(where)
        sql += " order by station_id, ts"
        with self.engine.connect() as conn:
            df = pd.read_sql(text(sql), conn, params=params)
        return coerce_readings(df)

    def _copy_into_temp(self, conn: Any, df: pd.DataFrame, cols: list[str], name: str) -> None:
        raw = conn.connection.driver_connection
        with raw.cursor() as cur:
            cur.execute(f"create temp table {name} on commit drop as select {', '.join(cols)} from readings with no data")
            buf = io.StringIO()
            out = df[cols].copy()
            for c in cols:
                if out[c].dtype == bool:
                    out[c] = out[c].map({True: "t", False: "f"})
            out.to_csv(buf, index=False, header=False, na_rep="\\N")
            buf.seek(0)
            with cur.copy(f"copy {name} ({', '.join(cols)}) from stdin with (format csv, null '\\N')") as cp:
                cp.write(buf.read())

    def upsert_readings(self, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        df = _reset_estimates(coerce_readings(df))
        df = df.sort_values("ingested_at", kind="stable").drop_duplicates(["station_id", "ts"], keep="last")
        cols = READING_COLUMNS
        sets = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in ("station_id", "ts"))
        with self.engine.begin() as conn:
            self._copy_into_temp(conn, df, cols, "_rd_up")
            conn.exec_driver_sql(
                f"insert into readings ({', '.join(cols)}) select {', '.join(cols)} from _rd_up "
                f"on conflict (station_id, ts) do update set {sets}"
            )
        return len(df)

    def update_readings(self, df: pd.DataFrame, columns: list[str]) -> int:
        if df.empty:
            return 0
        full = coerce_readings(df.assign(**{c: df[c] for c in columns}))
        cols = ["station_id", "ts", *columns]
        sets = ", ".join(f"{c} = u.{c}" for c in columns)
        with self.engine.begin() as conn:
            self._copy_into_temp(conn, full, cols, "_rd_upd")
            conn.exec_driver_sql(
                f"update readings r set {sets} from _rd_upd u where r.station_id = u.station_id and r.ts = u.ts"
            )
        return len(df)

    def last_observed(self, before: datetime) -> pd.Series:
        from sqlalchemy import text

        sql = text(
            "select station_id, max(ts) as ts from readings where temp_f is not null and ts <= :b "
            "group by station_id"
        )
        with self.engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"b": utc(before).to_pydatetime()})
        return pd.Series(pd.to_datetime(df["ts"], utc=True).array, index=df["station_id"].astype(int).to_numpy())

    def offsets(self) -> pd.DataFrame:
        return pd.read_sql("select * from station_offsets order by id", self.engine)

    def add_offset(self, row: dict[str, Any]) -> None:
        pd.DataFrame([row]).to_sql("station_offsets", self.engine, if_exists="append", index=False)
