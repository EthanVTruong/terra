"""Benchmark: inject faults into held-out seasons, replay the daily job, score it.

Writes ``bench/results/{timestamp}.json`` and ``.md``. These are the only numbers allowed
on slides; a run on synthetic data is labeled as such in both files.
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
from ..context import Context
from ..demo.faults import FaultLabel, inject, random_faults
from ..pipeline import replay
from ..qc.detect import Detection
from ..qc.model import chill_hours, chill_mask
from ..store import MemoryStore
from ..timeutil import season_bounds, utc

log = logging.getLogger(__name__)

COMPATIBLE = {
    "outage": {"offline", "system_outage"},
    "stuck": {"stuck"},
    "spike": {"spike", "step"},
    "drift": {"drift", "offset"},
    "offset": {"offset", "drift"},
    "clock_shift": {"clock_shift"},
}
PRIMARY = {"outage": "offline", "stuck": "stuck", "spike": "spike", "drift": "drift", "offset": "offset",
           "clock_shift": "clock_shift"}


def _span(d: Detection, as_of: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    return pd.Timestamp(d.start_ts), pd.Timestamp(d.end_ts) if d.end_ts is not None else as_of


def _covers(d: Detection, sid: int) -> bool:
    if d.station_id == sid:
        return True
    return d.type == "system_outage" and sid in d.evidence.get("stations_offline", [])


def match_faults(labels: list[FaultLabel], runs: list[tuple[pd.Timestamp, list[Detection]]],
                 grace_days: int = 7) -> list[dict[str, Any]]:
    out = []
    for lab in labels:
        a, b = lab.start_ts, lab.end_ts + pd.Timedelta(days=grace_days)
        hit = None
        for as_of, dets in runs:
            if as_of < a:
                continue
            for d in dets:
                if d.type not in COMPATIBLE[lab.type] or not _covers(d, lab.station_id):
                    continue
                s, e = _span(d, as_of)
                if s <= b and e >= a - pd.Timedelta(hours=6):
                    hit = (as_of, d)
                    break
            if hit:
                break
        rec = {**lab.to_dict(), "detected": hit is not None}
        if hit:
            rec["first_detected_run"] = hit[0].isoformat()
            rec["hours_to_detect"] = float((hit[0] - a) / pd.Timedelta(hours=1))
            rec["detected_as"] = hit[1].type
            rec["type_correct"] = hit[1].type == PRIMARY[lab.type]
        out.append(rec)
    return out


def false_alarms(labels: list[FaultLabel], runs: list[tuple[pd.Timestamp, list[Detection]]],
                 station_days: dict[int, float]) -> dict[str, Any]:
    """Unique detections not explained by any injected fault, per station-month."""
    seen: dict[tuple[int, str, str], Detection] = {}
    last_as_of: dict[tuple[int, str, str], pd.Timestamp] = {}
    for as_of, dets in runs:
        for d in dets:
            seen.setdefault(d.key(), d)
            last_as_of[d.key()] = as_of
    injected = {lab.station_id for lab in labels}

    def explained(d: Detection, as_of: pd.Timestamp) -> bool:
        s, e = _span(d, as_of)
        for lab in labels:
            if not _covers(d, lab.station_id):
                continue
            if s <= lab.end_ts + pd.Timedelta(days=7) and e >= lab.start_ts - pd.Timedelta(days=1):
                return True
        return False

    fa = [d for k, d in seen.items() if not explained(d, last_as_of[k])]
    # merge repeats of the same ongoing problem reported with a moving start
    groups: dict[tuple[int, str], list[Detection]] = {}
    for d in sorted(fa, key=lambda x: (x.station_id, x.type, pd.Timestamp(x.start_ts))):
        groups.setdefault((d.station_id, d.type), []).append(d)
    events = []
    for _, ds in groups.items():
        cur_end = None
        for d in ds:
            s = pd.Timestamp(d.start_ts)
            if cur_end is None or s > cur_end + pd.Timedelta(hours=24):
                events.append(d)
            e = pd.Timestamp(d.end_ts) if d.end_ts is not None else s + pd.Timedelta(days=1)
            cur_end = max(cur_end, e) if cur_end is not None else e
    clean_months = sum(v for k, v in station_days.items() if k not in injected) / 30.0
    all_months = sum(station_days.values()) / 30.0
    on_clean = [d for d in events if d.station_id not in injected and d.station_id != 0]
    by_type: dict[str, int] = {}
    for d in events:
        by_type[d.type] = by_type.get(d.type, 0) + 1
    by_type_clean: dict[str, int] = {}
    for d in on_clean:
        by_type_clean[d.type] = by_type_clean.get(d.type, 0) + 1
    return {
        "uninjected_station_months": round(clean_months, 2),
        "alarms_on_uninjected_stations": len(on_clean),
        "per_station_month_uninjected": round(len(on_clean) / clean_months, 4) if clean_months else None,
        "by_type_uninjected": by_type_clean,
        "all_station_months": round(all_months, 2),
        "unexplained_alarms_all_stations": len(events),
        "per_station_month_all": round(len(events) / all_months, 4) if all_months else None,
        "by_type_all": by_type,
        "note": "alarms on real data can be real station problems; they are counted as false alarms here",
    }


def chill_errors(truth: pd.DataFrame, faulty: pd.DataFrame, repaired: pd.DataFrame,
                 labels: list[FaultLabel], season: str) -> list[dict[str, Any]]:
    """Season chill hours on stations with a fault inside the chill window (Nov 1 on)."""
    out = []
    chill_start = pd.Timestamp(f"{season[:4]}-{config.thresholds()['chill']['start_mmdd']} 01:00", tz="UTC") \
        + pd.Timedelta(hours=8)
    for sid in sorted({lab.station_id for lab in labels if lab.end_ts >= chill_start}):
        t = truth[truth["station_id"] == sid].set_index("ts")
        f = faulty[faulty["station_id"] == sid].set_index("ts").reindex(t.index)
        r = repaired[repaired["station_id"] == sid].set_index("ts").reindex(t.index)
        m = chill_mask(t.index) & t["temp_f"].notna().to_numpy() & t["qc_base"].eq("ok").to_numpy()
        if m.sum() < 24 * 30:
            continue
        ct = chill_hours(t["temp_f"].to_numpy()[m])
        cf = chill_hours(f["temp_f"].to_numpy()[m])
        cr = chill_hours(r["used_temp_f"].to_numpy()[m])
        out.append({"station_id": sid, "season": season,
                    "fault_types": sorted({lab.type for lab in labels if lab.station_id == sid}),
                    "truth": ct, "faulty_unrepaired": cf, "repaired": cr,
                    "error_unrepaired": cf - ct, "error_repaired": cr - ct})
    return out


def gapfill_accuracy(ctx: Context, truth: pd.DataFrame, stations: pd.DataFrame, n_blocks: int,
                     seed: int) -> list[dict[str, Any]]:
    """Hide random real blocks (1 h - 7 d) on clean data and score the estimates."""
    rng = np.random.default_rng(seed + 7)
    ok = truth[truth["qc_base"].eq("ok") & truth["temp_f"].notna()]
    ids = ok["station_id"].value_counts()
    ids = ids[ids > 24 * 30].index.to_numpy()
    hours = pd.DatetimeIndex(sorted(truth["ts"].unique()))
    out = []
    for _ in range(n_blocks):
        if len(ids) == 0:
            break
        sid = int(rng.choice(ids))
        length = int(np.exp(rng.uniform(0, np.log(24 * 7))))  # log-uniform 1 h .. 7 d
        i0 = int(rng.integers(0, max(1, len(hours) - length)))
        a, b = hours[i0], hours[min(i0 + length - 1, len(hours) - 1)]
        pad = pd.Timedelta(hours=1)
        win = truth[(truth["ts"] >= a - pad) & (truth["ts"] <= b + pad)].copy()
        m = (win["station_id"] == sid) & (win["ts"] >= a) & (win["ts"] <= b)
        actual = win.loc[m].set_index("ts")["temp_f"].where(win.loc[m].set_index("ts")["qc_base"].eq("ok"))
        win.loc[m, "temp_f"] = np.nan
        win.loc[m, ["qc_base", "qc_status"]] = "missing"
        c = Context(MemoryStore(stations, win), ctx.model)
        e = c.expected(a, b, [sid]).set_index("ts")
        est = e["expected_f"].reindex(actual.index)
        both = actual.notna() & est.notna()
        if both.sum() == 0:
            continue
        cm = chill_mask(actual.index) & both.to_numpy()
        # blocks with no chill-season hours (October) have no chill error to report, not a zero
        ch_err = (chill_hours(est[cm].to_numpy()) - chill_hours(actual[cm].to_numpy())) if cm.any() else None
        out.append({"station_id": sid, "start_ts": a.isoformat(), "hours": int(length),
                    "mae_f": float((actual[both] - est[both]).abs().mean()),
                    "chill_hours_error": ch_err, "chill_window_hours": int(cm.sum()),
                    "methods": e["method"].value_counts().to_dict()})
    return out


def _summary(matches: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for t in COMPATIBLE:
        ms = [m for m in matches if m["type"] == t]
        if not ms:
            continue
        det = [m for m in ms if m["detected"]]
        ttd = [m["hours_to_detect"] for m in det]
        out[t] = {"n": len(ms), "detected": len(det), "recall": round(len(det) / len(ms), 3),
                  "type_correct": sum(1 for m in det if m.get("type_correct")),
                  "median_hours_to_detect": float(np.median(ttd)) if ttd else None}
    return out


def _gap_summary(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    if not blocks:
        return {}
    df = pd.DataFrame(blocks)
    bins = [0, 6, 24, 72, 24 * 7 + 1]
    labels = ["1-6h", "7-24h", "1-3d", "3-7d"]
    df["bin"] = pd.cut(df["hours"], bins, labels=labels, right=True)
    df["chill_hours_error"] = pd.to_numeric(df["chill_hours_error"], errors="coerce")

    def chill(g: pd.DataFrame) -> float | None:
        c = g["chill_hours_error"].dropna()
        return round(float(c.abs().mean()), 2) if len(c) else None

    by = {}
    for k, g in df.groupby("bin", observed=True):
        by[str(k)] = {"blocks": int(len(g)), "mae_f": round(float(g["mae_f"].mean()), 3),
                      "chill_blocks": int(g["chill_hours_error"].notna().sum()),
                      "mean_abs_chill_hours_error": chill(g)}
    return {"blocks": int(len(df)), "mae_f": round(float(df["mae_f"].mean()), 3),
            "chill_blocks": int(df["chill_hours_error"].notna().sum()),
            "mean_abs_chill_hours_error": chill(df), "by_length": by,
            "note": "chill-hour error is over blocks that include chill-season hours (Nov 1 on)"}


def run_bench(n_faults: int = 100, seed: int = 42, ctx: Context | None = None, seasons: list[str] | None = None,
              gapfill_blocks: int = 200, out_dir: Path | None = None, max_days: int | None = None
              ) -> dict[str, Any]:
    from ..context import get_context

    ctx = ctx or get_context()
    reg = config.region()
    seasons = seasons or list(reg.get("holdout_seasons", []))
    stations = ctx.region_stations()
    synthetic = bool((stations.get("county") == "synthetic").any()) if "county" in stations else False
    sources = sorted(set(stations["source"].astype(str))) if "source" in stations else ["unknown"]
    network = {"cimis": "CIMIS", "asos": "NOAA ASOS (IEM archive), not CIMIS"}.get(sources[0], sources[0]) \
        if len(sources) == 1 else "+".join(sources)
    per = [n_faults // len(seasons) + (1 if i < n_faults % len(seasons) else 0) for i in range(len(seasons))]
    all_matches, all_chill, all_gap = [], [], []
    fa_parts = []
    labels_all: list[FaultLabel] = []
    for si, season in enumerate(seasons):
        a, b = season_bounds(season, reg["seasons"]["start_mmdd"], reg["seasons"]["end_mmdd"])
        if max_days:
            b = min(b, a + pd.Timedelta(days=max_days) - pd.Timedelta(hours=1))
        truth = ctx.store.readings(a, b, stations["station_id"].tolist())
        if truth.empty:
            log.warning("no data for %s", season)
            continue
        cov = truth.groupby("station_id")["temp_f"].apply(lambda s: s.notna().mean())
        cand = cov[cov > 0.8].index.astype(int).tolist()
        specs = random_faults(cand, a, b, per[si], seed=seed + si)
        faulty, labels = inject(truth, specs)
        labels_all += labels
        pre = Context(MemoryStore(stations, faulty), ctx.model)
        pre.precompute_expected(a, b)
        rc = Context(MemoryStore(stations), ctx.model)
        rc.freeze_from(pre)
        first_day = (a - pd.Timedelta(hours=9)).normalize().tz_localize(None)
        last_day = (b - pd.Timedelta(hours=9)).normalize().tz_localize(None)
        log.info("replaying %s (%d faults)", season, len(labels))
        res = replay(rc, faulty, first_day, last_day)
        all_matches += match_faults(labels, res.runs)
        repaired = rc.store.readings(a, b)
        all_chill += chill_errors(truth, faulty, repaired, labels, season)
        obs_days = truth.groupby("station_id")["temp_f"].apply(lambda s: s.notna().sum() / 24.0).to_dict()
        fa_parts.append(false_alarms(labels, res.runs, obs_days))
        all_gap += gapfill_accuracy(ctx, truth, stations, gapfill_blocks // len(seasons), seed + si)

    fa_total = {
        "uninjected_station_months": round(sum(p["uninjected_station_months"] for p in fa_parts), 2),
        "alarms_on_uninjected_stations": sum(p["alarms_on_uninjected_stations"] for p in fa_parts),
        "all_station_months": round(sum(p["all_station_months"] for p in fa_parts), 2),
        "unexplained_alarms_all_stations": sum(p["unexplained_alarms_all_stations"] for p in fa_parts),
        "by_season": fa_parts,
    }
    fa_total["per_station_month_uninjected"] = (
        round(fa_total["alarms_on_uninjected_stations"] / fa_total["uninjected_station_months"], 4)
        if fa_total["uninjected_station_months"] else None)
    fa_total["per_station_month_all"] = (
        round(fa_total["unexplained_alarms_all_stations"] / fa_total["all_station_months"], 4)
        if fa_total["all_station_months"] else None)
    ce = pd.DataFrame(all_chill)
    chill_summary = {}
    if len(ce):
        chill_summary = {
            "station_seasons": int(len(ce)),
            "mean_abs_error_unrepaired": round(float(ce["error_unrepaired"].abs().mean()), 2),
            "mean_abs_error_repaired": round(float(ce["error_repaired"].abs().mean()), 2),
            "median_abs_error_unrepaired": round(float(ce["error_unrepaired"].abs().median()), 2),
            "median_abs_error_repaired": round(float(ce["error_repaired"].abs().median()), 2),
        }
    result = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "data": "SYNTHETIC (not real data; do not present as results)" if synthetic else network,
        "model_version": ctx.model.version,
        "seasons": seasons,
        "n_faults": len(labels_all),
        "seed": seed,
        "detection": _summary(all_matches),
        "false_alarms": fa_total,
        "chill_hours": chill_summary,
        "gap_fill": _gap_summary(all_gap),
        "thresholds": config.thresholds(),
        "faults": all_matches,
        "chill_by_station": all_chill,
        "gap_blocks": all_gap,
    }
    out_dir = out_dir or config.repo_root() / "bench" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    p = out_dir / f"{stamp}.json"
    p.write_text(json.dumps(result, indent=2, default=str))
    (out_dir / f"{stamp}.md").write_text(markdown(result))
    result["paths"] = [str(p), str(out_dir / f"{stamp}.md")]
    return result


def markdown(r: dict[str, Any]) -> str:
    lines = [f"# ChillWatch benchmark ({r['generated_at']})", ""]
    if r["data"].startswith("SYNTHETIC"):
        lines += ["> **SYNTHETIC DATA. These numbers are a pipeline dry run, not ChillWatch results.**", ""]
    lines += [f"Data: {r['data']} | model `{r['model_version']}` | held-out seasons {', '.join(r['seasons'])} | "
              f"{r['n_faults']} injected faults, seed {r['seed']}", "",
              "Detection thresholds: " + ", ".join(f"{k} {r['thresholds']['residual'][k]}" for k in
                                                  ("spike_sigma", "cusum_k", "cusum_h", "mean24_sigma")
                                                  if k in r["thresholds"].get("residual", {}))
              + f"; stuck {r['thresholds'].get('stuck', {}).get('min_hours')} h"
              + f"; offline {r['thresholds'].get('offline', {}).get('warn_hours')} h", "",
              "## Detection", "",
              "| Fault type | Injected | Detected | Recall | Type correct | Median hours to detect |",
              "|---|---:|---:|---:|---:|---:|"]
    for t, s in r["detection"].items():
        mh = f"{s['median_hours_to_detect']:.0f}" if s["median_hours_to_detect"] is not None else "-"
        lines.append(f"| {t} | {s['n']} | {s['detected']} | {s['recall']:.2f} | {s['type_correct']} | {mh} |")
    fa = r["false_alarms"]
    lines += ["", "## False alarms", "",
              f"- Uninjected stations: {fa['alarms_on_uninjected_stations']} alarms over "
              f"{fa['uninjected_station_months']} station-months = **{fa['per_station_month_uninjected']} per station-month**",
              f"- All stations, alarms not explained by an injected fault: {fa['unexplained_alarms_all_stations']} over "
              f"{fa['all_station_months']} station-months = {fa['per_station_month_all']} per station-month",
              "- On real data some of these are genuine station problems.", ""]
    c = r["chill_hours"]
    if c:
        lines += ["## Season chill hours on faulted stations (32-45 F from Nov 1)", "",
                  "| | Mean abs error | Median abs error |", "|---|---:|---:|",
                  f"| Faulty, unrepaired | {c['mean_abs_error_unrepaired']} | {c['median_abs_error_unrepaired']} |",
                  f"| Repaired | {c['mean_abs_error_repaired']} | {c['median_abs_error_repaired']} |",
                  "", f"{c['station_seasons']} station-seasons.", ""]
    g = r["gap_fill"]
    if g:
        lines += ["## Gap filling on clean data (hidden real blocks)", "",
                  "| Block length | Blocks | MAE (F) | Mean abs chill-hour error |", "|---|---:|---:|---:|"]
        for k, v in g["by_length"].items():
            ce = v["mean_abs_chill_hours_error"]
            lines.append(f"| {k} | {v['blocks']} | {v['mae_f']} | {'-' if ce is None else ce} (n={v['chill_blocks']}) |")
        ce = g["mean_abs_chill_hours_error"]
        lines.append(f"| **all** | {g['blocks']} | {g['mae_f']} | {'-' if ce is None else ce} (n={g['chill_blocks']}) |")
        lines.append("")
        lines.append("Chill-hour error is averaged over blocks that include chill-season hours (Nov 1 on).")
    return "\n".join(lines) + "\n"


__all__ = ["run_bench", "utc"]
