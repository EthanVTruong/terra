"""Runtime context: which store and model the public API functions use.

The spec'd API (``predict_expected(station_id, start, end)``, ``run_detection(as_of)``...)
takes no data arguments; it reads from the active context. Tests, the bench and the demo
replay build their own ``Context`` and pass it explicitly or install it with ``set_context``.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from . import config
from .qc.model import ModelBundle, active_model_path, apply_offsets, idw_only_bundle, predict_station_frame
from .qc.neighbors import Field, active_on, build_field, haversine_km
from .store import MemoryStore, SqlStore, Store
from .timeutil import utc


class Context:
    def __init__(self, store: Store, model: ModelBundle | None = None) -> None:
        self.store = store
        self._model = model
        self._fields: dict[Any, Field] = {}
        self._frozen: pd.DataFrame | None = None  # precomputed expected (no offsets), bench/demo
        self._frozen_range: tuple[pd.Timestamp, pd.Timestamp] | None = None
        self._refined: dict[Any, pd.DataFrame] = {}
        self._stations: pd.DataFrame | None = None
        self._near_cache: tuple[dict[int, int], np.ndarray] | None = None

    # ------------------------------------------------------------------ model
    @property
    def model(self) -> ModelBundle:
        if self._model is None:
            p = active_model_path()
            self._model = ModelBundle.load(p) if p else idw_only_bundle()
        return self._model

    @model.setter
    def model(self, m: ModelBundle) -> None:
        self._model = m
        self.invalidate()

    # ------------------------------------------------------------------ stations
    def region_stations(self) -> pd.DataFrame:
        if self._stations is None:
            st = self.store.stations()
            if "in_region" in st.columns:
                st = st[st["in_region"].fillna(True).astype(bool)]
            self._stations = st.reset_index(drop=True)
            self._near_cache = None
        return self._stations

    def reload_stations(self) -> None:
        self._stations = None

    def active_station_ids(self, at: datetime) -> list[int]:
        st = self.region_stations()
        day = utc(at).tz_localize(None) - pd.Timedelta(hours=9)
        return [int(s) for s in st.loc[active_on(st, day.normalize()), "station_id"]]

    # ------------------------------------------------------------------ field / expected
    def invalidate(self) -> None:
        """Drop cached fields. Cached flag-refined expected values stay valid only while the
        frozen (replay) data is fixed, so they are kept in frozen mode and dropped otherwise."""
        self._fields.clear()
        if self._frozen is None:
            self._refined.clear()

    def field(self, start: datetime, end: datetime, drop_ids: set[int] | None = None,
              valid_col: str = "qc_status") -> Field:
        key = (utc(start), utc(end), frozenset(drop_ids or ()), valid_col)
        if key not in self._fields:
            if len(self._fields) > 8:
                self._fields.clear()
            st = self.region_stations()
            r = self.store.readings(utc(start), utc(end), st["station_id"].tolist())
            self._fields[key] = build_field(r, st, start, end, valid_col=valid_col, drop_ids=drop_ids)
        return self._fields[key]

    def _predict(self, fld: Field, ids: list[int]) -> pd.DataFrame:
        frames = []
        for sid in ids:
            if sid not in fld.pos:
                continue
            f = predict_station_frame(fld, self.model, sid)
            f.insert(0, "station_id", sid)
            frames.append(f)
        if not frames:
            return pd.DataFrame(columns=EXPECTED_COLUMNS)
        return pd.concat(frames, ignore_index=True)

    def precompute_expected(self, start: datetime, end: datetime, chunk_days: int = 31) -> None:
        """Freeze expected values (source-QC validity, pre-offset) for all region stations.

        Used by replays (bench / demo) where the full data is known up front. Expected values
        for hour t only use neighbor values at hour t, so this equals computing them day by day.
        """
        ids = [int(s) for s in self.region_stations()["station_id"]]
        s, e = utc(start), utc(end)
        parts = []
        cur = s
        while cur <= e:
            ce = min(e, cur + pd.Timedelta(days=chunk_days) - pd.Timedelta(hours=1))
            parts.append(self._predict(self.field(cur, ce, valid_col="qc_base"), ids))
            self._fields.clear()
            cur = ce + pd.Timedelta(hours=1)
        self._frozen = pd.concat(parts, ignore_index=True).sort_values(["station_id", "ts"])
        self._frozen_range = (s, e)

    def freeze_from(self, other: Context) -> None:
        self._frozen, self._frozen_range = other._frozen, other._frozen_range

    def _near(self, ids: list[int], flagged: set[int]) -> list[int]:
        """Targets within neighbor range of any flagged station."""
        if not flagged:
            return []
        if self._near_cache is None:
            st = self.region_stations()
            max_km = float(config.thresholds()["neighbors"]["max_km"])
            lat, lon = st["lat"].to_numpy(), st["lon"].to_numpy()
            close = haversine_km(lat[:, None], lon[:, None], lat[None, :], lon[None, :]) <= max_km
            np.fill_diagonal(close, False)
            self._near_cache = ({int(s): i for i, s in enumerate(st["station_id"])}, close)
        pos, close = self._near_cache
        fi = [pos[x] for x in flagged if x in pos]
        if not fi:
            return []
        hit = close[:, fi].any(axis=1)
        return [sid for sid in ids if sid in pos and hit[pos[sid]]]

    def expected(
        self,
        start: datetime,
        end: datetime,
        station_ids: list[int] | None = None,
        with_offsets: bool = True,
        valid_col: str = "qc_status",
    ) -> pd.DataFrame:
        """Expected values for stations over [start, end].

        ``valid_col="qc_status"`` (default) excludes neighbor hours flagged by detection;
        ``"qc_base"`` uses source QC only (detection's first pass).
        """
        s, e = utc(start), utc(end)
        ids = station_ids if station_ids is not None else [int(x) for x in self.region_stations()["station_id"]]
        if self._frozen is not None:
            f = self._frozen
            df = f[f["station_id"].isin(ids) & (f["ts"] >= s) & (f["ts"] <= e)].copy()
            if valid_col == "qc_status":
                df = self.refine(df, self.store.readings(s, e), ids)
        else:
            df = self._predict(self.field(s, e, valid_col=valid_col), ids)
        return self._with_offsets(df) if with_offsets else df.reset_index(drop=True)

    def refine(self, base: pd.DataFrame, readings: pd.DataFrame, targets: list[int]) -> pd.DataFrame:
        """Recompute ``base`` (source-QC expected values, no offsets) where detector-flagged
        neighbor hours must be dropped. Expected values at hour t depend only on hour t, so
        only targets near a flagged station and only the flagged time segments are redone.
        """
        fl = readings[(readings["qc_status"] == "invalid") & (readings["qc_base"] == "ok")]
        if fl.empty:
            return base
        st = self.region_stations()
        days = fl["ts"].dt.floor("D")
        parts = []
        for day, f_day in fl.groupby(days):
            redo = self._near(targets, set(f_day["station_id"].astype(int)))
            if not redo:
                continue
            a, b = day, day + pd.Timedelta(hours=23)
            seg = readings[(readings["ts"] >= a) & (readings["ts"] <= b)]
            # cache per UTC day: same data, same flagged neighbor-hours, same targets -> same answer
            sig = (int(pd.util.hash_pandas_object(f_day[["station_id", "ts"]], index=False).sum()),
                   int(pd.util.hash_pandas_object(seg[["station_id", "ts", "temp_f", "qc_base"]], index=False).sum()))
            key = (day, sig, tuple(sorted(redo)))
            if key not in self._refined:
                self._refined[key] = self._predict(build_field(seg, st, a, b, valid_col="qc_status"), redo)
                if len(self._refined) > 5000:
                    self._refined.pop(next(iter(self._refined)))
            parts.append(self._refined[key])
        if not parts:
            return base
        live = pd.concat(parts, ignore_index=True)
        live = live[(live["ts"] >= readings["ts"].min()) & (live["ts"] <= readings["ts"].max())]
        out = pd.concat([base, live], ignore_index=True)
        return out.drop_duplicates(["station_id", "ts"], keep="last").sort_values(["station_id", "ts"])

    def expected_from_readings(self, readings: pd.DataFrame, start: datetime, end: datetime,
                               station_ids: list[int], base: pd.DataFrame | None = None,
                               with_offsets: bool = True) -> pd.DataFrame:
        """Expected values given an explicit readings frame (neighbor validity from its ``qc_status``).

        ``base`` = the same stations' source-QC expected values (no offsets), if already computed.
        """
        if base is None:
            base = self.expected(start, end, station_ids, with_offsets=False, valid_col="qc_base")
        base = base[base["station_id"].isin(station_ids)]
        df = self.refine(base, readings, station_ids)
        return self._with_offsets(df) if with_offsets else df.reset_index(drop=True)

    def _with_offsets(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df.reset_index(drop=True)
        offs = self.store.offsets()
        if offs.empty:
            return df.reset_index(drop=True)
        out = []
        for sid, g in df.groupby("station_id", sort=False):
            out.append(apply_offsets(g, offs[offs["station_id"] == sid]))
        return pd.concat(out, ignore_index=True)


EXPECTED_COLUMNS = ["station_id", "ts", "expected_f", "est_sd", "p10", "p90", "n_neighbors", "method", "night",
                    "t_base", "nbr_median", "nearest_km", "nearest_dz_ft", "offset_f"]


_CTX: Context | None = None


def default_context() -> Context:
    url = os.environ.get("CHILLWATCH_DB_URL")
    if url:
        return Context(SqlStore(url))
    pq = config.data_dir() / "parquet"
    reg = config.region().get("name", "region")
    rp, sp = pq / f"readings_{reg}.parquet", pq / f"stations_{reg}.parquet"
    if rp.exists() and sp.exists():
        return Context(MemoryStore.from_parquet(rp, sp))
    return Context(MemoryStore())


def get_context() -> Context:
    global _CTX
    if _CTX is None:
        _CTX = default_context()
    return _CTX


def set_context(ctx: Context | None) -> None:
    global _CTX
    _CTX = ctx


__all__ = ["Context", "get_context", "set_context"]
