"""Season chill summary from a scored logger series, with units, period and missing data shown.

The input is the hourly frame from :func:`chillwatch.interop.fusion.score` (or any frame with
``ts``, ``temp_f``, ``qc_source``, ``used_temp_f``, ``is_estimated``, ``est_sd``). Three totals are
reported for each model so nobody mistakes an estimate for a measurement:

* ``observed_only`` - validated (``ok``) logger hours only. For the Dynamic Model the state is
  held across gaps, so this undercounts when data is missing.
* ``with_estimates`` - gaps and rejected hours filled with the fused physics/ML estimate.
* ``p10`` / ``p90`` - spread of ``with_estimates`` over random draws of the estimated hours from
  their uncertainty. Errors within one contiguous gap are drawn fully correlated (an interpolation
  error is mostly a persistent offset), which is the conservative choice.

``complete`` follows the existing farm-chill rule: conclusions that need complete data require at
least 95 % of the period's hours to be validated observations.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from .. import config
from ..interop.contract import f_to_c
from ..interop.hourly import gaps as gap_list
from ..normalize import local_date_of_hour
from .models import CHILL_HOURS_HI_C, CHILL_HOURS_LO_C, chill_hours, dynamic_model

UNITS = {
    "chill_hours": f"hours with air temperature {CHILL_HOURS_LO_C:g}-{CHILL_HOURS_HI_C:g} C (32-45 F)",
    "chill_portions": "Chill Portions (Dynamic Model, Erez et al. 1990)",
    "temperature": "degrees F (device reports degrees C; converted once at ingest)",
}


def chill_settings() -> dict[str, Any]:
    t = config.thresholds()
    return {**t["chill"], **t["chill_summary"]}


def season_window(season: str, th: dict[str, Any] | None = None) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Hour-ending UTC bounds for a season label like ``2026-27`` (PST dates, inclusive)."""
    th = th or chill_settings()
    y0 = int(season[:4])
    sm, sd = (int(x) for x in str(th["start_mmdd"]).split("-"))
    em, ed = (int(x) for x in str(th["end_mmdd"]).split("-"))
    first = date(y0, sm, sd)
    last = date(y0 + (1 if (em, ed) < (sm, sd) else 0), em, ed)
    start = pd.Timestamp(first).tz_localize("UTC") + pd.Timedelta(hours=1 + 8)
    end = pd.Timestamp(last).tz_localize("UTC") + pd.Timedelta(hours=24 + 8)
    return start, end


def _draw_estimates(used_c: np.ndarray, est: np.ndarray, sd_c: np.ndarray, n: int, seed: int) -> np.ndarray:
    """(n, H) temperature traces: observed hours fixed, each estimated run shifted by one shared draw."""
    rng = np.random.default_rng(seed)
    traces = np.repeat(used_c[None, :], n, axis=0)
    i, h = 0, len(est)
    while i < h:
        if not est[i]:
            i += 1
            continue
        j = i
        while j + 1 < h and est[j + 1]:
            j += 1
        z = rng.standard_normal(n)[:, None]
        traces[:, i:j + 1] += z * sd_c[None, i:j + 1]
        i = j + 1
    return traces


def summarize(scored: pd.DataFrame, start: Any, end: Any, as_of: Any = None,
              target_portions: float | None = None, target_hours: float | None = None,
              th: dict[str, Any] | None = None) -> dict[str, Any]:
    th = th or chill_settings()
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    if as_of is not None:
        e = min(e, pd.Timestamp(as_of).floor("h"))
    grid = pd.date_range(s, e, freq="h")
    df = scored.set_index("ts").reindex(grid)
    df.index.name = "ts"
    df["qc_source"] = df["qc_source"].fillna("missing")
    df["is_estimated"] = df["is_estimated"].fillna(False).astype(bool)
    n = len(df)
    ok = (df["qc_source"] == "ok").to_numpy()
    est = df["is_estimated"].to_numpy() & ~ok
    obs_c = np.where(ok, f_to_c(df["temp_f"].to_numpy(dtype="float64")), np.nan)
    used_c = f_to_c(df["used_temp_f"].to_numpy(dtype="float64"))
    used_c = np.where(ok | est, used_c, np.nan)
    sd_c = df["est_sd"].to_numpy(dtype="float64") * 5.0 / 9.0
    sd_c = np.where(np.isfinite(sd_c), sd_c, 0.0)

    ch_obs = float(np.nansum(chill_hours(obs_c)))
    ch_all = float(np.nansum(chill_hours(used_c)))
    cp_obs = float(np.nansum(dynamic_model(obs_c)[0]))
    cp_all = float(np.nansum(dynamic_model(used_c)[0]))

    draws = int(th["estimate_draws"])
    if est.any() and draws > 0:
        traces = _draw_estimates(np.nan_to_num(used_c, nan=np.nan), est, sd_c, draws, int(th["seed"]))
        ch_t = np.array([np.nansum(chill_hours(t)) for t in traces])
        cp_t = np.array([np.nansum(dynamic_model(t)[0]) for t in traces])
        ch_q = (float(np.quantile(ch_t, 0.1)), float(np.quantile(ch_t, 0.9)))
        cp_q = (float(np.quantile(cp_t, 0.1)), float(np.quantile(cp_t, 0.9)))
    else:
        ch_q, cp_q = (ch_all, ch_all), (cp_all, cp_all)

    missing = int((~ok & ~est).sum())
    cov_obs = ok.sum() / n if n else 0.0
    complete = cov_obs >= float(th["complete_coverage"])
    dates = local_date_of_hour(pd.Series(grid))
    out: dict[str, Any] = {
        "units": UNITS,
        "period": {"start": s.isoformat(), "end": e.isoformat(),
                   "first_local_date": str(dates.iloc[0]) if n else None,
                   "last_local_date": str(dates.iloc[-1]) if n else None,
                   "timestamps": "UTC hour-ending; local dates are PST (UTC-8) like CIMIS"},
        "hours": {"expected": n, "observed_ok": int(ok.sum()), "estimated": int(est.sum()), "unfilled": missing,
                  "coverage_observed": round(float(cov_obs), 4),
                  "coverage_with_estimates": round(float((ok | est).sum() / n) if n else 0.0, 4)},
        "complete": bool(complete),
        "chill_hours": {"observed_only": round(ch_obs, 1), "with_estimates": round(ch_all, 1),
                        "estimated_part": round(ch_all - ch_obs, 1),
                        "p10": round(ch_q[0], 1), "p90": round(ch_q[1], 1)},
        "chill_portions": {"observed_only": round(cp_obs, 2), "with_estimates": round(cp_all, 2),
                           "p10": round(cp_q[0], 2), "p90": round(cp_q[1], 2)},
        "gaps": gap_list(df.reset_index()[["ts", "qc_source"]].assign(ts=lambda d: pd.to_datetime(d["ts"], utc=True)),
                         statuses=("missing", "invalid", "suspect")),
    }
    for key, target, value, lo in (("chill_portions", target_portions, cp_all, cp_q[0]),
                                   ("chill_hours", target_hours, ch_all, ch_q[0])):
        if target:
            out[key]["target"] = target
            out[key]["fraction_of_target"] = round(value / target, 3)
            # "reached" only when even the low end of the estimate range clears the target
            out[key]["target_reached"] = bool(lo >= target)
    return out
