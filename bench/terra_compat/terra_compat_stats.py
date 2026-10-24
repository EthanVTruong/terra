"""Paired day-block bootstrap CIs, gross chill misclassification, eligibility effect, ET fidelity check."""

import json
import sys

import numpy as np
import pandas as pd

d = pd.read_parquet(sys.argv[1])
ex = pd.read_parquet(sys.argv[2]) if len(sys.argv) > 2 else None
rng = np.random.default_rng(0)
B = 2000
ALL = ["et4_peer_median", "phys_et4", "phys_full", "phys_full_et_elig", "et4_plus_offset", "model_off"]
if ex is not None:
    k = ex[ex["part"] == "known_et_elig"][["window", "site", "ts", "model_off_et_elig"]].copy()
    k["station"] = k["site"].astype(int)
    d = d.merge(k.drop(columns="site"), on=["window", "station", "ts"], how="left")
    ALL = ALL + ["model_off_et_elig"]


def pst_day(ts):
    return (pd.DatetimeIndex(ts).tz_convert("Etc/GMT+8") - pd.Timedelta(hours=1)).normalize()


def paired_ci(df, truth, a, b):
    """MAE(a) - MAE(b) on common rows, 95% CI by resampling PST days (all stations of a day together)."""
    m = df[truth].notna() & df[a].notna() & df[b].notna()
    x = df[m]
    diff = (x[a] - x[truth]).abs() - (x[b] - x[truth]).abs()
    day = pst_day(x["ts"])
    g = pd.DataFrame({"day": day, "diff": diff.to_numpy()}).groupby("day")["diff"].agg(["sum", "count"])
    s, c = g["sum"].to_numpy(), g["count"].to_numpy()
    est = s.sum() / c.sum()
    bs = []
    for _ in range(B):
        i = rng.integers(0, len(s), len(s))
        bs.append(s[i].sum() / c[i].sum())
    lo, hi = np.quantile(bs, [0.025, 0.975])
    return {
        "n": int(m.sum()),
        "days": int(len(s)),
        "mae_diff": round(float(est), 3),
        "ci95": [round(float(lo), 3), round(float(hi), 3)],
    }


out = {"paired": {}, "chill": {}, "eligibility": {}, "fidelity": {}}
PAIRS = [
    ("phys_et4", "et4_peer_median", "held-out, same 4 Terra peers: CW IDW vs ET median"),
    ("phys_full", "et4_peer_median", "held-out: CW physics full network vs ET median"),
    ("phys_full", "phys_et4", "held-out: full network vs 4 Terra peers (network gain)"),
    ("model_off", "et4_plus_offset", "known station: CW model+offset vs ET-approx"),
    ("model_off_et_elig", "et4_plus_offset", "known station, ET eligibility: CW model+offset vs ET-approx"),
    ("phys_full_et_elig", "phys_full", "eligibility: blank-flag-only vs CW (Y kept) neighbors"),
]
for w in d["window"].unique():
    for truth in ("obs_et", "obs_cw"):
        dw = d[d["window"] == w]
        key = f"{w}|{truth}"
        out["paired"][key] = {}
        for a, b, label in PAIRS:
            if a in dw and b in dw:
                out["paired"][key][label] = {
                    "pooled": paired_ci(dw, truth, a, b),
                    "per_station": {int(s): paired_ci(g, truth, a, b) for s, g in dw.groupby("station")},
                }

# gross vs net chill-range misclassification, winter, common rows, Terra-eligible truth
# (32-45 F hour counts recomputed from CIMIS hourly readings, not FNRIC hours_32_45)
w = d[d["window"] == "inspected_winter"]
m = w["obs_et"].notna()
for c in ALL:
    m &= w[c].notna()
x = w[m]


def inr(s):
    return (s >= 32) & (s <= 45)


o = inr(x["obs_et"])
for c in ALL:
    e = inr(x[c])
    per = pd.DataFrame({"st": x["station"], "fp": e & ~o, "fn": ~e & o}).groupby("st")[["fp", "fn"]].sum()
    out["chill"][c] = {
        "net_err_h_per_station": (per["fp"] - per["fn"]).astype(int).to_dict(),
        "gross_misclassified_h_per_station": (per["fp"] + per["fn"]).astype(int).to_dict(),
        "mean_abs_net": round(float((per["fp"] - per["fn"]).abs().mean()), 1),
        "mean_gross": round(float((per["fp"] + per["fn"]).mean()), 1),
    }
