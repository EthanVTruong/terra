"""PRISM daily 800 m tmin/tmax at points (model features only; P2).

VERIFY: URL pattern, terms of use, daily window and latency (see docs/DECISIONS.md); the
template is configurable via ``PRISM_URL_TEMPLATE``. Requires ``rasterio`` (optional extra).
Values are cached per (variable, date) under ``data/raw/prism``.
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from .. import config
from .base import DAILY_COLUMNS

log = logging.getLogger(__name__)

# TODO source / VERIFY: PRISM web service pattern (returns a zip with a COG / BIL raster)
DEFAULT_URL = "https://services.nacse.org/prism/data/get/us/800m/{var}/{yyyymmdd}"


class PrismSource:
    source = "prism"

    def __init__(self, cache_dir: str | Path | None = None, url_template: str | None = None,
                 client: httpx.Client | None = None) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else config.data_dir() / "raw" / "prism"
        self.url = url_template or os.environ.get("PRISM_URL_TEMPLATE", DEFAULT_URL)
        self.client = client or httpx.Client(timeout=120)
        self.points: dict[str, tuple[float, float]] = {}

    def register_point(self, point_id: str, lat: float, lon: float) -> None:
        self.points[str(point_id)] = (lat, lon)

    def list_stations(self) -> pd.DataFrame:
        return pd.DataFrame(columns=["source", "source_station_id", "name", "county", "lat", "lon",
                                     "elevation_ft", "is_active", "connect_date", "disconnect_date"])

    def fetch_hourly(self, station_id: str, start: date, end: date) -> pd.DataFrame:
        raise NotImplementedError("PRISM is daily only")

    def _raster(self, var: str, d: date) -> Path:
        out = self.cache_dir / var / f"{d:%Y%m%d}.zip"
        if not out.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            r = self.client.get(self.url.format(var=var, yyyymmdd=f"{d:%Y%m%d}"))
            r.raise_for_status()
            out.write_bytes(r.content)
        return out

    def _sample(self, path: Path, lat: float, lon: float) -> float:
        import rasterio  # optional dependency

        with rasterio.open(f"zip://{path}") as ds:
            v = next(ds.sample([(lon, lat)]))[0]
            nod = ds.nodata
        return float("nan") if nod is not None and v == nod else float(v)

    def fetch_daily(self, station_id: str, start: date, end: date) -> pd.DataFrame:
        lat, lon = self.points[str(station_id)]
        rows = []
        d = start
        while d <= end:
            row = {"source": "prism", "source_station_id": str(station_id), "date_local": d, "qc_source": None}
            for var, col in (("tmax", "tmax_f"), ("tmin", "tmin_f")):
                try:
                    c = self._sample(self._raster(var, d), lat, lon)
                    row[col] = c * 9 / 5 + 32 if np.isfinite(c) else np.nan
                except Exception as e:  # missing day / no rasterio: feature stays NaN
                    log.warning("PRISM %s %s unavailable: %s", var, d, e)
                    row[col] = np.nan
            rows.append(row)
            d += timedelta(days=1)
        return pd.DataFrame(rows, columns=DAILY_COLUMNS)
