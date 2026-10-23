"""CIMIS (California DWR) station adapter.

* Station list: ``GET {base}/station`` (no key).
* Data: ``GET {base}/data?appKey=..&targets=..&startDate=..&endDate=..&dataItems=..&unitOfMeasure=E``.
* <= 1,750 records per request: hourly requests go out in <= 60-day windows, halved on refusal.
* 1 request / second. Every raw response is cached under ``data/raw/cimis/{station}/``
  and cached ranges are never downloaded again unless ``refresh=True`` (late data).
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .. import config
from ..normalize import cimis_ts_utc
from .base import DAILY_COLUMNS, HOURLY_COLUMNS, HOURLY_EXTRA_COLUMNS, STATION_COLUMNS, empty_hourly

log = logging.getLogger(__name__)

BASE_URL = "https://et.water.ca.gov/api"
HOURLY_ITEMS_P0 = ["hly-air-tmp"]
HOURLY_ITEMS_P1 = ["hly-rel-hum", "hly-dew-pnt", "hly-sol-rad", "hly-wind-spd"]
DAILY_ITEMS = ["day-air-tmp-max", "day-air-tmp-min"]

# dataItems name -> response record key -> canonical column
ITEM_FIELDS = {
    "hly-air-tmp": ("HlyAirTmp", "temp_f"),
    "hly-rel-hum": ("HlyRelHum", "rh_pct"),
    "hly-dew-pnt": ("HlyDewPnt", "dewpoint_f"),
    "hly-sol-rad": ("HlySolRad", "solar_wm2"),
    "hly-wind-spd": ("HlyWindSpd", "wind_mph"),
    "day-air-tmp-max": ("DayAirTmpMax", "tmax_f"),
    "day-air-tmp-min": ("DayAirTmpMin", "tmin_f"),
}
# 1 langley/day = 41,840 J/m^2 per 86,400 s
LY_PER_DAY_TO_WM2 = 41840.0 / 86400.0
MPS_TO_MPH = 3600.0 / 1609.344


class CimisError(RuntimeError):
    pass


class CimisAuthError(CimisError):
    pass


class CimisRefused(CimisError):
    """The API refused the request (e.g. too many records); caller should shrink the window."""


class _Transient(CimisError):
    pass


def parse_hms_coord(value: Any) -> float:
    """``"38º32'8N / 38.535694"`` -> 38.535694 (decimal part after the slash)."""
    if value is None:
        return float("nan")
    s = str(value)
    part = s.split("/")[-1].strip()
    try:
        return float(part)
    except ValueError:
        return float("nan")


def _parse_date(value: Any) -> pd.Timestamp | None:
    if value in (None, ""):
        return None
    ts = pd.to_datetime(str(value), format="mixed", errors="coerce")
    return None if pd.isna(ts) else ts.normalize()


def _bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def parse_stations(payload: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for st in payload.get("Stations", []):
        rows.append(
            {
                "source": "cimis",
                "source_station_id": str(st.get("StationNbr")).strip(),
                "name": st.get("Name"),
                "county": st.get("County"),
                "lat": parse_hms_coord(st.get("HmsLatitude")),
                "lon": parse_hms_coord(st.get("HmsLongitude")),
                "elevation_ft": pd.to_numeric(st.get("Elevation"), errors="coerce"),
                "is_active": _bool(st.get("IsActive")),
                "connect_date": _parse_date(st.get("ConnectDate")),
                "disconnect_date": _parse_date(st.get("DisconnectDate")),
            }
        )
    df = pd.DataFrame(rows, columns=STATION_COLUMNS)
    df["connect_date"] = pd.to_datetime(df["connect_date"])
    df["disconnect_date"] = pd.to_datetime(df["disconnect_date"])
    return df


def _records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("Data") or {}
    out: list[dict[str, Any]] = []
    for prov in data.get("Providers", []) or []:
        out.extend(prov.get("Records", []) or [])
    return out


def _value(rec: dict[str, Any], key: str) -> tuple[float, str | None, str | None]:
    item = rec.get(key)
    if not isinstance(item, dict):
        return float("nan"), None, None
    v = pd.to_numeric(item.get("Value"), errors="coerce")
    qc = item.get("Qc")
    unit = item.get("Unit")
    return (float(v) if v is not None and not pd.isna(v) else float("nan")), qc, unit


def _convert(col: str, v: float, unit: str | None) -> float:
    u = (unit or "").upper()
    if col in ("temp_f", "dewpoint_f", "tmax_f", "tmin_f") and "C" in u and "F" not in u:
        return v * 9.0 / 5.0 + 32.0
    if col == "solar_wm2" and "LY" in u:
        return v * LY_PER_DAY_TO_WM2
    if col == "wind_mph" and ("M/S" in u or "MPS" in u):
        return v * MPS_TO_MPH
    return v


def parse_hourly(payload: dict[str, Any], raw_ref: str | None = None) -> pd.DataFrame:
    recs = _records(payload)
    if not recs:
        return empty_hourly()
    rows = []
    for r in recs:
        row: dict[str, Any] = {
            "source": "cimis",
            "source_station_id": str(r.get("Station")).strip(),
            "_date": r.get("Date"),
            "_hour": r.get("Hour"),
            "raw_ref": raw_ref,
        }
        for item, (key, col) in ITEM_FIELDS.items():
            if not item.startswith("hly-"):
                continue
            v, qc, unit = _value(r, key)
            row[col] = _convert(col, v, unit)
            if col == "temp_f":
                row["qc_source"] = qc
        rows.append(row)
    df = pd.DataFrame(rows)
    df["ts"] = cimis_ts_utc(df["_date"], df["_hour"]).set_axis(df.index)
    for c in ["temp_f", "qc_source", *HOURLY_EXTRA_COLUMNS]:
        if c not in df.columns:
            df[c] = None if c == "qc_source" else np.nan
    return df[HOURLY_COLUMNS + HOURLY_EXTRA_COLUMNS]


def parse_daily(payload: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for r in _records(payload):
        tmax, qmax, umax = _value(r, "DayAirTmpMax")
        tmin, qmin, umin = _value(r, "DayAirTmpMin")
        qcs = "".join(q.strip() for q in (qmax, qmin) if q and q.strip()) or None
        rows.append(
            {
                "source": "cimis",
                "source_station_id": str(r.get("Station")).strip(),
                "date_local": pd.to_datetime(r.get("Date")).date(),
                "tmax_f": _convert("tmax_f", tmax, umax),
                "tmin_f": _convert("tmin_f", tmin, umin),
                "qc_source": qcs,
            }
        )
    return pd.DataFrame(rows, columns=DAILY_COLUMNS)


def windows(start: date, end: date, days: int) -> Iterator[tuple[date, date]]:
    """Deterministic [start, end] chunks of ``days`` days (so backfills hit the cache on resume)."""
    cur = start
    while cur <= end:
        we = min(end, cur + timedelta(days=days - 1))
        yield cur, we
        cur = we + timedelta(days=1)


class CimisSource:
    source = "cimis"

    def __init__(
        self,
        app_key: str | None = None,
        cache_dir: str | Path | None = None,
        client: httpx.Client | None = None,
        base_url: str = BASE_URL,
        min_interval_s: float = 1.0,
        window_days: int = 60,
        hourly_items: list[str] | None = None,
        timeout_s: float = 120.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.app_key = app_key if app_key is not None else os.environ.get("CIMIS_APP_KEY")
        self.cache_dir = Path(cache_dir) if cache_dir else config.data_dir() / "raw" / "cimis"
        self.client = client or httpx.Client(timeout=timeout_s, headers={"Accept": "application/json"})
        self.base_url = base_url.rstrip("/")
        self.min_interval_s = min_interval_s
        self.window_days = window_days
        self.hourly_items = hourly_items or HOURLY_ITEMS_P0 + HOURLY_ITEMS_P1
        self._sleep = sleep
        self._last_request = 0.0
        self.requests_made = 0

    # ------------------------------------------------------------------ http
    def _throttle(self) -> None:
        wait = self.min_interval_s - (time.monotonic() - self._last_request)
        if wait > 0:
            self._sleep(wait)
        self._last_request = time.monotonic()

    @retry(
        retry=retry_if_exception_type(_Transient),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        reraise=True,
    )
    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._throttle()
        self.requests_made += 1
        try:
            resp = self.client.get(f"{self.base_url}/{path}", params=params)
        except (httpx.TransportError, httpx.TimeoutException) as e:
            raise _Transient(str(e)) from e
        if resp.status_code in (401, 403):
            raise CimisAuthError(f"CIMIS refused credentials ({resp.status_code})")
        if 400 <= resp.status_code < 500:
            raise CimisRefused(f"CIMIS {resp.status_code}: {resp.text[:200]}")
        if resp.status_code >= 500:
            raise _Transient(f"CIMIS {resp.status_code}")
        try:
            payload = resp.json()
        except ValueError as e:
            raise _Transient("non-JSON response") from e
        if isinstance(payload, dict) and payload.get("Data") is None and payload.get("Stations") is None:
            # error envelopes such as {"Message": "..."} or {"Errors": ...}
            raise CimisRefused(f"CIMIS error payload: {str(payload)[:200]}")
        return payload

    # ------------------------------------------------------------------ cache
    def _cache_path(self, station: str, kind: str, start: date, end: date) -> Path:
        name = f"{start.isoformat()}_{end.isoformat()}.json"
        if kind != "hourly":
            name = f"{kind}_{name}"
        return self.cache_dir / str(station) / name

    def _cached_or_fetch(
        self, station: str, kind: str, items: list[str], start: date, end: date, refresh: bool
    ) -> list[tuple[dict[str, Any], str]]:
        path = self._cache_path(station, kind, start, end)
        if path.exists() and not refresh:
            return [(json.loads(path.read_text()), str(path))]
        if not self.app_key:
            raise CimisAuthError("CIMIS_APP_KEY is not set")
        params = {
            "appKey": self.app_key,
            "targets": station,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dataItems": ",".join(items),
            "unitOfMeasure": "E",
        }
        try:
            payload = self._get("data", params)
        except CimisRefused:
            if start >= end:
                raise
            mid = start + timedelta(days=(end - start).days // 2)
            log.info("CIMIS refused %s %s..%s; halving window", station, start, end)
            return self._cached_or_fetch(station, kind, items, start, mid, refresh) + self._cached_or_fetch(
                station, kind, items, mid + timedelta(days=1), end, refresh
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(path)
        return [(payload, str(path))]

    # ------------------------------------------------------------------ protocol
    def list_stations(self, refresh: bool = False) -> pd.DataFrame:
        path = self.cache_dir / "stations.json"
        if path.exists() and not refresh:
            payload = json.loads(path.read_text())
        else:
            payload = self._get("station")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload))
        return parse_stations(payload)

    def fetch_hourly(self, station_id: str, start: date, end: date, refresh: bool = False) -> pd.DataFrame:
        frames = []
        for ws, we in windows(start, end, self.window_days):
            for payload, ref in self._cached_or_fetch(
                str(station_id), "hourly", self.hourly_items, ws, we, refresh
            ):
                frames.append(parse_hourly(payload, ref))
        frames = [f for f in frames if not f.empty]
        if not frames:
            return empty_hourly()
        return pd.concat(frames, ignore_index=True)

    def fetch_daily(self, station_id: str, start: date, end: date, refresh: bool = False) -> pd.DataFrame:
        frames = []
        # daily records: 1 per day, so a year fits well under the record limit
        for ws, we in windows(start, end, 365):
            for payload, _ in self._cached_or_fetch(str(station_id), "daily", DAILY_ITEMS, ws, we, refresh):
                frames.append(parse_daily(payload))
        frames = [f for f in frames if not f.empty]
        if not frames:
            return pd.DataFrame(columns=DAILY_COLUMNS)
        return pd.concat(frames, ignore_index=True)
