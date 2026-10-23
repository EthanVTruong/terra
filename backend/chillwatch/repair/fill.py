"""Gap filling: replace missing / invalid hours with labeled estimates."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from .. import config
from ..qc.rules import runs
from ..timeutil import utc

REPLACE = ("missing", "invalid")


def fill(readings: pd.DataFrame, expected: pd.DataFrame, widest: dict[str, Any] | None = None
         ) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Pure: readings + expected (same station(s)) -> readings with repair columns, gap list.

    * ``qc_status`` in {missing, invalid}: ``used_temp_f = expected_f``, ``is_estimated``.
    * otherwise ``used_temp_f = temp_f``.
    * no estimate -> ``used_temp_f`` NaN (downstream marks the day incomplete).
    * gaps longer than ``long_gap_days`` use the widest uncertainty bin and are tagged ``long_gap``.
    """
    long_h = int(config.thresholds()["repair"]["long_gap_days"]) * 24
    cols = ["station_id", "ts", "expected_f", "est_sd", "method"]
    e = expected[cols].rename(columns={"expected_f": "_exp", "est_sd": "_sd", "method": "_m"})
    out = readings.drop(columns=[c for c in ("_exp", "_sd", "_m") if c in readings]).merge(
        e, on=["station_id", "ts"], how="left")
    out = out.sort_values(["station_id", "ts"]).reset_index(drop=True)
    bad = out["qc_status"].isin(REPLACE).to_numpy()
    has_est = np.isfinite(out["_exp"].to_numpy(dtype="float64"))
    est = bad & has_est
    out["est_temp_f"] = out["_exp"]
    out["est_sd"] = out["_sd"]
    out["est_method"] = out["_m"].where(out["_m"].notna(), None)
    out["is_estimated"] = est
    out["used_temp_f"] = np.where(bad, np.where(has_est, out["_exp"], np.nan), out["temp_f"])
    gaps: list[dict[str, Any]] = []
    for sid, g in out.groupby("station_id", sort=False):
        gb = g["qc_status"].isin(REPLACE).to_numpy()
        for s, e_ in runs(gb):
            n = e_ - s + 1
            long_gap = n > long_h
            idx = g.index[s:e_ + 1]
            if long_gap and widest:
                out.loc[idx, "est_sd"] = np.where(out.loc[idx, "is_estimated"], widest["sd"], np.nan)
            gaps.append({
                "station_id": int(sid), "start_ts": g["ts"].iloc[s], "end_ts": g["ts"].iloc[e_], "hours": int(n),
                "estimated_hours": int(out.loc[idx, "is_estimated"].sum()), "long_gap": bool(long_gap),
            })
    out = out.drop(columns=["_exp", "_sd", "_m"])
    out.loc[~out["is_estimated"], "est_method"] = out.loc[~out["is_estimated"], "est_method"]
    return out, gaps


def fill_store(start: datetime, end: datetime, ctx: Any | None = None,
               station_ids: list[int] | None = None) -> list[dict[str, Any]]:
    """Fill [start, end] in the store; returns the gap list."""
    from ..context import get_context

    ctx = ctx or get_context()
    ids = station_ids or [int(s) for s in ctx.region_stations()["station_id"]]
    rd = ctx.store.readings(utc(start), utc(end), ids)
    if rd.empty:
        return []
    exp = ctx.expected(utc(start), utc(end), ids)
    out, gaps = fill(rd, exp, ctx.model.unc_model.get("_widest"))
    ctx.store.update_readings(out, ["est_temp_f", "est_sd", "used_temp_f", "is_estimated", "est_method"])
    return gaps
