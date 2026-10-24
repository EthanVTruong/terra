"""Empirical (split-conformal style) uncertainty from leave-one-station-out residuals.

Bins: distance to nearest valid neighbor x |dz| to it x night/day. Each bin stores a
robust sd (1.4826 * MAD) and the 10th / 90th residual percentiles.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .. import config


def _bins() -> tuple[list[float], list[float]]:
    u = config.thresholds()["uncertainty"]
    return [float(x) for x in u["distance_bins_km"]], [float(x) for x in u["dz_bins_ft"]]


def bin_keys(nearest_km: np.ndarray, nearest_dz_ft: np.ndarray, night: np.ndarray) -> np.ndarray:
    dbins, zbins = _bins()
    d = np.digitize(np.nan_to_num(nearest_km, nan=1e9), dbins)
    z = np.digitize(np.nan_to_num(np.abs(nearest_dz_ft), nan=1e9), zbins)
    n = np.where(night, "n", "d")
    return np.char.add(np.char.add(np.char.add("d", d.astype(str)), np.char.add("_z", z.astype(str))),
                       np.char.add("_", n))


def _stats(r: np.ndarray) -> dict[str, float]:
    r = r[np.isfinite(r)]
    if len(r) == 0:
        return {"sd": float("nan"), "q10": float("nan"), "q90": float("nan"), "n": 0, "bias": float("nan")}
    med = float(np.median(r))
    return {
        "sd": float(max(1.4826 * np.median(np.abs(r - med)), 1e-6)),
        "q10": float(np.quantile(r, 0.1)),
        "q90": float(np.quantile(r, 0.9)),
        "n": int(len(r)),
        "bias": med,
        "mae": float(np.mean(np.abs(r))),
    }


def fit_bins(
    residual: np.ndarray, nearest_km: np.ndarray, nearest_dz_ft: np.ndarray, night: np.ndarray
) -> dict[str, Any]:
    min_count = int(config.thresholds()["uncertainty"]["min_bin_count"])
    keys = bin_keys(nearest_km, nearest_dz_ft, night)
    table: dict[str, Any] = {}
    for k in np.unique(keys):
        st = _stats(residual[keys == k])
        if st["n"] >= min_count:
            table[k] = st
    table["_pooled_n"] = _stats(residual[night])
    table["_pooled_d"] = _stats(residual[~night])
    table["_all"] = _stats(residual)
    cands = [v for v in table.values() if v["n"] >= min_count and np.isfinite(v["sd"])]
    widest = max(cands, key=lambda v: v["q90"] - v["q10"]) if cands else table["_all"]
    table["_widest"] = widest
    return table


def lookup(
    table: dict[str, Any],
    nearest_km: np.ndarray,
    nearest_dz_ft: np.ndarray,
    night: np.ndarray,
    widest: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (sd, q10, q90) residual stats per hour."""
    keys = bin_keys(nearest_km, nearest_dz_ft, night)
    sd = np.full(len(keys), np.nan)
    q10 = np.full(len(keys), np.nan)
    q90 = np.full(len(keys), np.nan)
    for k in np.unique(keys):
        st = table.get(k) or table.get("_pooled_n" if k.endswith("_n") else "_pooled_d") or table["_all"]
        m = keys == k
        sd[m], q10[m], q90[m] = st["sd"], st["q10"], st["q90"]
    if widest is not None and widest.any():
        w = table["_widest"]
        sd[widest], q10[widest], q90[widest] = w["sd"], w["q10"], w["q90"]
    return sd, q10, q90


def bootstrap_traces(
    station_id: int,
    season: str,
    n: int = 200,
    ctx: Any | None = None,
    seed: int = 0,
) -> pd.DataFrame:
    """Resampled ``used_temp_f`` traces for a station-season (error bars for chill totals).

    Observed hours are kept as-is. Estimated hours get leave-stations-out residuals added,
    drawn as whole local days (24-hour blocks) to keep the within-day error correlation, and
    rescaled to each hour's own uncertainty (``est_sd``: its distance x |dz| x night/day bin,
    the IDW table for physics-filled hours, the widest bin for long gaps).
    Returns a frame indexed by ts with ``n`` trace columns.
    """
    from ..context import get_context
    from ..timeutil import season_bounds

    ctx = ctx or get_context()
    reg = config.region()
    start, end = season_bounds(season, reg["seasons"]["start_mmdd"], reg["seasons"]["end_mmdd"])
    r = ctx.store.readings(start, end, [station_id]).sort_values("ts").set_index("ts")
    base = r["used_temp_f"].astype("float64")
    est = r["is_estimated"].astype(bool).to_numpy()
    traces = np.repeat(base.to_numpy()[:, None], n, axis=1)
    if not est.any():
        return pd.DataFrame(traces, index=base.index, columns=[f"trace_{i}" for i in range(n)])
    pool = ctx.model.oof_blocks()  # (n_blocks, 24) residual blocks
    flat = pool[np.isfinite(pool)]
    pool_sd = float(1.4826 * np.median(np.abs(flat - np.median(flat)))) if flat.size else 1.0
    pool_sd = pool_sd if pool_sd > 0 else 1.0
    sd = r["est_sd"].to_numpy(dtype="float64")
    scale = np.where(np.isfinite(sd), sd / pool_sd, 1.0)
    rng = np.random.default_rng(seed)
    hours = pd.DatetimeIndex(base.index)
    local_hour = ((hours.tz_convert("UTC").tz_localize(None) - pd.Timedelta(hours=9)).hour).to_numpy()
    day_key = (hours.tz_convert("UTC").tz_localize(None) - pd.Timedelta(hours=9)).normalize()
    for d in pd.unique(day_key[est]):
        m = (day_key == d) & est
        picks = rng.integers(0, len(pool), size=n)
        traces[m, :] += pool[picks][:, local_hour[m]].T * scale[m][:, None]
    return pd.DataFrame(traces, index=base.index, columns=[f"trace_{i}" for i in range(n)])
