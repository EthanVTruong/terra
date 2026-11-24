"""Reference expectation for the logger: the physics core and the ML core, weighted by uncertainty.

For every hour the logger reports, ChillWatch predicts what an air-temperature sensor at the
logger's location *should* read from the surrounding reference stations (CIMIS), two ways:

* **Physics core** - inverse-distance weighting of lapse-adjusted neighbors (``qc/neighbors.py``,
  fitted by ``chillwatch fit-physics``); its 1-sigma comes from the physics fit's
  leave-stations-out residual bins (``repair/uncertainty.py``).
* **ML core** - the LightGBM neighbor model (``qc/model.py``); its 1-sigma comes from the
  model's leave-one-station-out residual bins stored with the bundle.

The two are combined with minimum-variance weights. For errors with correlation ``rho``::

    w_p = (s_m^2 - rho s_p s_m) / (s_p^2 + s_m^2 - 2 rho s_p s_m),   w_m = 1 - w_p
    var = w_p^2 s_p^2 + w_m^2 s_m^2 + 2 w_p w_m rho s_p s_m

With ``rho = 0`` (the default) this is plain inverse-variance weighting, w_i = (1/s_i^2) / sum(1/s^2).
The two cores share their inputs, so their errors are positively correlated in practice;
:func:`estimate_rho` measures it from paired leave-one-station-out residuals and the result can
be set as ``fusion.rho`` in ``config/thresholds.yaml``. A larger rho gives a wider (more honest)
fused interval.

The logger is then scored against the fused value. An orchard *should* differ from grass-sited
CIMIS stations (that difference is the project's whole point), so a trailing, lagged median of
the logger's own residual (night and day separately) is removed first; only departures from the
orchard's usual difference are flagged. The offset itself is reported: it is the microclimate +
shield + sensor difference.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .. import config
from ..qc.model import predict_location_frame
from ..timeutil import utc
from .contract import Issue, Node


def fusion_thresholds() -> dict[str, Any]:
    return dict(config.thresholds()["fusion"])


# --------------------------------------------------------------------------- combination
def fuse(mu_p: np.ndarray, sd_p: np.ndarray, mu_m: np.ndarray, sd_m: np.ndarray,
         rho: float = 0.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Minimum-variance combination of two estimates. Returns (mean, sd, physics weight).

    Hours where only one core has a value use that core alone (weight 1 or 0).
    """
    mu_p, sd_p, mu_m, sd_m = (np.asarray(x, dtype="float64") for x in (mu_p, sd_p, mu_m, sd_m))
    has_p = np.isfinite(mu_p) & np.isfinite(sd_p) & (sd_p > 0)
    has_m = np.isfinite(mu_m) & np.isfinite(sd_m) & (sd_m > 0)
    both = has_p & has_m
    with np.errstate(invalid="ignore", divide="ignore"):
        denom = sd_p ** 2 + sd_m ** 2 - 2 * rho * sd_p * sd_m
        w_p = np.where(denom > 0, (sd_m ** 2 - rho * sd_p * sd_m) / denom, 0.5)
    w_p = np.clip(w_p, 0.0, 1.0)
    w_p = np.where(both, w_p, np.where(has_p, 1.0, 0.0))
    w_m = 1.0 - w_p
    p = np.where(has_p, mu_p, 0.0)
    m = np.where(has_m, mu_m, 0.0)
    sp = np.where(has_p, sd_p, 0.0)
    sm = np.where(has_m, sd_m, 0.0)
    mean = w_p * p + w_m * m
    var = w_p ** 2 * sp ** 2 + w_m ** 2 * sm ** 2 + 2 * w_p * w_m * rho * sp * sm
    none = ~(has_p | has_m)
    mean = np.where(none, np.nan, mean)
    sd = np.where(none, np.nan, np.sqrt(np.maximum(var, 0.0)))
    return mean, sd, np.where(none, np.nan, w_p)


def estimate_rho(resid_physics: np.ndarray, resid_ml: np.ndarray) -> float:
    """Correlation of the two cores' errors from paired held-out residuals (robust: Spearman)."""
    a, b = np.asarray(resid_physics, float), np.asarray(resid_ml, float)
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 30:
        raise ValueError("need at least 30 paired residuals to estimate rho")
    ra = pd.Series(a[ok]).rank().to_numpy()
    rb = pd.Series(b[ok]).rank().to_numpy()
    return float(np.corrcoef(ra, rb)[0, 1])


