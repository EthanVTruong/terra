"""Virtual stations and blind tests (predict a location purely from its neighbors)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from .. import config
from ..qc.model import ModelBundle, chill_hours, chill_mask, predict_location_frame, train
from ..qc.neighbors import build_field
from ..timeutil import season_bounds, seasons_between

log = logging.getLogger(__name__)


def virtual_station(lat: float, lon: float, elevation_ft: float, start: datetime, end: datetime,
                    ctx: Any | None = None) -> pd.DataFrame:
    """Hourly estimates at a point with no station: always ``is_estimated``."""
    from ..qc.model import predict_at_location

    df = predict_at_location(lat, lon, elevation_ft, start, end, ctx=ctx)
    df["used_temp_f"] = df["expected_f"]
    df["is_estimated"] = True
    df["est_method"] = df["method"]
    return df


def blind_model(station_id: int, ctx: Any, retrain: bool = True) -> ModelBundle:
    """A model trained with the station removed entirely (cached under data/models/blind)."""
    if not retrain:
        return ctx.model
    base = ctx.model.version
    out = config.data_dir() / "models" / "blind"
    path = out / f"blind-{station_id}-{base}"
    if (path / "features.json").exists():
        return ModelBundle.load(path)
    m = config.thresholds()["model"]
    log.info("training blind model without station %s", station_id)
    return train(ctx.store, version=path.name, out_dir=out, loso_folds=1, drop_ids={int(station_id)},
                 max_rows=int(m["blind_max_train_rows"]), activate=False)


def blind_test(station_id: int, season: str, ctx: Any | None = None, retrain: bool = True,
               model: ModelBundle | None = None) -> dict[str, Any]:
    """Hide a station entirely and predict it from its neighbors.

    Returns ``{series: [ts, actual, predicted, p10, p90], mae_f, chill_hours_actual,
    chill_hours_predicted, ...}``. Chill hours count 32-45 F from Nov 1 over hours where the
    station actually reported (so both totals cover the same hours).
    """
    from ..context import get_context

    ctx = ctx or get_context()
    reg = config.region()
    start, end = season_bounds(season, reg["seasons"]["start_mmdd"], reg["seasons"]["end_mmdd"])
    st = ctx.region_stations()
    row = st[st["station_id"] == int(station_id)]
    if row.empty:
        raise KeyError(f"station {station_id} not in region")
    row = row.iloc[0]
    bundle = model or blind_model(int(station_id), ctx, retrain)
    rd = ctx.store.readings(start, end, st["station_id"].tolist())
    fld = build_field(rd, st, start, end, valid_col="qc_status", drop_ids={int(station_id)})
    pred = predict_location_frame(fld, bundle, float(row.lat), float(row.lon), float(row.elevation_ft))
    own = rd[rd["station_id"] == int(station_id)].set_index("ts")
    actual = own["temp_f"].where(own["qc_status"] == "ok").reindex(pred["ts"]).to_numpy(dtype="float64")
    series = pd.DataFrame({"ts": pred["ts"], "actual": actual, "predicted": pred["expected_f"],
                           "p10": pred["p10"], "p90": pred["p90"], "method": pred["method"]})
    both = np.isfinite(series["actual"]) & np.isfinite(series["predicted"])
    cm = chill_mask(pd.DatetimeIndex(series["ts"])) & np.isfinite(series["actual"].to_numpy())
    inside = both & (series["actual"] >= series["p10"]) & (series["actual"] <= series["p90"])
    return {
        "station_id": int(station_id),
        "season": season,
        "model": bundle.version,
        "series": series,
        "n_hours": int(both.sum()),
        "mae_f": float(np.mean(np.abs(series["actual"][both] - series["predicted"][both]))) if both.any() else None,
        "bias_f": float(np.mean(series["predicted"][both] - series["actual"][both])) if both.any() else None,
        "p10_p90_coverage": float(inside.sum() / both.sum()) if both.any() else None,
        "chill_hours_actual": chill_hours(series["actual"][cm].to_numpy()),
        "chill_hours_predicted": chill_hours(series["predicted"][cm].to_numpy()),
        "chill_hours_compared": int(cm.sum()),
    }


def durham_rescue(ctx: Any | None = None, station_id: int = 12, min_coverage: float = 0.8,
                  retrain: bool = True) -> dict[str, Any]:
    """Validate the "rescued station": LOSO on Durham's pre-closure seasons, chill-hour error."""
    from ..context import get_context

    ctx = ctx or get_context()
    reg = config.region()
    seasons = seasons_between(reg["seasons"]["first"], reg["seasons"]["last"])
    bundle = blind_model(station_id, ctx, retrain)
    out = []
    for s in seasons:
        a, b = season_bounds(s, reg["seasons"]["start_mmdd"], reg["seasons"]["end_mmdd"])
        r = ctx.store.readings(a, b, [station_id])
        hours = (b - a) / pd.Timedelta(hours=1) + 1
        if r.empty or r["temp_f"].notna().sum() < min_coverage * hours:
            continue
        res = blind_test(station_id, s, ctx, model=bundle)
        res.pop("series")
        res["chill_hours_error"] = res["chill_hours_predicted"] - res["chill_hours_actual"]
        out.append(res)
    errs = np.array([abs(x["chill_hours_error"]) for x in out]) if out else np.array([])
    return {
        "station_id": station_id,
        "seasons": out,
        "mean_abs_chill_hours_error": float(errs.mean()) if len(errs) else None,
        "median_abs_chill_hours_error": float(np.median(errs)) if len(errs) else None,
        "mean_mae_f": float(np.mean([x["mae_f"] for x in out])) if out else None,
    }
