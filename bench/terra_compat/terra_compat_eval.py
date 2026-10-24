"""Station-held-out check of ChillWatch estimation on Terra's five-station network.

Evaluation behind docs/TERRA_COMPATIBILITY.md (not part of the package). Uses readings already in the
local ChillWatch DB (same CIMIS source, not ET's hashed dataset artifact). Every target is
excluded from its own neighbors, lapse fit and features. Windows are half-open UTC.
"""

from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from chillwatch.context import get_context
from chillwatch.qc import model as M
from chillwatch.qc.features import is_night
from chillwatch.qc.neighbors import build_field

ET = [6, 226, 139, 212, 235]
T = pd.Timestamp
FIT = (T("2023-09-01T08:00Z"), T("2025-09-01T08:00Z"))  # ET offset-fit window
WINDOWS = {
    "untouched_2026-03": (T("2026-03-01T08:00Z"), T("2026-04-01T08:00Z")),
    "inspected_winter": (T("2025-12-01T08:00Z"), T("2026-03-01T08:00Z")),
}
H1 = pd.Timedelta(hours=1)

ctx = get_context()
st_all = ctx.region_stations()
st_et = st_all[st_all["station_id"].isin(ET)]
bundle = ctx.model


def eligible(r: pd.DataFrame) -> pd.DataFrame:
    """ET eligibility: basic-valid and blank CIMIS source flag (ChillWatch also accepts 'Y')."""
    r = r.copy()
    blank = r["qc_source"].fillna("").str.strip().eq("")
    r["et_ok"] = np.where(r["qc_base"].eq("ok") & blank, "ok", "no")
    return r


def load(a: T, b: T, ids=None) -> pd.DataFrame:
    return eligible(ctx.store.readings(a, b - H1, ids if ids is not None else st_all["station_id"].tolist()))


def pst_month_hour(h: pd.DatetimeIndex):
    p = h.tz_convert("Etc/GMT+8")
    return p.month.to_numpy(), p.hour.to_numpy()


def peer_median(fld, sid):
    cols = [fld.pos[s] for s in ET if s != sid and s in fld.pos]
    v = fld.W[:, cols]
    n = np.isfinite(v).sum(axis=1)
    with np.errstate(all="ignore"):
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            m = np.nanmedian(v, axis=1)
    m[n < 3] = np.nan
    return m, n


def metrics(est, obs):
    has_obs = np.isfinite(obs)
    both = has_obs & np.isfinite(est)
    e = est[both] - obs[both]
    if not len(e):
        return {"n": 0, "coverage": 0.0}
    chill_o = ((obs[both] >= 32) & (obs[both] <= 45)).sum()
    chill_e = ((est[both] >= 32) & (est[both] <= 45)).sum()
    return {
        "n": int(both.sum()),
        "coverage": round(both.sum() / max(has_obs.sum(), 1), 4),
        "mae": round(float(np.abs(e).mean()), 3),
        "bias": round(float(e.mean()), 3),
        "rmse": round(float(np.sqrt((e**2).mean())), 3),
        "p95_abs": round(float(np.quantile(np.abs(e), 0.95)), 2),
        "chill_obs_h": int(chill_o),
        "chill_est_h": int(chill_e),
        "chill_err_h": int(chill_e - chill_o),
    }


# ---------------------------------------------------------------- fit-window quantities (target history)
print("loading fit window ...", file=sys.stderr)
rf = load(*FIT)
fld_fit_et = build_field(rf, st_et, FIT[0], FIT[1] - H1, valid_col="et_ok")
fld_fit = build_field(rf, st_all, FIT[0], FIT[1] - H1, valid_col="qc_base")
mo_f, hr_f = pst_month_hour(fld_fit_et.hours)
et_offsets, cw_offsets = {}, {}
for sid in ET:
    # approximate ET reference: median(target - same-hour 4-peer median) per PST month x hour
    pm, _ = peer_median(fld_fit_et, sid)
    d = fld_fit_et.W[:, fld_fit_et.pos[sid]] - pm
    tab = pd.DataFrame({"m": mo_f, "h": hr_f, "d": d}).dropna().groupby(["m", "h"])["d"].median()
    et_offsets[sid] = tab
    # ChillWatch learned night/day offset: median(obs - model) over the same fit window
    row = fld_fit.station_row(sid)
    e = M.predict_location_frame(fld_fit, bundle, row.lat, row.lon, row.elevation_ft, exclude={sid})
    r = rf[rf["station_id"] == sid][["ts", "temp_f", "qc_status"]]
    n_f, d_f, n = M.compute_offset(r, e)
    cw_offsets[sid] = {"night_f": round(n_f, 3), "day_f": round(d_f, 3), "n": n}
del rf, fld_fit, fld_fit_et

