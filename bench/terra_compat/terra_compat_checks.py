"""Leakage and Y-flag checks behind docs/TERRA_COMPATIBILITY.md (read-only on the DBs).

1. Scramble test: replace each Terra station's own readings (temp, flags) with noise for three
   March 2026 days; every estimate for that station must stay identical.
2. Y-flag check: each Terra station vs its nearest ASOS sensor (a different network) on
   Y-flagged vs blank-flag hours.
"""

from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

from chillwatch.context import get_context
from chillwatch.qc import model as M
from chillwatch.qc.neighbors import build_field, haversine_km
from chillwatch.store import SqlStore

ET = [6, 226, 139, 212, 235]
T = pd.Timestamp
H1 = pd.Timedelta(hours=1)
ctx = get_context()
st_all = ctx.region_stations()
st_et = st_all[st_all["station_id"].isin(ET)]
bundle = ctx.model
out = {"scramble": {}, "y_vs_asos": {}}


def eligible(r):
    r = r.copy()
    blank = r["qc_source"].fillna("").str.strip().eq("")
    r["et_ok"] = np.where(r["qc_base"].eq("ok") & blank, "ok", "no")
    return r


def estimates(r, sid, a, b):
    row = st_all[st_all["station_id"] == sid].iloc[0]
    args = (float(row.lat), float(row.lon), float(row.elevation_ft))
    f_et = build_field(r, st_et, a, b - H1, valid_col="et_ok")
    f_cw = build_field(r, st_all, a, b - H1, valid_col="qc_base")
    f_cwet = build_field(r, st_all, a, b - H1, valid_col="et_ok")
    cols = {}
    for name, f, use in [
        ("phys_et4", f_et, False),
        ("phys_full", f_cw, False),
        ("phys_full_et", f_cwet, False),
        ("model", f_cw, True),
        ("model_et", f_cwet, True),
    ]:
        p = M.predict_location_frame(f, bundle, *args, exclude={sid}, use_model=use)
        for c in ("expected_f", "p10", "p90", "n_neighbors"):
            cols[f"{name}.{c}"] = p[c].to_numpy(dtype="float64")
    peers = [f_et.pos[s] for s in ET if s != sid]
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        cols["et4_median"] = np.nanmedian(f_et.W[:, peers], axis=1)
    return cols


a, b = T("2026-03-10T08:00Z"), T("2026-03-13T08:00Z")
r0 = eligible(ctx.store.readings(a, b - H1, st_all["station_id"].tolist()))
rng = np.random.default_rng(7)
for sid in ET:
    base = estimates(r0, sid, a, b)
    r1 = r0.copy()
    m = r1["station_id"] == sid
    r1.loc[m, "temp_f"] = rng.uniform(-5, 110, m.sum())
    r1.loc[m, "qc_base"] = rng.choice(["ok", "invalid", "missing"], m.sum())
    r1.loc[m, "qc_source"] = rng.choice([" ", "Y", "R"], m.sum())
    r1 = eligible(r1)
    alt = estimates(r1, sid, a, b)
    diff = max(
        float(np.nanmax(np.abs(base[k] - alt[k]))) if np.isfinite(base[k]).any() else 0.0 for k in base
    )
    same_nan = all(np.array_equal(np.isnan(base[k]), np.isnan(alt[k])) for k in base)
    out["scramble"][str(sid)] = {
        "hours": int(len(base["et4_median"])),
        "outputs_compared": len(base),
        "max_abs_diff": diff,
        "same_missing_pattern": same_nan,
    }

# Y flags vs the nearest ASOS sensor
asos = SqlStore(os.environ["CHILLWATCH_ASOS_DB_URL"])
ast = asos.stations()
for wname, (a, b) in {
    "untouched_2026-03": (T("2026-03-01T08:00Z"), T("2026-04-01T08:00Z")),
    "inspected_winter": (T("2025-12-01T08:00Z"), T("2026-03-01T08:00Z")),
}.items():
    r = ctx.store.readings(a, b - H1, ET)
    res = {}
    for sid in ET:
        row = st_all[st_all["station_id"] == sid].iloc[0]
        d = haversine_km(row.lat, row.lon, ast["lat"].to_numpy(), ast["lon"].to_numpy())
        j = int(np.argmin(d))
        aid, aname = int(ast["station_id"].iloc[j]), str(ast["source_station_id"].iloc[j])
        ra = asos.readings(a, b - H1, [aid])
        e = ra[ra["qc_base"].eq("ok")].set_index("ts")["temp_f"].rename("asos")
        c = r[(r["station_id"] == sid) & r["qc_base"].eq("ok")].set_index("ts")
        x = c.join(e, how="inner")
        x["flag"] = x["qc_source"].fillna("").str.strip().replace("", "blank")
        dd = x["temp_f"] - x["asos"]
        res[str(sid)] = {
            "asos": aname,
            "km": round(float(d[j]), 1),
            **{
                f: {
                    "n": int((x.flag == f).sum()),
                    "mean_diff": round(float(dd[x.flag == f].mean()), 2),
                    "mae": round(float(dd[x.flag == f].abs().mean()), 2),
                }
                for f in ("blank", "Y")
                if (x.flag == f).any()
            },
        }
    out["y_vs_asos"][wname] = res
print(json.dumps(out, indent=1))
