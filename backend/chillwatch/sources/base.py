"""Adapter protocol and canonical schemas shared by every station source."""

from __future__ import annotations

from datetime import date
from typing import Protocol

import pandas as pd

STATION_COLUMNS = [
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
]

HOURLY_COLUMNS = ["source", "source_station_id", "ts", "temp_f", "qc_source", "raw_ref"]
HOURLY_EXTRA_COLUMNS = ["rh_pct", "dewpoint_f", "solar_wm2", "wind_mph"]

DAILY_COLUMNS = ["source", "source_station_id", "date_local", "tmax_f", "tmin_f", "qc_source"]


class StationSource(Protocol):
    source: str  # "cimis" | "diy" | "prism"

    def list_stations(self) -> pd.DataFrame: ...

    def fetch_hourly(self, station_id: str, start: date, end: date) -> pd.DataFrame: ...

    def fetch_daily(self, station_id: str, start: date, end: date) -> pd.DataFrame: ...


def empty_hourly(extra: bool = True) -> pd.DataFrame:
    cols = HOURLY_COLUMNS + (HOURLY_EXTRA_COLUMNS if extra else [])
    df = pd.DataFrame({c: pd.Series(dtype="float64") for c in cols})
    df["source"] = df["source"].astype(object)
    df["source_station_id"] = df["source_station_id"].astype(object)
    df["qc_source"] = df["qc_source"].astype(object)
    df["raw_ref"] = df["raw_ref"].astype(object)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df


def check_schema(df: pd.DataFrame, columns: list[str], optional: list[str] | None = None) -> pd.DataFrame:
    """Return df with exactly the canonical columns (plus present optional ones), in order."""
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValueError(f"missing canonical columns: {missing}")
    keep = list(columns) + [c for c in (optional or []) if c in df.columns]
    return df[keep]