# ---------------------------------------------------------------- evaluation windows
out = {
    "windows": {k: [str(a), str(b)] for k, (a, b) in WINDOWS.items()},
    "fit_window": [str(FIT[0]), str(FIT[1])],
    "cw_offsets": {str(k): v for k, v in cw_offsets.items()},
    "results": {},
    "eligibility": {},
}
hourly = []
for wname, (a, b) in WINDOWS.items():
    print(f"window {wname} ...", file=sys.stderr)
    r = load(a, b)
    rt = r[r["station_id"].isin(ET)]
    out["eligibility"][wname] = {
        "target_rows": int(len(rt)),
        "et_usable": int(rt["et_ok"].eq("ok").sum()),
        "cw_usable_qc_base_ok": int(rt["qc_base"].eq("ok").sum()),
        "source_flag_Y_kept_by_cw": int(
            (rt["qc_base"].eq("ok") & rt["qc_source"].fillna("").str.strip().ne("")).sum()
        ),
    }
    f_et = build_field(r, st_et, a, b - H1, valid_col="et_ok")
    f_cw = build_field(r, st_all, a, b - H1, valid_col="qc_base")  # ChillWatch eligibility
    f_cw_et = build_field(r, st_all, a, b - H1, valid_col="et_ok")  # full network, ET eligibility
    mo, hr = pst_month_hour(f_et.hours)
    night = is_night(f_et.hours)
    res = {}
    for sid in ET:
        obs = f_et.W[:, f_et.pos[sid]]  # truth: ET-eligible target readings
        row = f_cw.station_row(sid)
        lat, lon, z = float(row.lat), float(row.lon), float(row.elevation_ft)
        pm, npeer = peer_median(f_et, sid)
        off = et_offsets[sid].reindex(pd.MultiIndex.from_arrays([mo, hr])).to_numpy()
        phys_et = M.predict_location_frame(f_et, bundle, lat, lon, z, exclude={sid}, use_model=False)
        phys_full = M.predict_location_frame(f_cw, bundle, lat, lon, z, exclude={sid}, use_model=False)
        phys_full_et = M.predict_location_frame(f_cw_et, bundle, lat, lon, z, exclude={sid}, use_model=False)
        mdl = M.predict_location_frame(f_cw, bundle, lat, lon, z, exclude={sid}, use_model=True)
        o = cw_offsets[sid]
        mdl_off = mdl["expected_f"].to_numpy() + np.where(night, o["night_f"], o["day_f"])
        hourly.append(
            pd.DataFrame(
                {
                    "window": wname,
                    "station": sid,
                    "ts": f_et.hours,
                    "night": night,
                    "obs_et": obs,
                    "obs_cw": f_cw.W[:, f_cw.pos[sid]],
                    "et4_peer_median": pm,
                    "et4_n_peers": npeer,
                    "et4_plus_offset": pm + off,
                    "phys_et4": phys_et["expected_f"].to_numpy(),
                    "phys_full": phys_full["expected_f"].to_numpy(),
                    "phys_full_et_elig": phys_full_et["expected_f"].to_numpy(),
                    "phys_full_p10": phys_full["p10"].to_numpy(),
                    "phys_full_p90": phys_full["p90"].to_numpy(),
                    "phys_full_n": phys_full["n_neighbors"].to_numpy(),
                    "model_off": mdl_off,
                    "model_p10": mdl["p10"].to_numpy() + (mdl_off - mdl["expected_f"].to_numpy()),
                    "model_p90": mdl["p90"].to_numpy() + (mdl_off - mdl["expected_f"].to_numpy()),
                    "model_method": mdl["method"].to_numpy(),
                }
            )
        )
        res[str(sid)] = {
            "station_held_out": {
                "et4_peer_median": metrics(pm, obs),
                "cw_physics_et4": metrics(phys_et["expected_f"].to_numpy(), obs),
                "cw_physics_full_network": metrics(phys_full["expected_f"].to_numpy(), obs),
                "cw_physics_full_network_et_eligibility": metrics(phys_full_et["expected_f"].to_numpy(), obs),
            },
            "known_station": {
                "et4_peer_median_plus_month_hour_offset": metrics(pm + off, obs),
                "cw_model_plus_offset": metrics(mdl_off, obs),
            },
            "full_network_neighbors_used": sorted(
                int(f_cw.station_ids[x])
                for x in np.unique(f_cw.schedule(lat, lon, z, exclude={sid})[0])
                if x >= 0
            ),
        }
    out["results"][wname] = res

pd.concat(hourly).to_parquet(sys.argv[1] if len(sys.argv) > 1 else "terra_compat_hourly.parquet", index=False)

# ---------------------------------------------------------------- replay hour: flagged-neighbor handling + causality
a, b = T("2026-01-10T08:00Z"), T("2026-01-14T08:00Z")
hit = T("2026-01-11T09:00Z")
r = load(a, b)
rep = {}
for label, inject, drop in [
    ("clean", False, False),
    ("davis_spike_unflagged", True, False),
    ("davis_spike_excluded_by_terra_flag", True, True),
]:
    rr = r.copy()
    if inject:
        m = (rr["station_id"] == 6) & (rr["ts"] == hit)
        rr.loc[m, "temp_f"] = 61.0
        if drop:
            rr.loc[m, "et_ok"] = "no"
    f = build_field(rr, st_et, a, b - H1, valid_col="et_ok")
    h = f.hours.get_loc(hit)
    vals = {}
    for sid in (226, 139, 212, 235):
        row = f.station_row(sid)
        p = M.predict_location_frame(
            f, bundle, float(row.lat), float(row.lon), float(row.elevation_ft), exclude={sid}, use_model=False
        )
        vals[str(sid)] = round(float(p["expected_f"].iloc[h]), 2)
    rep[label] = vals
# causality: estimate at the hit hour from a field that ends at that hour (no future data)
f_prefix = build_field(r[r["ts"] <= hit], st_all, a, hit, valid_col="qc_base")
f_full = build_field(r, st_all, a, b - H1, valid_col="qc_base")
row = f_full.station_row(6)
args = (float(row.lat), float(row.lon), float(row.elevation_ft))
p1 = M.predict_location_frame(f_prefix, bundle, *args, exclude={6})
p2 = M.predict_location_frame(f_full, bundle, *args, exclude={6})
rep["davis_obs_at_hit"] = float(r[(r["station_id"] == 6) & (r["ts"] == hit)]["temp_f"].iloc[0])
rep["prefix_vs_full_davis_model_estimate"] = [
    round(float(p1["expected_f"].iloc[-1]), 4),
    round(float(p2["expected_f"].iloc[f_full.hours.get_loc(hit)]), 4),
]
out["replay"] = rep
print(json.dumps(out, indent=1, default=str))
