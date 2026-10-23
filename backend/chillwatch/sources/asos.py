"""NOAA ASOS airport stations via the Iowa Environmental Mesonet (IEM) archive.

No key needed. Used for real-data validation while a CIMIS key is unavailable; it is a
different network (airports, routine METARs) and its results must be labeled ASOS.

* Stations: ``GET https://mesonet.agron.iastate.edu/geojson/network/CA_ASOS.geojson``
  (elevation in metres, archive_begin / archive_end dates).
* Data: ``GET .../cgi-bin/request/asos.py?station=SAC&data=tmpf&...&tz=Etc/UTC&report_type=3``
  (report_type 3 = routine hourly METARs, normally at :53). An observation is assigned to the
  hour ending at the next full hour (10:53 UTC -> 11:00 UTC); these are spot values, not
  hourly means as in CIMIS.
* One request per station per <= 1-year window, >= 1 s apart; raw CSV cached under
  ``data/raw/asos/{station}/``.
"""

from __future__ import annotations

import io
import json
import logging
import time
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .. import config
from ..normalize import local_date_of_hour
from .base import HOURLY_COLUMNS, HOURLY_EXTRA_COLUMNS, STATION_COLUMNS, empty_hourly
from .cimis import windows

log = logging.getLogger(__name__)

IEM = "https://mesonet.agron.iastate.edu"
FT_PER_M = 3.280839895
MPH_PER_KT = 1.150779448


class _Transient(RuntimeError):
    pass


def parse_network(payload: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for f in payload.get("features", []):
        p = f.get("properties", {})
        lon, lat = f["geometry"]["coordinates"][:2]
        end = pd.to_datetime(p.get("archive_end")) if p.get("archive_end") else pd.NaT
        rows.append({
            "source": "asos",
            "source_station_id": str(f.get("id")),
            "name": p.get("sname"),
            "county": p.get("county"),
            "lat": float(lat),
            "lon": float(lon),
            "elevation_ft": float(p["elevation"]) * FT_PER_M if p.get("elevation") is not None else np.nan,
            "is_active": bool(p.get("online", pd.isna(end))) and pd.isna(end),
            "connect_date": pd.to_datetime(p.get("archive_begin")) if p.get("archive_begin") else pd.NaT,
            "disconnect_date": end,
        })
    return pd.DataFrame(rows, columns=STATION_COLUMNS)


def parse_csv(text: str, raw_ref: str | None = None) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text), dtype={"station": str})
    if df.empty or "valid" not in df:
        return empty_hourly()
    ts = pd.to_datetime(df["valid"], format="%Y-%m-%d %H:%M", utc=True)
    out = pd.DataFrame({
        "source": "asos",
        "source_station_id": df["station"].astype(str),
        "_obs": ts,
        "ts": ts.dt.ceil("h"),
        "temp_f": pd.to_numeric(df.get("tmpf"), errors="coerce"),
        "dewpoint_f": pd.to_numeric(df.get("dwpf"), errors="coerce"),
        "rh_pct": pd.to_numeric(df.get("relh"), errors="coerce"),
        "wind_mph": pd.to_numeric(df.get("sknt"), errors="coerce") * MPH_PER_KT,
        "solar_wm2": np.nan,
        "qc_source": None,
        "raw_ref": raw_ref,
    })
    # one value per hour: the latest observation inside the hour (the routine :53 report)
    out = out.sort_values("_obs").drop_duplicates(["source_station_id", "ts"], keep="last")
    return out[HOURLY_COLUMNS + HOURLY_EXTRA_COLUMNS].reset_index(drop=True)


class AsosSource:
    source = "asos"

    def __init__(self, cache_dir: str | Path | None = None, network: str = "CA_ASOS",
                 client: httpx.Client | None = None, min_interval_s: float = 1.0, window_days: int = 366,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else config.data_dir() / "raw" / "asos"
        self.network = network
        self.client = client or httpx.Client(timeout=180.0)
        self.min_interval_s = min_interval_s
        self.window_days = window_days
        self._sleep = sleep
        self._last = 0.0

    @retry(retry=retry_if_exception_type(_Transient), stop=stop_after_attempt(6),
           wait=wait_exponential(multiplier=2, min=2, max=60), reraise=True)
    def _get_text(self, url: str, params: dict[str, Any] | list[tuple[str, Any]] | None = None) -> str:
        wait = self.min_interval_s - (time.monotonic() - self._last)
        if wait > 0:
            self._sleep(wait)
        self._last = time.monotonic()
        try:
            r = self.client.get(url, params=params)
        except (httpx.TransportError, httpx.TimeoutException) as e:
            raise _Transient(str(e)) from e
        if r.status_code == 429 or r.status_code >= 500:
            raise _Transient(f"IEM {r.status_code}")
        r.raise_for_status()
        return r.text

    def list_stations(self, refresh: bool = False) -> pd.DataFrame:
        path = self.cache_dir / f"{self.network}.geojson"
        if path.exists() and not refresh:
            payload = json.loads(path.read_text())
        else:
            payload = json.loads(self._get_text(f"{IEM}/geojson/network/{self.network}.geojson"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload))
        return parse_network(payload)

    def _window(self, station: str, start: date, end: date, refresh: bool) -> tuple[str, str]:
        path = self.cache_dir / station / f"{start.isoformat()}_{end.isoformat()}.csv"
        if path.exists() and not refresh:
            return path.read_text(), str(path)
        # PST dates start..end span UTC start 08:00 .. end+1 09:00; IEM's end day is exclusive
        a, b = start, end + timedelta(days=2)
        params = [("station", station), ("data", "tmpf"), ("data", "dwpf"), ("data", "relh"), ("data", "sknt"),
                  ("year1", a.year), ("month1", a.month), ("day1", a.day),
                  ("year2", b.year), ("month2", b.month), ("day2", b.day),
                  ("tz", "Etc/UTC"), ("format", "onlycomma"), ("latlon", "no"), ("elev", "no"),
                  ("missing", "empty"), ("trace", "empty"), ("direct", "no"), ("report_type", "3")]
        text = self._get_text(f"{IEM}/cgi-bin/request/asos.py", params)
        if not text.startswith("station,"):
            raise RuntimeError(f"IEM returned unexpected content: {text[:120]!r}")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text)
        tmp.replace(path)
        return text, str(path)

    def fetch_hourly(self, station_id: str, start: date, end: date, refresh: bool = False) -> pd.DataFrame:
        frames = []
        for ws, we in windows(start, end, self.window_days):
            text, ref = self._window(str(station_id), ws, we, refresh)
            df = parse_csv(text, ref)
            if len(df):
                d = local_date_of_hour(df["ts"])
                frames.append(df[(d >= ws) & (d <= we)])
        frames = [f for f in frames if len(f)]
        if not frames:
            return empty_hourly()
        return pd.concat(frames, ignore_index=True)

    def fetch_daily(self, station_id: str, start: date, end: date) -> pd.DataFrame:
        raise NotImplementedError("daily summaries are not used for ASOS")