# --------------------------------------------------------------------------- expectations
@dataclass
class Expectation:
    frame: pd.DataFrame          # ts, physics_f/sd, ml_f/sd, expected_f/sd, w_physics, night, ...
    ml_available: bool
    model_version: str


def reference_expectation(ctx: Any, node: Node, start: Any, end: Any,
                          th: dict[str, Any] | None = None) -> Expectation:
    """Physics and ML expected temperature at the node, fused. ``ctx`` is a ChillWatch Context
    whose store holds the reference stations (the logger itself is not in it)."""
    th = th or fusion_thresholds()
    fld = ctx.field(utc(start), utc(end))
    bundle = ctx.model
    phys = predict_location_frame(fld, bundle, node.lat, node.lon, node.elevation_ft, use_model=False)
    ml_ok = bundle.booster is not None
    if ml_ok:
        ml = predict_location_frame(fld, bundle, node.lat, node.lon, node.elevation_ft, use_model=True)
        is_model = (ml["method"] == "model").to_numpy()
        ml_f = np.where(is_model, ml["expected_f"], np.nan)
        ml_sd = np.where(is_model, ml["est_sd"], np.nan)
    else:
        ml_f = ml_sd = np.full(len(phys), np.nan)
    floor = float(th["min_sd_f"])
    phys_sd = np.maximum(phys["est_sd"].to_numpy(dtype="float64"), floor)
    ml_sd = np.maximum(np.asarray(ml_sd, dtype="float64"), floor)
    phys_f = phys["expected_f"].to_numpy(dtype="float64")
    mean, sd, w_p = fuse(phys_f, phys_sd, ml_f, ml_sd, float(th["rho"]))
    with np.errstate(invalid="ignore", divide="ignore"):
        dis = np.abs(phys_f - ml_f) / np.sqrt(phys_sd ** 2 + ml_sd ** 2 - 2 * float(th["rho"]) * phys_sd * ml_sd)
    frame = pd.DataFrame({
        "ts": pd.DatetimeIndex(phys["ts"]),
        "physics_f": phys_f, "physics_sd": np.where(np.isfinite(phys_f), phys_sd, np.nan),
        "ml_f": ml_f, "ml_sd": np.where(np.isfinite(ml_f), ml_sd, np.nan),
        "expected_f": mean, "expected_sd": sd, "w_physics": w_p,
        "cores_disagree": np.isfinite(dis) & (dis > float(th["disagree_z"])),
        "n_neighbors": phys["n_neighbors"].to_numpy(), "night": phys["night"].to_numpy(dtype=bool),
    })
    return Expectation(frame=frame, ml_available=ml_ok, model_version=bundle.version)


# --------------------------------------------------------------------------- scoring
def _trailing_offset(resid: np.ndarray, usable: np.ndarray, night: np.ndarray, window: int, lag: int,
                     min_hours: int) -> np.ndarray:
    """Median residual over the trailing window ending ``lag`` hours back, night and day apart."""
    out = np.full(len(resid), np.nan)
    for i in range(len(resid)):
        lo, hi = max(0, i - lag - window), max(0, i - lag)
        sel = np.arange(lo, hi)
        sel = sel[usable[sel] & (night[sel] == night[i])]
        if len(sel) >= min_hours:
            out[i] = float(np.median(resid[sel]))
    return out


