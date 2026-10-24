"""Ground elevation at arbitrary points, from the public AWS terrain tiles (Terrarium PNG).

``https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png``; elevation in metres
= (R * 256 + G + B / 256) - 32768. Zoom 9 (~300 m pixels at 37 N) is plenty for a 5 km grid.
Tiles are cached under ``data/raw/terrain/{z}/{x}/{y}.png``. Ocean cells come back negative.
"""

from __future__ import annotations

import io
import logging
import math
import time
from pathlib import Path

import httpx
import numpy as np

from .. import config

log = logging.getLogger(__name__)
URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
FT_PER_M = 3.280839895


def _tile_xy(lat: np.ndarray, lon: np.ndarray, z: int) -> tuple[np.ndarray, np.ndarray]:
    """Fractional tile coordinates (Web Mercator)."""
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    lat_r = np.radians(lat)
    y = (1.0 - np.log(np.tan(lat_r) + 1.0 / np.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


class TerrainTiles:
    def __init__(self, zoom: int = 9, cache_dir: str | Path | None = None, client: httpx.Client | None = None) -> None:
        self.zoom = zoom
        self.cache_dir = Path(cache_dir) if cache_dir else config.data_dir() / "raw" / "terrain"
        self.client = client or httpx.Client(timeout=60.0)
        self._tiles: dict[tuple[int, int], np.ndarray] = {}

    def _tile(self, x: int, y: int) -> np.ndarray:
        key = (x, y)
        if key in self._tiles:
            return self._tiles[key]
        from PIL import Image

        path = self.cache_dir / str(self.zoom) / str(x) / f"{y}.png"
        if not path.exists():
            for attempt in range(4):
                try:
                    r = self.client.get(URL.format(z=self.zoom, x=x, y=y))
                    r.raise_for_status()
                    break
                except httpx.HTTPError:
                    if attempt == 3:
                        raise
                    time.sleep(2 ** attempt)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r.content)
        rgb = np.asarray(Image.open(io.BytesIO(path.read_bytes())).convert("RGB"), dtype="float64")
        elev = rgb[..., 0] * 256.0 + rgb[..., 1] + rgb[..., 2] / 256.0 - 32768.0
        self._tiles[key] = elev
        return elev

    def elevation_m(self, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
        lat = np.asarray(lat, dtype="float64")
        lon = np.asarray(lon, dtype="float64")
        fx, fy = _tile_xy(lat, lon, self.zoom)
        tx, ty = np.floor(fx).astype(int), np.floor(fy).astype(int)
        out = np.full(lat.shape, np.nan)
        for x, y in sorted(set(zip(tx.ravel().tolist(), ty.ravel().tolist(), strict=True))):
            m = (tx == x) & (ty == y)
            t = self._tile(x, y)
            size = t.shape[0]
            px = np.clip(((fx[m] - x) * size).astype(int), 0, size - 1)
            py = np.clip(((fy[m] - y) * size).astype(int), 0, size - 1)
            out[m] = t[py, px]
        return out

    def elevation_ft(self, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
        return self.elevation_m(lat, lon) * FT_PER_M
