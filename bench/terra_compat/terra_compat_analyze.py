"""Common-row comparison of the per-hour output of terra_compat_eval.py."""

import json
import sys

import pandas as pd

d = pd.read_parquet(sys.argv[1])
HELD = ["et4_peer_median", "phys_et4", "phys_full", "phys_full_et_elig"]
KNOWN = ["et4_plus_offset", "model_off"]
ALL = HELD + KNOWN


def score(df, truth, methods):
    m = df[truth].notna()
    for c in methods:
        m &= df[c].notna()
    x = df[m]
    out = {"n": int(m.sum()), "truth_hours": int(df[truth].notna().sum())}
    for c in methods:
        e = x[c] - x[truth]
        out[c] = (
            {
                "mae": round(float(e.abs().mean()), 3),
                "bias": round(float(e.mean()), 3),
                "p95": round(float(e.abs().quantile(0.95)), 2),
            }
            if len(x)
            else None
        )
    return out


res = {}
for w, truth in [(w, t) for w in d["window"].unique() for t in ("obs_et", "obs_cw")]:
    dw = d[d["window"] == w]
    key = f"{w}|truth={truth}"
    res[key] = {
        "pooled": score(dw, truth, ALL),
        "per_station": {int(s): score(g, truth, ALL) for s, g in dw.groupby("station")},
    }
    # availability per method over truth hours (no common-row filter)
    t = dw[truth].notna()
    res[key]["availability"] = {c: round(float(dw.loc[t, c].notna().mean()), 4) for c in ALL}
    # p10-p90 coverage (nominal 80%) where the band exists
    cov = {}
    for est, lo, hi in [
        ("phys_full", "phys_full_p10", "phys_full_p90"),
        ("model_off", "model_p10", "model_p90"),
    ]:
        mm = t & dw[lo].notna() & dw[hi].notna()
        inside = (dw.loc[mm, truth] >= dw.loc[mm, lo]) & (dw.loc[mm, truth] <= dw.loc[mm, hi])
        cov[est] = {
            "n": int(mm.sum()),
            "p10_p90_coverage": round(float(inside.mean()), 3) if mm.any() else None,
        }
    res[key]["interval_coverage"] = cov
    # warm-spell hours that ET eligibility drops (Y flags): how do estimates do there?
    y = dw["obs_cw"].notna() & dw["obs_et"].isna()
    if y.any():
        res[key]["cw_only_hours"] = {
            "n": int(y.sum()),
            **{
                c: round(float((dw.loc[y, c] - dw.loc[y, "obs_cw"]).abs().mean()), 3)
                for c in ("phys_full", "model_off")
                if dw.loc[y, c].notna().any()
            },
        }
# daily (PST) chill hours on common rows, winter window only, truth=obs_cw
print(json.dumps(res, indent=1))
