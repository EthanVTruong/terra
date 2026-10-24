"""Follow-up checks for the Terra compatibility report (scratch, read-only on the DBs).

A. Orchard path at independent test locations (ASOS sensors near Davis; CIMIS Delta stations),
   estimated from Terra's five stations only (lapse on / off) and from the full CIMIS network.
B. Known-station model under ET eligibility (neighbors and target offset rows blank-flag only).
C. Davis spike replay on the full 132-station network (physics and model).
D. CIMIS Davis vs ASOS University Airport (2 km) on Y-flagged vs blank-flag hours.
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

from chillwatch.context import get_context
from chillwatch.qc import model as M
from chillwatch.qc.features import is_night
from chillwatch.qc.neighbors import adjusted_neighbor_temps, build_field, idw_baseline
from chillwatch.store import SqlStore

OUT = sys.argv[1]
ET = [6, 226, 139, 212, 235]
T = pd.Timestamp
H1 = pd.Timedelta(hours=1)
FIT = (T("2023-09-01T08:00Z"), T("2025-09-01T08:00Z"))
WINDOWS = {
    "untouched_2026-03": (T("2026-03-01T08:00Z"), T("2026-04-01T08:00Z")),
    "inspected_winter": (T("2025-12-01T08:00Z"), T("2026-03-01T08:00Z")),
}
ASOS_TEST = ["EDU", "DWA", "SMF", "SAC", "VCB", "SUU"]
CIMIS_TEST = [243, 140, 242, 247]

ctx = get_context()
st_all = ctx.region_stations()
st_et = st_all[st_all["station_id"].isin(ET)]
bundle = ctx.model
asos = SqlStore(os.environ["CHILLWATCH_ASOS_DB_URL"])
ast = asos.stations()
ast = ast[ast["source_station_id"].isin(ASOS_TEST)]


def eligible(r):
    r = r.copy()
    blank = r["qc_source"].fillna("").str.strip().eq("")
    r["et_ok"] = np.where(r["qc_base"].eq("ok") & blank, "ok", "no")
    return r


def load(a, b, ids=None):
    return eligible(ctx.store.readings(a, b - H1, ids if ids is not None else st_all["station_id"].tolist()))


def phys(fld, lat, lon, z, exclude=None, shrink=0.5, scale=600.0):
    idx, dist, dz = fld.schedule(lat, lon, z, exclude=exclude)
    gamma = fld.gamma_excluding(exclude)
    adj = adjusted_neighbor_temps(fld.W, idx, dz, gamma, shrink)
    tp, n = idw_baseline(adj, dist, dz_ft=dz, dz_scale_ft=scale)
    return tp, n, gamma


def median_of(fld, ids, need=3):
    v = fld.W[:, [fld.pos[s] for s in ids if s in fld.pos]]
    n = np.isfinite(v).sum(axis=1)
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        m = np.nanmedian(v, axis=1)
    m[n < need] = np.nan
    return m


out = {"lapse_stats": {}, "spike_full_network": {}, "davis_vs_edu": {}}
frames = []

# ------------------------------------------------ B. offsets under ET eligibility (fit window)
print("fit window ...", file=sys.stderr)
rf = load(*FIT)
fld_fit_et = build_field(rf, st_all, FIT[0], FIT[1] - H1, valid_col="et_ok")
off_et = {}
for sid in ET:
    row = fld_fit_et.station_row(sid)
    e = M.predict_location_frame(fld_fit_et, bundle, row.lat, row.lon, row.elevation_ft, exclude={sid})
    r = rf[rf["station_id"] == sid][["ts", "temp_f", "et_ok"]].rename(columns={"et_ok": "qc_status"})
    n_f, d_f, n = M.compute_offset(r, e)
    off_et[sid] = (n_f, d_f, n)
out["cw_offsets_et_eligibility"] = {
    str(k): {"night_f": round(v[0], 3), "day_f": round(v[1], 3), "n": v[2]} for k, v in off_et.items()
}
del rf, fld_fit_et

for wname, (a, b) in WINDOWS.items():
    print(f"window {wname} ...", file=sys.stderr)
    r = load(a, b)
    f_et5 = build_field(r, st_et, a, b - H1, valid_col="et_ok")
    f_cw = build_field(r, st_all, a, b - H1, valid_col="qc_base")
    f_cw_et = build_field(r, st_all, a, b - H1, valid_col="et_ok")
    night = is_night(f_et5.hours)
    # lapse on the 5-station field with nothing excluded (the orchard path)
    g = f_et5.gamma
    out["lapse_stats"][wname] = {
        "hours": int(len(g)),
        "finite": round(float(np.isfinite(g).mean()), 4),
        "clipped_at_10": round(float((np.abs(g) >= 9.999).mean()), 4),
        "median_abs_f_per_kft": round(float(np.nanmedian(np.abs(g))), 2),
    }
    # B. known-station model with ET eligibility everywhere
    for sid in ET:
        row = f_cw_et.station_row(sid)
        p = M.predict_location_frame(
            f_cw_et, bundle, float(row.lat), float(row.lon), float(row.elevation_ft), exclude={sid}
        )
        n_f, d_f, _ = off_et[sid]
        frames.append(
            pd.DataFrame(
                {
                    "part": "known_et_elig",
                    "window": wname,
                    "site": str(sid),
                    "ts": f_cw_et.hours,
                    "model_off_et_elig": p["expected_f"].to_numpy() + np.where(night, n_f, d_f),
                }
            )
        )
    # A. orchard path at independent test sites
    ra = asos.readings(a, b - H1, ast["station_id"].tolist())
    sites = [
        (s.source_station_id, float(s.lat), float(s.lon), float(s.elevation_ft), "asos", int(s.station_id))
        for s in ast.itertuples()
    ]
    for sid in CIMIS_TEST:
        row = f_cw.station_row(sid)
        sites.append((str(sid), float(row.lat), float(row.lon), float(row.elevation_ft), "cimis", sid))
    for name, lat, lon, z, kind, key in sites:
        if kind == "asos":
            rr = ra[ra["station_id"] == key].set_index("ts")
            ok = rr["qc_base"].eq("ok")
            obs_cw = rr["temp_f"].where(ok).reindex(f_et5.hours).to_numpy()
            obs_et = obs_cw
            excl = None
        else:
            obs_cw = f_cw.W[:, f_cw.pos[key]]
            obs_et = f_cw_et.W[:, f_cw_et.pos[key]]
            excl = {key}
        rec = {
            "part": "orchard",
            "window": wname,
            "site": name,
            "kind": kind,
            "ts": f_et5.hours,
            "obs_cw": obs_cw,
            "obs_et": obs_et,
            "elev_ft": z,
        }
        rec["et5_median"] = median_of(f_et5, ET)
        for sh in (0.0, 0.5, 1.0):
            rec[f"phys_et5_shrink{sh}"], rec["phys_et5_n"], _ = phys(f_et5, lat, lon, z, shrink=sh)
        rec["phys_full"], rec["phys_full_n"], _ = phys(f_cw, lat, lon, z, exclude=excl)
        rec["phys_full_et_elig"], _, _ = phys(f_cw_et, lat, lon, z, exclude=excl)
        rec["phys_full_noLapse"], _, _ = phys(f_cw, lat, lon, z, exclude=excl, shrink=0.0)
        frames.append(pd.DataFrame(rec))
    # D. CIMIS Davis vs ASOS EDU on Y vs blank hours (both raw readings, CW-usable)
    edu = ast[ast["source_station_id"] == "EDU"]["station_id"].iloc[0]
    e = ra[(ra["station_id"] == edu) & ra["qc_base"].eq("ok")].set_index("ts")["temp_f"]
    for cid in (6,):
        c = r[(r["station_id"] == cid) & r["qc_base"].eq("ok")].set_index("ts")
        j = c.join(e.rename("edu"), how="inner")
        j["flag"] = j["qc_source"].fillna("").str.strip().replace("", "blank")
        d = j["temp_f"] - j["edu"]
        out["davis_vs_edu"][wname] = {
            f: {
                "n": int((j.flag == f).sum()),
                "mean_diff": round(float(d[j.flag == f].mean()), 2),
                "mae": round(float(d[j.flag == f].abs().mean()), 2),
                "mean_temp": round(float(j.loc[j.flag == f, "temp_f"].mean()), 1),
            }
            for f in j["flag"].unique()
        }

# ------------------------------------------------ C. spike on the full network
a, b = T("2026-01-10T08:00Z"), T("2026-01-14T08:00Z")
hit = T("2026-01-11T09:00Z")
r = load(a, b)
res = {}
for label, inject, drop in [
    ("clean", False, False),
    ("spike_unflagged", True, False),
    ("spike_flagged", True, True),
    ("clean_davis_dropped", False, True),
]:
    rr = r.copy()
    m = (rr["station_id"] == 6) & (rr["ts"] == hit)
    if inject:
        rr.loc[m, "temp_f"] = 61.0
    if drop:
        rr.loc[m, "qc_base"] = "invalid"
    f = build_field(rr, st_all, a, b - H1, valid_col="qc_base")
    h = f.hours.get_loc(hit)
    vals = {}
    for sid in st_all["station_id"].astype(int):
        if sid == 6:
            continue
        row = f.station_row(sid)
        idx, _, _ = f.schedule(float(row.lat), float(row.lon), float(row.elevation_ft), exclude={sid})
        if f.pos[6] not in idx[h]:
            continue
        p = M.predict_location_frame(
            f, bundle, float(row.lat), float(row.lon), float(row.elevation_ft), exclude={sid}
        )
        q = M.predict_location_frame(
            f, bundle, float(row.lat), float(row.lon), float(row.elevation_ft), exclude={sid}, use_model=False
        )
        vals[str(sid)] = {
            "model": round(float(p["expected_f"].iloc[h]), 2),
            "physics": round(float(q["expected_f"].iloc[h]), 2),
        }
    res[label] = vals
out["spike_full_network"] = res

pd.concat(frames, ignore_index=True).to_parquet(OUT, index=False)
print(json.dumps(out, indent=1, default=str))