def score(hourly: pd.DataFrame, exp: Expectation, node: Node,
          th: dict[str, Any] | None = None) -> pd.DataFrame:
    """Join the logger's hourly rows with the fused expectation and flag departures.

    Adds: expected_f/sd, physics/ml columns, offset_f (the orchard's learned difference),
    residual_f, z, used_temp_f, is_estimated, est_sd. Hours flagged here move from ok to suspect.
    """
    th = th or fusion_thresholds()
    e = exp.frame.set_index("ts")
    out = hourly.set_index("ts").join(e, how="left").reset_index()
    obs = out["temp_f"].to_numpy(dtype="float64")
    mu = out["expected_f"].to_numpy(dtype="float64")
    sd = out["expected_sd"].to_numpy(dtype="float64")
    night = out["night"].fillna(False).to_numpy(dtype=bool)
    status = out["qc_source"].to_numpy(dtype=object).copy()
    resid = obs - mu
    usable = (status == "ok") & np.isfinite(resid) & ~out["cores_disagree"].fillna(False).to_numpy(dtype=bool)
    offset = _trailing_offset(resid, usable, night, int(th["offset_window_hours"]), int(th["offset_lag_hours"]),
                              int(th["offset_min_hours"]))
    # Before a trailing window exists (the first days of a record), use the first learned offset of
    # the same phase: reports are batch runs, so a large real orchard offset is not flagged at start.
    for phase in (True, False):
        m = night == phase
        learned = offset[m & np.isfinite(offset)]
        if len(learned):
            offset = np.where(m & ~np.isfinite(offset), learned[0], offset)
    offset = np.where(np.isfinite(offset), offset, 0.0)
    total_sd = np.sqrt(sd ** 2 + node.sensor_sd_f ** 2)
    z = (resid - offset) / total_sd

    issues = [set(filter(None, str(s).split(","))) for s in out["issues"].fillna("")]
    disagree = out["cores_disagree"].fillna(False).to_numpy(dtype=bool)
    no_ref = ~np.isfinite(mu)
    judged = (status == "ok") & np.isfinite(z) & ~disagree
    extreme = judged & (np.abs(z) >= float(th["z_extreme"]))
    moderate = judged & (np.abs(z) >= float(th["z_moderate"]))
    persistent = np.zeros(len(z), dtype=bool)
    run = 0
    need = int(th["persist_hours"])
    for i in range(len(z)):
        if status[i] in ("missing", "invalid") or not judged[i]:
            run = 0  # a gap or an unjudged hour resets persistence (Terra's rule)
            continue
        run = run + 1 if moderate[i] else 0
        if run >= need:
            persistent[i - need + 1:i + 1] = True
    for i in range(len(z)):
        if extreme[i]:
            issues[i].add(str(Issue.RESIDUAL_EXTREME))
        if persistent[i]:
            issues[i].add(str(Issue.RESIDUAL_PERSISTENT))
        if disagree[i] and status[i] == "ok":
            issues[i].add(str(Issue.REFERENCE_DISAGREES))
        if no_ref[i] and status[i] == "ok":
            issues[i].add(str(Issue.NO_REFERENCE))
    flagged = extreme | persistent
    status = np.where(flagged & (status == "ok"), "suspect", status)

    est = mu + offset
    est_sd = sd  # the offset is a median over many hours; its own error is small next to sd
    observed = status == "ok"
    out["qc_source"] = status
    out["issues"] = [",".join(sorted(s)) for s in issues]
    out["offset_f"] = offset
    out["residual_f"] = resid
    out["z"] = z
    out["used_temp_f"] = np.where(observed, obs, est)
    out["is_estimated"] = ~observed & np.isfinite(est)
    out["est_sd"] = np.where(observed, node.sensor_sd_f, est_sd)
    return out


def calibration_summary(scored: pd.DataFrame) -> dict[str, Any]:
    """Logger vs the fused reference over ok hours: bias, spread and how much each core counted."""
    ok = scored[(scored["qc_source"] == "ok") & np.isfinite(scored["residual_f"])]
    if ok.empty:
        return {"hours": 0}
    r = ok["residual_f"].to_numpy()
    med = float(np.median(r))
    return {
        "hours": int(len(ok)),
        "median_difference_f": round(med, 2),
        "median_difference_c": round(med * 5 / 9, 2),
        "robust_sd_f": round(float(1.4826 * np.median(np.abs(r - med))), 2),
        "mean_expected_sd_f": round(float(np.nanmean(ok["expected_sd"])), 2),
        "mean_physics_weight": round(float(np.nanmean(ok["w_physics"])), 3),
        "night_offset_f": round(float(np.nanmedian(r[ok["night"].fillna(False).to_numpy(dtype=bool)])), 2)
        if ok["night"].fillna(False).any() else None,
        "day_offset_f": round(float(np.nanmedian(r[~ok["night"].fillna(False).to_numpy(dtype=bool)])), 2)
        if (~ok["night"].fillna(False).astype(bool)).any() else None,
    }
