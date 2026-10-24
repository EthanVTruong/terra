"""Feature builder for the neighbor model. Never reads the target's own value."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .. import config
from ..normalize import to_pst
from .neighbors import Field, adjusted_neighbor_temps, idw_baseline

EXTRA_FEATURES = {"rh_pct": "nbr_rh", "dewpoint_f": "nbr_dew", "solar_wm2": "nbr_solar", "wind_mph": "nbr_wind"}


def feature_names(k: int | None = None, extras: tuple[str, ...] = (), prism: bool = False) -> list[str]:
    k = k or int(config.thresholds()["neighbors"]["k"])
    names = ["t_base", "gamma_h"]
    for j in range(1, k + 1):
        names += [f"n{j}_adj", f"n{j}_dist", f"n{j}_dz"]
    names += ["nbr_median", "nbr_std", "n_valid", "hod_sin", "hod_cos", "doy_sin", "doy_cos"]
    names += ["elevation_ft", "lat", "lon"]
    names += [EXTRA_FEATURES[e] for e in extras if e in EXTRA_FEATURES]
    if prism:
        names += ["prism_tmin", "prism_tmax"]
    return names


def is_night(hours: pd.DatetimeIndex) -> np.ndarray:
    """Night = 18:00-06:00 local (PST): hour-ending 19..23 and 0..6."""
    h = to_pst(hours).hour.to_numpy()
    return (h >= 19) | (h <= 6)


def time_features(hours: pd.DatetimeIndex) -> dict[str, np.ndarray]:
    local = to_pst(hours)
    hod = local.hour.to_numpy() + local.minute.to_numpy() / 60.0
    doy = local.day_of_year.to_numpy()
    return {
        "hod_sin": np.sin(2 * np.pi * hod / 24.0),
        "hod_cos": np.cos(2 * np.pi * hod / 24.0),
        "doy_sin": np.sin(2 * np.pi * doy / 365.25),
        "doy_cos": np.cos(2 * np.pi * doy / 365.25),
    }


def location_features(
    fld: Field,
    lat: float,
    lon: float,
    elevation_ft: float,
    exclude: set[int] | None = None,
    prism: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
    """Features for one location over ``fld.hours``.

    ``exclude`` must contain the target's own station id when the location is a station,
    so its own reading can never enter the features.
    Returns (X, aux) where aux holds n_valid, nearest-valid distance/dz and T_base.
    """
    idx, dist, dz = fld.schedule(lat, lon, elevation_ft, exclude=exclude)
    if exclude:
        own = [fld.pos[s] for s in exclude if s in fld.pos]
        assert not np.isin(idx, own).any(), "target's own column leaked into neighbor features"
    gamma = fld.gamma_excluding(exclude)
    adj = adjusted_neighbor_temps(fld.W, idx, dz, gamma)
    tb, n = idw_baseline(adj, dist)
    k = idx.shape[1]
    H = len(fld.hours)
    cols: dict[str, Any] = {"t_base": tb, "gamma_h": gamma}
    for j in range(k):
        valid = np.isfinite(adj[:, j])
        cols[f"n{j + 1}_adj"] = adj[:, j]
        cols[f"n{j + 1}_dist"] = np.where(valid, dist[:, j], np.nan)
        cols[f"n{j + 1}_dz"] = np.where(valid, dz[:, j], np.nan)
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        cols["nbr_median"] = np.nanmedian(adj, axis=1)
        cols["nbr_std"] = np.nanstd(adj, axis=1)
    cols["n_valid"] = n.astype("float64")
    cols.update(time_features(fld.hours))
    cols["elevation_ft"] = np.full(H, float(elevation_ft))
    cols["lat"] = np.full(H, float(lat))
    cols["lon"] = np.full(H, float(lon))
    rows = np.arange(H)[:, None]
    for src, name in EXTRA_FEATURES.items():
        if src in fld.extras:
            vals = np.where(idx >= 0, fld.extras[src][rows, np.clip(idx, 0, None)], np.nan)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                cols[name] = np.nanmean(vals, axis=1)
    if prism is not None and len(prism):
        day = pd.Series((to_pst(fld.hours) - pd.Timedelta(hours=1)).normalize())
        p = prism.set_index(pd.to_datetime(prism["date_local"]))
        cols["prism_tmin"] = day.map(p["tmin_f"]).to_numpy(dtype="float64")
        cols["prism_tmax"] = day.map(p["tmax_f"]).to_numpy(dtype="float64")
    X = pd.DataFrame(cols, index=fld.hours)
    # fitted physics for places without a station (config baseline.unseen_*); the model's own
    # t_base feature above stays the spec's estimate so features match training
    b = config.thresholds()["baseline"]
    shrink, scale = float(b.get("unseen_lapse_shrink", 1.0)), b.get("unseen_dz_scale_ft")
    if shrink != 1.0 or scale:
        tp, _ = idw_baseline(adjusted_neighbor_temps(fld.W, idx, dz, gamma, shrink), dist, dz_ft=dz,
                             dz_scale_ft=scale)
    else:
        tp = tb
    # nearest *valid* neighbor distance / |dz| per hour, for uncertainty bins
    valid = np.isfinite(adj)
    first = np.where(valid.any(axis=1), valid.argmax(axis=1), -1)
    nd = np.where(first >= 0, dist[np.arange(H), np.clip(first, 0, None)], np.nan)
    ndz = np.where(first >= 0, np.abs(dz[np.arange(H), np.clip(first, 0, None)]), np.nan)
    aux = {"n_valid": n, "nearest_km": nd, "nearest_dz_ft": ndz, "t_base": tb, "nbr_median": cols["nbr_median"],
           "t_phys": tp}
    return X, aux


def station_features(
    fld: Field, station_id: int, prism: pd.DataFrame | None = None
) -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
    row = fld.station_row(station_id)
    return location_features(
        fld, float(row.lat), float(row.lon), float(row.elevation_ft), exclude={int(station_id)}, prism=prism
    )