out["chill"]["obs_chill_hours_per_station"] = (
    x.assign(c=o).groupby("station")["c"].sum().astype(int).to_dict()
)
out["chill"]["common_hours_per_station"] = x.groupby("station").size().astype(int).to_dict()

# eligibility effect on the hours ET drops (target Y-flagged: obs_cw present, obs_et missing)
for wname in d["window"].unique():
    dw = d[d["window"] == wname]
    y = dw["obs_cw"].notna() & dw["obs_et"].isna()
    r = {"y_hours": int(y.sum())}
    for c in ("et4_peer_median", "phys_full", "phys_full_et_elig", "model_off", "model_off_et_elig"):
        if c in dw:
            mm = y & dw[c].notna()
            e = dw.loc[mm, c] - dw.loc[mm, "obs_cw"]
            r[c] = {
                "n": int(mm.sum()),
                "bias": round(float(e.mean()), 2),
                "mae": round(float(e.abs().mean()), 2),
            }
    t = dw["obs_et"].notna()
    r["et4_median_unavailable_frac_of_et_truth"] = round(float(dw.loc[t, "et4_peer_median"].isna().mean()), 3)
    r["et4_median_unavailable_frac_all_hours"] = round(float(dw["et4_peer_median"].isna().mean()), 3)
    out["eligibility"][wname] = r

# ET handoff example: Davis expected 37.65 F at 2026-01-11T09Z
row = d[(d["station"] == 6) & (d["ts"] == pd.Timestamp("2026-01-11T09:00Z"))]
out["fidelity"]["davis_2026-01-11T09Z"] = {
    "et_handoff_expected_f": 37.65,
    "et_approx_from_cw_db": round(float(row["et4_plus_offset"].iloc[0]), 2),
    "peer_median": round(float(row["et4_peer_median"].iloc[0]), 2),
    "n_peers": int(row["et4_n_peers"].iloc[0]),
    "cw_model_plus_offset": round(float(row["model_off"].iloc[0]), 2),
    "observed_f": float(row["obs_cw"].iloc[0]),
}


# ---------------------------------------------------------------- orchard path (terra_compat_extra.py output)
def orchard_summary(ex):
    o = ex[ex["part"] == "orchard"].copy()
    o["station"] = o["site"]
    res = {}
    methods = [
        "et5_median",
        "phys_et5_shrink0.0",
        "phys_et5_shrink0.5",
        "phys_et5_shrink1.0",
        "phys_full_et_elig",
        "phys_full",
    ]
    pairs = [
        ("phys_et5_shrink0.5", "et5_median"),
        ("phys_et5_shrink0.0", "et5_median"),
        ("phys_et5_shrink0.5", "phys_et5_shrink0.0"),
        ("phys_full_et_elig", "phys_et5_shrink0.5"),
        ("phys_full", "phys_et5_shrink0.5"),
    ]
    for (w, kind), g in o.groupby(["window", "kind"]):
        truth = "obs_cw" if kind == "asos" else "obs_et"  # ASOS has no CIMIS flags
        t = g[truth].notna()
        m = t.copy()
        for c in methods:
            m &= g[c].notna()
        x = g[m]
        r = {
            "truth": truth,
            "truth_hours": int(t.sum()),
            "common_hours": int(m.sum()),
            "mae": {c: round(float((x[c] - x[truth]).abs().mean()), 3) for c in methods},
            "bias": {c: round(float((x[c] - x[truth]).mean()), 3) for c in methods},
            "per_site_mae": {
                s: {c: round(float((gg[c] - gg[truth]).abs().mean()), 3) for c in methods}
                for s, gg in x.groupby("site")
            },
            "availability_current_rule": {c: round(float(g.loc[t, c].notna().mean()), 3) for c in methods},
            "availability_3_peer_rule": {
                "et5_median": round(float(g.loc[t, "et5_median"].notna().mean()), 3),
                "phys_et5": round(
                    float((g.loc[t, "phys_et5_shrink0.5"].notna() & (g.loc[t, "phys_et5_n"] >= 3)).mean()), 3
                ),
                "phys_full": round(
                    float((g.loc[t, "phys_full"].notna() & (g.loc[t, "phys_full_n"] >= 3)).mean()), 3
                ),
            },
            "paired": {f"{a} - {b}": paired_ci(g.assign(ts=g["ts"]), truth, a, b) for a, b in pairs},
        }
        res[f"{w}|{kind}"] = r
    return res


if ex is not None:
    out["orchard"] = orchard_summary(ex)

print(json.dumps(out, indent=1, default=str))
