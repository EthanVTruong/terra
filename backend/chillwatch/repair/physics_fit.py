"""Fit the physics estimate used where no station exists (leave-stations-out).

Two parameters: ``lapse_shrink`` (multiplies the hourly lapse adjustment) and ``dz_scale_ft``
(neighbor weights fall off as exp(-(dz/scale)^2)). Each station is predicted from the others
exactly as an unseen point would be (excluded from its neighbors and from the lapse fit).
"""

from __future__ import annotations

import itertools
import logging
from typing import Any

import numpy as np

from .. import config
from ..qc.model import chill_hours, chill_mask
from ..qc.neighbors import adjusted_neighbor_temps, build_field, idw_baseline
from ..timeutil import season_bounds

log = logging.getLogger(__name__)
SHRINKS = (0.0, 0.25, 0.5, 0.75, 1.0)
SCALES: tuple[float | None, ...] = (None, 300.0, 600.0, 1000.0, 2000.0)


def evaluate(ctx: Any, seasons: list[str], shrinks: tuple[float, ...] = SHRINKS,
             scales: tuple[float | None, ...] = SCALES) -> dict[str, Any]:
    reg = config.region()
    st = ctx.region_stations()
    variants = list(itertools.product(shrinks, scales))
    abs_sum = np.zeros(len(variants))
    count = 0
    chill_err: list[list[float]] = [[] for _ in variants]
    for s in seasons:
        a, b = season_bounds(s, reg["seasons"]["start_mmdd"], reg["seasons"]["end_mmdd"])
        rd = ctx.store.readings(a, b, st["station_id"].tolist())
        if rd.empty:
            continue
        fld = build_field(rd, st, a, b, valid_col="qc_status")
        cm = chill_mask(fld.hours)
        for sid in fld.station_ids:
            sid = int(sid)
            y = fld.W[:, fld.pos[sid]]
            if np.isfinite(y).sum() < 24 * 30:
                continue
            row = fld.station_row(sid)
            idx, dist, dz = fld.schedule(float(row.lat), float(row.lon), float(row.elevation_ft), exclude={sid})
            gamma = fld.gamma_excluding({sid})
            preds = []
            for shrink, scale in variants:
                adj = adjusted_neighbor_temps(fld.W, idx, dz, gamma, shrink)
                tb, _ = idw_baseline(adj, dist, dz_ft=dz, dz_scale_ft=scale)
                preds.append(tb)
            P = np.vstack(preds)
            ok = np.isfinite(y) & np.isfinite(P).all(axis=0)
            if ok.sum() == 0:
                continue
            abs_sum += np.abs(P[:, ok] - y[ok]).sum(axis=1)
            count += int(ok.sum())
            c = cm & ok
            if c.sum() >= 24 * 30:
                truth = chill_hours(y[c])
                for i in range(len(variants)):
                    chill_err[i].append(chill_hours(P[i, c]) - truth)
        log.info("season %s done", s)
    rows: list[dict[str, Any]] = []
    for i, (shrink, scale) in enumerate(variants):
        e = np.abs(np.asarray(chill_err[i])) if chill_err[i] else np.array([np.nan])
        rows.append({"lapse_shrink": shrink, "dz_scale_ft": scale, "mae_f": float(abs_sum[i] / max(count, 1)),
                     "chill_median_abs": float(np.median(e)), "chill_mean_abs": float(np.mean(e))})
    rows.sort(key=lambda r: float(r["mae_f"]))
    return {"seasons": seasons, "hours": count, "variants": rows}


def fit(ctx: Any, fit_seasons: list[str], check_seasons: list[str]) -> dict[str, Any]:
    fitted = evaluate(ctx, fit_seasons)
    best = fitted["variants"][0]
    spec = next(r for r in fitted["variants"] if r["lapse_shrink"] == 1.0 and r["dz_scale_ft"] is None)
    check = evaluate(ctx, check_seasons, shrinks=(1.0, best["lapse_shrink"]), scales=(None, best["dz_scale_ft"]))
    get = lambda res, sh, sc: next(r for r in res["variants"] if r["lapse_shrink"] == sh and r["dz_scale_ft"] == sc)  # noqa: E731
    return {
        "best": {"lapse_shrink": best["lapse_shrink"], "dz_scale_ft": best["dz_scale_ft"]},
        "fit": {"seasons": fit_seasons, "best": best, "spec": spec, "all": fitted["variants"]},
        "check": {"seasons": check_seasons, "best": get(check, best["lapse_shrink"], best["dz_scale_ft"]),
                  "spec": get(check, 1.0, None)},
    }
