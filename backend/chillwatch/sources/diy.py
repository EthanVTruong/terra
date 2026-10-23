"""Grower DIY stations: CSV / JSON uploads -> canonical hourly rows.

Registry (YAML or JSON list): ``id, name, lat, lon, elevation_ft, tz`` (+ optional county).
Uploads live in ``{upload_dir}/{id}/*.csv|*.json`` with fields
``timestamp`` (ISO 8601, offset preferred), ``temp``, ``unit`` (F|C), optional ``rh``.
Timestamps without an offset use the station's IANA zone (daylight saving aware); rows that
still can't be placed in time are rejected.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .. import config
from ..normalize import local_date_of_hour, parse_local_timestamps, to_fahrenheit, to_hourly
from .base import DAILY_COLUMNS, HOURLY_COLUMNS, STATION_COLUMNS, empty_hourly

log = logging.getLogger(__name__)


def read_upload(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            data = data.get("readings", data.get("data", []))
        return pd.DataFrame(data)
    return pd.read_csv(path)


def parse_upload(df: pd.DataFrame, station_id: str, tz: str | None, raw_ref: str | None = None,
                 default_unit: str = "F") -> tuple[pd.DataFrame, int]:
    """Raw upload rows -> sub-hourly canonical rows; returns (rows, n_rejected)."""
    cols = {c.lower().strip(): c for c in df.columns}
    ts_col = cols.get("timestamp") or cols.get("time") or cols.get("ts")
    t_col = cols.get("temp") or cols.get("temperature") or cols.get("temp_f") or cols.get("temp_c")
    if ts_col is None or t_col is None:
        raise ValueError("upload needs timestamp and temp columns")
    unit = df[cols["unit"]] if "unit" in cols else ("C" if t_col.lower() == "temp_c" else default_unit)
    ts = parse_local_timestamps(df[ts_col], tz)
    out = pd.DataFrame({
        "source": "diy",
        "source_station_id": str(station_id),
        "ts": ts,
        "temp_f": to_fahrenheit(df[t_col], unit),
        "rh_pct": pd.to_numeric(df[cols["rh"]], errors="coerce") if "rh" in cols else float("nan"),
    })
    rejected = int(out["ts"].isna().sum())
    out = out.dropna(subset=["ts"])
    out["raw_ref"] = raw_ref
    return out, rejected


class DiySource:
    source = "diy"

    def __init__(self, registry: str | Path | None = None, upload_dir: str | Path | None = None) -> None:
        self.registry = Path(registry) if registry else config.data_dir() / "diy" / "registry.yaml"
        self.upload_dir = Path(upload_dir) if upload_dir else config.data_dir() / "diy" / "uploads"
        self.rejected: dict[str, int] = {}

    def _registry(self) -> list[dict[str, Any]]:
        if not self.registry.exists():
            return []
        text = self.registry.read_text()
        data = json.loads(text) if self.registry.suffix == ".json" else yaml.safe_load(text)
        return list(data or [])

    def list_stations(self) -> pd.DataFrame:
        rows = [{
            "source": "diy",
            "source_station_id": str(r["id"]),
            "name": r.get("name"),
            "county": r.get("county"),
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
            "elevation_ft": float(r.get("elevation_ft", float("nan"))),
            "is_active": bool(r.get("is_active", True)),
            "connect_date": pd.to_datetime(r.get("connect_date")),
            "disconnect_date": pd.to_datetime(r.get("disconnect_date")),
        } for r in self._registry()]
        return pd.DataFrame(rows, columns=STATION_COLUMNS)

    def _tz(self, station_id: str) -> str | None:
        for r in self._registry():
            if str(r["id"]) == str(station_id):
                return r.get("tz")
        return None

    def fetch_hourly(self, station_id: str, start: date, end: date, refresh: bool = False) -> pd.DataFrame:
        files = sorted((self.upload_dir / str(station_id)).glob("*")) if (self.upload_dir / str(station_id)).exists() else []
        frames, rej = [], 0
        tz = self._tz(station_id)
        for f in files:
            if f.suffix.lower() not in (".csv", ".json"):
                continue
            try:
                part, r = parse_upload(read_upload(f), station_id, tz, str(f))
            except Exception as e:  # a bad file must not block the station's other files
                log.error("DIY upload %s rejected: %s", f, e)
                continue
            rej += r
            frames.append(part)
        self.rejected[str(station_id)] = rej
        if not frames:
            return empty_hourly()
        sub = pd.concat(frames, ignore_index=True)
        hourly = to_hourly(sub, ["temp_f", "rh_pct"])
        hourly["qc_source"] = None
        hourly["raw_ref"] = ",".join(sorted({str(f) for f in files}))
        d = local_date_of_hour(hourly["ts"])
        hourly = hourly[(d >= start) & (d <= end)]
        for c in ("dewpoint_f", "solar_wm2", "wind_mph"):
            hourly[c] = float("nan")
        return hourly[[*HOURLY_COLUMNS, "rh_pct", "dewpoint_f", "solar_wm2", "wind_mph"]].reset_index(drop=True)

    def fetch_daily(self, station_id: str, start: date, end: date) -> pd.DataFrame:
        h = self.fetch_hourly(station_id, start, end)
        if h.empty:
            return pd.DataFrame(columns=DAILY_COLUMNS)
        h = h.assign(date_local=local_date_of_hour(h["ts"]))
        g = h.groupby("date_local")["temp_f"]
        cov = g.count() >= 18  # TODO source: daily max/min needs 18 of 24 hours
        out = pd.DataFrame({"tmax_f": g.max().where(cov), "tmin_f": g.min().where(cov)}).reset_index()
        out["source"] = "diy"
        out["source_station_id"] = str(station_id)
        out["qc_source"] = None
        return out[DAILY_COLUMNS]
