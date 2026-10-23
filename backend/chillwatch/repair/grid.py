"""Chill-hour map: virtual stations on a regular grid.

Each cell is an estimate from the neighbor model at the cell centre (ground elevation from
terrain tiles). Cells the model cannot support honestly are kept but marked ``valid = False``
with a reason: too few stations within the neighbor radius, missing estimates, a large
elevation gap to the nearest station, or water.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .. import config
from ..qc.model import chill_mask, predict_location_frame
from ..qc.neighbors import build_field, haversine_km
from ..timeutil import season_bounds, utc

log = logging.getLogger(__name__)
KM_PER_DEG_LAT = 111.195


def grid_cells(stations: pd.DataFrame, km: float, max_km: float, min_stations: int) -> pd.DataFrame:
    """Cell centres within ``max_km`` of at least ``min_stations`` of the given stations."""
    lat0 = float(stations["lat"].mean())
    dlat = km / KM_PER_DEG_LAT
    dlon = km / (KM_PER_DEG_LAT * np.cos(np.radians(lat0)))
    pad_lat, pad_lon = max_km / KM_PER_DEG_LAT, max_km / (KM_PER_DEG_LAT * np.cos(np.radians(lat0)))
    lats = np.arange(stations["lat"].min() - pad_lat, stations["lat"].max() + pad_lat + dlat, dlat)
    lons = np.arange(stations["lon"].min() - pad_lon, stations["lon"].max() + pad_lon + dlon, dlon)
    LA, LO = np.meshgrid(lats, lons, indexing="ij")
    la, lo = LA.ravel(), LO.ravel()
    d = haversine_km(la[:, None], lo[:, None], stations["lat"].to_numpy()[None, :], stations["lon"].to_numpy()[None, :])
    n_near = (d <= max_km).sum(axis=1)
    keep = n_near >= min_stations
    return pd.DataFrame({"lat": la[keep], "lon": lo[keep], "n_stations_within": n_near[keep],
                         "nearest_station_km": d[keep].min(axis=1), "dlat": dlat, "dlon": dlon})


def _chill_counts(temps: np.ndarray) -> np.ndarray:
    c = config.thresholds()["chill"]
    return ((temps >= c["min_f"]) & (temps <= c["max_f"])).sum(axis=0)


def predict_grid(ctx: Any, season: str, km: float = 5.0, end: Any | None = None, terrain: Any | None = None,
                 n_boot: int = 30, seed: int = 0, elevation_fn: Any | None = None) -> pd.DataFrame:
    """Season-to-date chill hours (32-45 F from Nov 1) per grid cell, with a 10-90% range.

    The range is the spread of chill totals over ``n_boot`` traces of expected temperature plus
    resampled leave-stations-out residual days (scaled to each hour's sd), centred on the point
    estimate; ``noise_shift_hours`` is how far the traces' median sits from it.
    """
    g = config.thresholds()["grid"]
    reg = config.region()
    start, s_end = season_bounds(season, reg["seasons"]["start_mmdd"], reg["seasons"]["end_mmdd"])
    end_ts = min(utc(end), s_end) if end is not None else s_end
    st = ctx.region_stations()
    rd = ctx.store.readings(start, end_ts, st["station_id"].tolist())
    last = rd.loc[rd["temp_f"].notna(), "ts"].max() if len(rd) else pd.NaT
    if pd.isna(last):
        raise RuntimeError(f"no station data for {season}")
    end_ts = min(end_ts, utc(last))  # season to date: stop at the last hour with data
    fld = build_field(rd, st, start, end_ts, valid_col="qc_status")
    fin = np.isfinite(fld.W)
    live = fin.any(axis=1)  # season-to-date: judge coverage over hours that have any data yet
    has_data = fin[live].mean(axis=0) > 0.5 if live.any() else np.zeros(fin.shape[1], dtype=bool)
    reporting = fld.stations[has_data]
    if reporting.empty:
        raise RuntimeError(f"no station data for {season}")
    max_km = float(config.thresholds()["neighbors"]["max_km"])
    cells = grid_cells(reporting, km, max_km, int(g["min_stations"]))
    if elevation_fn is None:
        from ..sources.terrain import TerrainTiles

        terrain = terrain or TerrainTiles()
        elevation_fn = terrain.elevation_ft
    cells["elevation_ft"] = elevation_fn(cells["lat"].to_numpy(), cells["lon"].to_numpy())
    log.info("%d grid cells at %.1f km", len(cells), km)

    bundle = ctx.model
    pool = bundle.oof_blocks()
    flat = pool[np.isfinite(pool)]
    pool_sd = float(1.4826 * np.median(np.abs(flat - np.median(flat)))) if flat.size else 1.0
    pool_sd = pool_sd if pool_sd > 0 else 1.0
    hours = fld.hours
    cm = chill_mask(hours)
    local = hours.tz_convert("UTC").tz_localize(None) - pd.Timedelta(hours=9)
    local_hour = local.hour.to_numpy()
    day_idx = np.unique(local.normalize(), return_inverse=True)[1]
    n_days = int(day_idx.max()) + 1 if len(day_idx) else 0

    rows = []
    for i, c in enumerate(cells.itertuples(index=False)):
        if not np.isfinite(c.elevation_ft) or c.elevation_ft < float(g["water_below_ft"]):
            rows.append({"chill_hours": np.nan, "chill_p10": np.nan, "chill_p90": np.nan, "noise_shift_hours": np.nan,
                         "coverage": 0.0,
                         "model_share": 0.0, "nearest_dz_ft": np.nan, "mean_temp_f": np.nan, "est_sd_f": np.nan,
                         "reason": "water"})
            continue
        df = predict_location_frame(fld, bundle, float(c.lat), float(c.lon), float(c.elevation_ft))
        e = df["expected_f"].to_numpy(dtype="float64")
        ok = np.isfinite(e)
        cov = float(ok[cm].mean()) if cm.any() else 0.0
        sd = df["est_sd"].to_numpy(dtype="float64")
        ch = float(_chill_counts(e[cm & ok][:, None])[0]) if cm.any() else np.nan
        # bootstrap: whole-day blocks of leave-stations-out residuals, rescaled to each hour's sd
        rng = np.random.default_rng(seed + i)
        picks = rng.integers(0, len(pool), size=(n_days, n_boot))
        sel = cm & ok
        res = pool[picks[day_idx[sel]], local_hour[sel][:, None]] * (sd[sel] / pool_sd)[:, None]
        boot = _chill_counts(e[sel][:, None] + res)
        dz = np.nanmedian(np.abs(df["nearest_dz_ft"].to_numpy(dtype="float64")))
        reason = ""
        if cov < float(g["min_coverage"]):
            reason = "too few estimated hours"
        elif np.isfinite(dz) and dz > float(g["max_dz_ft"]):
            reason = "far above/below the nearest station"
        # adding hour-level noise moves hours across the 32/45 F edges, which shifts the bootstrap
        # totals as a whole; use the bootstrap for the spread around the point estimate and report
        # the shift separately (noise_shift_hours)
        med = float(np.median(boot))
        rows.append({
            "chill_hours": ch, "chill_p10": ch + float(np.quantile(boot, 0.1)) - med,
            "chill_p90": ch + float(np.quantile(boot, 0.9)) - med, "noise_shift_hours": med - ch,
            "coverage": cov, "model_share": float((df["method"] == "model").mean()), "nearest_dz_ft": float(dz),
            "mean_temp_f": float(np.nanmean(e)) if ok.any() else np.nan,
            "est_sd_f": float(np.nanmedian(sd)) if np.isfinite(sd).any() else np.nan, "reason": reason,
        })
        if (i + 1) % 250 == 0:
            log.info("grid: %d/%d cells", i + 1, len(cells))
    out = pd.concat([cells.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    out["valid"] = out["reason"].eq("") & out["chill_hours"].notna()
    out.attrs.update({"season": season, "end": end_ts.isoformat(), "km": km, "model": bundle.version,
                      "chill_from": config.thresholds()["chill"]["start_mmdd"]})
    return out


# ============================================================================ outputs
def _outline() -> list[list[list[float]]]:
    p = config.config_dir() / "ca_outline.json"
    return json.loads(p.read_text())["rings"] if p.exists() else []


def to_geojson(df: pd.DataFrame) -> dict[str, Any]:
    feats = []
    for r in df.itertuples(index=False):
        hy, hx = r.dlat / 2, r.dlon / 2
        ring = [[r.lon - hx, r.lat - hy], [r.lon + hx, r.lat - hy], [r.lon + hx, r.lat + hy],
                [r.lon - hx, r.lat + hy], [r.lon - hx, r.lat - hy]]
        props = {k: (None if isinstance(v, float) and not np.isfinite(v) else v)
                 for k, v in r._asdict().items() if k not in ("dlat", "dlon")}
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[round(x, 5), round(y, 5)]
                      for x, y in ring]]}, "properties": props})
    return {"type": "FeatureCollection", "properties": dict(df.attrs), "features": feats}


def render_png(df: pd.DataFrame, stations: pd.DataFrame, path: Path, title: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    fig, ax = plt.subplots(figsize=(8, 9), dpi=150)
    v = df[df["valid"]]
    vmin, vmax = (float(v["chill_hours"].quantile(0.02)), float(v["chill_hours"].quantile(0.98))) if len(v) else (0, 1)
    cmap = plt.get_cmap("viridis")
    norm = matplotlib.colors.Normalize(vmin=vmin, vmax=vmax)
    for r in df.itertuples(index=False):
        if r.reason == "water":
            continue
        color = cmap(norm(r.chill_hours)) if r.valid else "#d9d9d9"
        ax.add_patch(Rectangle((r.lon - r.dlon / 2, r.lat - r.dlat / 2), r.dlon, r.dlat, facecolor=color,
                               edgecolor="none"))
    for ring in _outline():
        xs, ys = zip(*ring, strict=True)
        ax.plot(xs, ys, color="#333333", lw=0.8)
    ax.scatter(stations["lon"], stations["lat"], s=8, c="white", edgecolors="black", linewidths=0.5, zorder=5,
               label="weather station")
    sm = matplotlib.cm.ScalarMappable(norm=norm, cmap=cmap)
    fig.colorbar(sm, ax=ax, shrink=0.6, label="chill hours (32-45 °F)")
    ax.set_xlim(df["lon"].min() - 0.2, df["lon"].max() + 0.2)
    ax.set_ylim(df["lat"].min() - 0.2, df["lat"].max() + 0.2)
    ax.set_aspect(1 / np.cos(np.radians(df["lat"].mean())))
    ax.set_title(title, fontsize=10)
    ax.legend(loc="lower left", fontsize=8)
    ax.text(0.99, 0.01, "grey: no estimate (fewer than 2 stations within 80 km, or far above/below them)", transform=ax.transAxes, ha="right",
            fontsize=7, color="#555555")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def write_grid(df: pd.DataFrame, stations: pd.DataFrame, out_dir: Path, label: str = "") -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    a = df.attrs
    stem = f"chill_{a.get('season', '')}_{a.get('km', '')}km"
    csv = out_dir / f"{stem}.csv"
    df.drop(columns=["dlat", "dlon"]).to_csv(csv, index=False)
    gj = out_dir / f"{stem}.geojson"
    gj.write_text(json.dumps(to_geojson(df)))
    png = out_dir / f"{stem}.png"
    end_day = (pd.Timestamp(a["end"]) - pd.Timedelta(hours=9)).strftime("%b %-d, %Y")
    render_png(df, stations, png, f"Chill hours since Nov 1 through {end_day} ({a.get('km')} km grid, model "
               f"{a.get('model')}){' - ' + label if label else ''}")
    meta = out_dir / f"{stem}.json"
    v = df[df["valid"]]
    meta.write_text(json.dumps({**a, "label": label, "generated_at": datetime.now().astimezone().isoformat(),
                                "cells": int(len(df)), "valid_cells": int(len(v)),
                                "masked": df.loc[~df["valid"], "reason"].value_counts().to_dict(),
                                "chill_hours_valid": {"min": float(v["chill_hours"].min()) if len(v) else None,
                                                      "median": float(v["chill_hours"].median()) if len(v) else None,
                                                      "max": float(v["chill_hours"].max()) if len(v) else None}},
                               indent=2, default=str))
    return {"csv": str(csv), "geojson": str(gj), "png": str(png), "meta": str(meta)}
