"""Runtime context: which store and model the public API functions use.

The spec'd API (``predict_expected(station_id, start, end)``, ``run_detection(as_of)``...)
takes no data arguments; it reads from the active context. Tests, the bench and the demo
replay build their own ``Context`` and pass it explicitly or install it with ``set_context``.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

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
        st = self.store.stations()
        if "in_region" in st.columns:
            st = st[st["in_region"].fillna(True).astype(bool)]
        return st.reset_index(drop=True)

    def active_station_ids(self, at: datetime) -> list[int]:
        st = self.region_stations()
        day = utc(at).tz_localize(None) - pd.Timedelta(hours=9)
        return [int(s) for s in st.loc[active_on(st, day.normalize()), "station_id"]]

    # ------------------------------------------------------------------ field / expected
    def invalidate(self) -> None:
        self._fields.clear()

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
        st = self.region_stations().set_index("station_id")
        max_km = float(config.thresholds()["neighbors"]["max_km"])
        f = st.loc[[x for x in flagged if x in st.index]]
        out = []
        for sid in ids:
            if sid not in st.index:
                continue
            d = haversine_km(st.at[sid, "lat"], st.at[sid, "lon"], f["lat"].to_numpy(), f["lon"].to_numpy())
            others = f.index.to_numpy() != sid
            if (d[others] <= max_km).any():
                out.append(sid)
        return out

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
                rd = self.store.readings(s, e)
                flagged = set(rd.loc[(rd["qc_status"] == "invalid") & (rd["qc_base"] == "ok"),
                                     "station_id"].astype(int))
                redo = self._near(ids, flagged)
                if redo:
                    live = self._predict(self.field(s, e, valid_col="qc_status"), redo)
                    df = pd.concat([df[~df["station_id"].isin(redo)], live], ignore_index=True)
        else:
            df = self._predict(self.field(s, e, valid_col=valid_col), ids)
        return self._with_offsets(df) if with_offsets else df.reset_index(drop=True)

    def expected_from_readings(self, readings: pd.DataFrame, start: datetime, end: datetime,
                               station_ids: list[int], with_offsets: bool = True) -> pd.DataFrame:
        """Expected values from an explicit readings frame (validity from its ``qc_status``)."""
        fld = build_field(readings, self.region_stations(), start, end, valid_col="qc_status")
        df = self._predict(fld, station_ids)
        return self._with_offsets(df) if with_offsets else df

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
