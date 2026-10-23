"""Station error / out-of-distribution detection.

``run_detection(as_of)`` evaluates rules and residual tests per station and returns
``Detection`` objects. It is deterministic: the same stored data gives the same detections.
Detection reads the *source* status (``qc_base``), never the flags it wrote itself, so a
second run over the same data reproduces the first.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

import numpy as np
import pandas as pd

from .. import config
from ..normalize import QC_ORDER
from ..timeutil import utc
from .rules import range_mask, runs, step_mask, stuck_runs

log = logging.getLogger(__name__)

DetectionType = Literal[
    "offline", "system_outage", "range", "step", "stuck", "spike", "drift", "offset", "clock_shift"
]
Severity = Literal["info", "warn", "critical"]


@dataclass
class Detection:
    station_id: int
    type: DetectionType
    severity: Severity
    start_ts: datetime
    end_ts: datetime | None  # None = ongoing
    hours_affected: int
    evidence: dict[str, Any] = field(default_factory=dict)
    suggested_reading_status: Literal["suspect", "invalid"] | None = None

    def key(self) -> tuple[int, str, str]:
        return (self.station_id, self.type, pd.Timestamp(self.start_ts).isoformat())

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["start_ts"] = pd.Timestamp(self.start_ts).isoformat()
        d["end_ts"] = pd.Timestamp(self.end_ts).isoformat() if self.end_ts is not None else None
        return d


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, 3) if np.isfinite(v) else None


# =========================================================================== residual tools
def robust_sigma(r: np.ndarray, floor: float, prior: float | None = None, min_n: int = 24) -> float:
    """1.4826 * MAD with a floor; with fewer than ``min_n`` residuals use ``prior`` (model LOSO sd)."""
    r = r[np.isfinite(r)]
    if len(r) < min_n:
        return max(prior if prior is not None and np.isfinite(prior) else floor, floor)
    return max(float(1.4826 * np.median(np.abs(r - np.median(r)))), floor)


@dataclass
class CusumEvent:
    alarm: int
    onset: int
    end: int | None
    direction: int


def cusum_events(
    z: np.ndarray, r: np.ndarray, sigma: float, k: float, h: float, mean_sigma: float,
    extra_gate: Any = None,
) -> list[CusumEvent]:
    """Two-sided CUSUM on standardized residuals with a 24 h mean-residual gate.

    NaN hours are skipped (sums carried). An event alarms when S >= h and the trailing
    24 h mean residual exceeds ``mean_sigma * sigma`` in the same direction; it ends when
    the trailing 24 h mean falls back under 1 sigma, and the sums restart.
    """
    n = len(z)
    events: list[CusumEvent] = []
    sp = sm = 0.0
    zero_p = zero_m = 0
    i = 0
    rs = pd.Series(r)
    mean24 = rs.rolling(24, min_periods=12).mean().to_numpy()
    while i < n:
        zi = z[i]
        if np.isfinite(zi):
            sp = max(0.0, sp + zi - k)
            sm = max(0.0, sm - zi - k)
            if sp == 0.0:
                zero_p = i + 1
            if sm == 0.0:
                zero_m = i + 1
            m = mean24[i]
            fire = 0
            if sp >= h and np.isfinite(m) and m > mean_sigma * sigma:
                fire = 1
            elif sm >= h and np.isfinite(m) and m < -mean_sigma * sigma:
                fire = -1
            if fire and (extra_gate is None or extra_gate(m)):
                onset = zero_p if fire > 0 else zero_m
                end = None
                j = i + 1
                while j < n:
                    mj = mean24[j]
                    if np.isfinite(mj) and fire * mj < sigma:
                        end = j
                        break
                    j += 1
                events.append(CusumEvent(alarm=i, onset=min(onset, i), end=end, direction=fire))
                if end is None:
                    break
                i = end
                sp = sm = 0.0
                zero_p = zero_m = i + 1
        i += 1
    return events


def classify_shift(r: np.ndarray, onset: int, alarm: int, stop: int, step_hours: int,
                   ambiguous_hours: int = 48) -> dict[str, Any]:
    """Fit 'ramp of duration D starting at tau' to residuals; D <= step_hours => offset.

    tau is searched in [onset - 48 h, alarm], D over a fixed grid; magnitude M by least
    squares. The fit uses residuals up to ``stop`` (at most 14 days past the alarm).
    """
    lo = max(0, onset - 48)
    fin = np.flatnonzero(np.isfinite(r[lo:alarm + 1]))
    if len(fin):
        lo += int(fin[0])
    stop = min(stop, alarm + 24 * 14)
    seg = r[lo : stop + 1]
    idx = np.arange(len(seg))
    ok = np.isfinite(seg)
    if ok.sum() < 6:
        return {"kind": "offset", "tau": onset, "ramp_hours": 0, "magnitude": float("nan")}
    y = np.where(ok, seg, 0.0)
    durations = np.array([1, 2, 3, 4, 6, 9, 12, 18, 24, 36, 48, 72, 96, 144, 192, 240, 336, 480], dtype=float)
    best = (np.inf, onset, 1, float("nan"))
    okf = ok.astype(float)
    yy = float((y * y).sum())
    for tau in range(0, alarm - lo + 1):
        shape = np.clip((idx[None, :] - tau + 1) / durations[:, None], 0.0, 1.0) * okf  # (D, n)
        ss = (shape * shape).sum(axis=1)
        sy = (shape * y).sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            sse = np.where(ss > 0, yy - sy * sy / ss, np.inf)  # least-squares M = sy / ss
        k = int(np.argmin(sse))
        if sse[k] < best[0] - 1e-9:
            best = (float(sse[k]), tau, int(durations[k]), float(sy[k] / ss[k]))
    _, tau, D, M = best
    # The spec's rule is "step = change-point within 6 h". Hourly residuals carry diurnal
    # structure, so a clean step is often fitted as a 12-24 h ramp; ramps shorter than
    # ``ambiguous_hours`` are therefore also called offsets (bench drifts ramp over >= 3 days).
    kind = "offset" if (step_hours >= D or ambiguous_hours >= D) else "drift"
    return {"kind": kind, "tau": lo + tau, "ramp_hours": int(D), "magnitude": M}


def best_lag(x: np.ndarray, m: np.ndarray, lags: list[int], min_pairs: int = 24) -> tuple[int, float, float]:
    """Return (best_lag, corr_at_lag0, corr_at_best). Positive lag: station lags neighbors."""

    def corr(lag: int) -> float:
        if lag > 0:
            a, b = x[lag:], m[:-lag]
        elif lag < 0:
            a, b = x[:lag], m[-lag:]
        else:
            a, b = x, m
        ok = np.isfinite(a) & np.isfinite(b)
        if ok.sum() < min_pairs or np.std(a[ok]) == 0 or np.std(b[ok]) == 0:
            return float("nan")
        return float(np.corrcoef(a[ok], b[ok])[0, 1])

    c0 = corr(0)
    best, cb = 0, c0
    for lag in lags:
        c = corr(lag)
        if np.isfinite(c) and (not np.isfinite(cb) or c > cb):
            best, cb = lag, c
    return best, c0, cb


# =========================================================================== per station
def detect_station(
    station_id: int,
    hours: pd.DatetimeIndex,
    temp: np.ndarray,
    qc_base: np.ndarray,
    exp: pd.DataFrame | None,
    as_of: pd.Timestamp,
    report_from: pd.Timestamp,
    accepted_offset: float | None = None,
    th: dict[str, Any] | None = None,
    sigma_prior: float | None = None,
) -> tuple[list[Detection], np.ndarray]:
    """Run all per-station checks on an hourly series (pure).

    ``exp`` (aligned to ``hours``) has expected_f, n_neighbors, nbr_median.
    Returns (detections overlapping [report_from, as_of], suggested status per hour or None).
    """
    th = th or config.thresholds()
    sev = th["severity"]
    rt = th["residual"]
    H = len(hours)
    status = np.full(H, None, dtype=object)
    dets: list[Detection] = []
    temp = np.asarray(temp, dtype="float64")
    observed = np.isfinite(temp)
    src_bad = np.isin(qc_base, ["invalid", "missing"])

    def hour(i: int) -> pd.Timestamp:
        return hours[i]

    def add(d: Detection, lo: int, hi: int | None) -> None:
        last = H - 1 if hi is None else hi
        if d.suggested_reading_status:
            for i in range(lo, last + 1):
                cur = status[i]
                if cur is None or QC_ORDER[d.suggested_reading_status] > QC_ORDER[cur]:
                    status[i] = d.suggested_reading_status
        end_ts = hours[last] if hi is not None else None
        if (end_ts is None or end_ts >= report_from) and d.start_ts <= as_of:
            dets.append(d)

    # ---------------- range
    rmask = range_mask(temp, th["range"])
    for s, e in runs(rmask):
        ongoing = e == H - 1
        add(Detection(station_id, "range", sev["range"], hour(s), None if ongoing else hour(e), e - s + 1,
                      {"min_f": _f(np.nanmin(temp[s:e + 1])), "max_f": _f(np.nanmax(temp[s:e + 1]))},
                      "invalid"), s, None if ongoing else e)

    # ---------------- stuck
    nbr_med = exp["nbr_median"].to_numpy(dtype="float64") if exp is not None else None
    stuck = np.zeros(H, dtype=bool)
    for s, e, change in stuck_runs(np.where(src_bad, np.nan, temp), nbr_med, th["stuck"]):
        stuck[s:e + 1] = True
        last_obs = np.flatnonzero(observed)
        ongoing = len(last_obs) > 0 and e >= last_obs[-1]
        add(Detection(station_id, "stuck", sev["stuck"], hour(s), None if ongoing else hour(e), e - s + 1,
                      {"value_f": _f(temp[s]), "neighbor_change_f": _f(change)}, "invalid"),
            s, None if ongoing else e)

    # ---------------- residual tests
    resid = np.full(H, np.nan)
    insufficient = 0
    sigma = float(rt["sigma_floor_f"])
    if exp is not None:
        e_f = exp["expected_f"].to_numpy(dtype="float64")
        nn = exp["n_neighbors"].to_numpy(dtype="float64")
        usable = observed & ~src_bad & ~rmask & ~stuck & np.isfinite(e_f)
        enough = nn >= int(rt["min_neighbors"])
        insufficient = int((usable & ~enough).sum())
        usable &= enough
        resid = np.where(usable, temp - e_f, np.nan)
        win = hours >= as_of - pd.Timedelta(days=int(rt["sigma_window_days"]))
        ok_mask = win & np.isin(qc_base, ["ok"])
        sigma = robust_sigma(resid[ok_mask], float(rt["sigma_floor_f"]), sigma_prior, int(rt["min_sigma_hours"]))

        # spike candidates: 1-3 consecutive hours beyond 6 sigma (kept out of CUSUM)
        big = np.abs(resid) > float(rt["spike_sigma"]) * sigma
        spike = np.zeros(H, dtype=bool)
        spike_runs = [(s0, e0) for s0, e0 in runs(big) if e0 - s0 + 1 <= int(rt["spike_max_hours"])]
        for s0, e0 in spike_runs:
            spike[s0:e0 + 1] = True

        # drift / offset: CUSUM + 24 h mean gate
        z = np.where(spike, np.nan, resid / sigma)
        r_ns = np.where(spike, np.nan, resid)
        gate = None
        if accepted_offset is not None and abs(accepted_offset) > 0:
            grow = 1.0 + float(rt["regrow_fraction"])

            def gate(m: float) -> bool:
                return abs(m + accepted_offset) > grow * abs(accepted_offset)

        shifted = np.zeros(H, dtype=bool)
        for ev in cusum_events(z, r_ns, sigma, float(rt["cusum_k"]), float(rt["cusum_h"]),
                               float(rt["mean24_sigma"]), gate):
            stop = ev.end if ev.end is not None else H - 1
            cls = classify_shift(r_ns, ev.onset, ev.alarm, stop, int(rt["offset_step_hours"]),
                                 int(rt["ambiguous_ramp_hours"]))
            # the onset estimate is refined as post-alarm data arrives (same data -> same answer);
            # consumers should match an ongoing drift/offset by (station, type, overlap)
            onset = int(min(max(cls["tau"], 0), ev.alarm))
            shifted[onset:stop + 1] = True
            tail = r_ns[max(stop - 23, 0):stop + 1]
            mag = float(np.nanmean(tail)) if np.isfinite(tail).any() else float("nan")
            critical = abs(mag) > float(sev["critical_magnitude_sigma"]) * sigma
            zz = np.nan_to_num(z[ev.onset:ev.alarm + 1]) * ev.direction
            cus = 0.0
            for v in zz:
                cus = max(0.0, cus + v - float(rt["cusum_k"]))
            add(Detection(
                station_id, cls["kind"], "critical" if critical else sev[cls["kind"]],
                hour(onset), None if ev.end is None else hour(ev.end), stop - onset + 1,
                {"mean_residual_f": _f(mag), "sigma_f": _f(sigma), "cusum": _f(cus),
                 "alarm_ts": hour(ev.alarm).isoformat(), "ramp_hours": cls["ramp_hours"],
                 "neighbors_used": int(np.nanmedian(nn[onset:stop + 1])) if stop >= onset else None,
                 "accepted_offset_f": _f(accepted_offset) if accepted_offset is not None else None},
                "invalid"), onset, ev.end)

        # spikes, unless they are just the tail of a shifted period
        for s0, e0 in spike_runs:
            seg = resid[s0:e0 + 1]
            if shifted[s0:e0 + 1].any():
                lvl = np.nanmean(r_ns[max(s0 - 24, 0):s0]) if np.isfinite(r_ns[max(s0 - 24, 0):s0]).any() else 0.0
                if np.nanmax(np.abs(seg - lvl)) <= float(rt["spike_sigma"]) * sigma:
                    spike[s0:e0 + 1] = False
                    continue
            add(Detection(station_id, "spike", sev["spike"], hour(s0), hour(e0), e0 - s0 + 1,
                          {"max_residual_f": _f(seg[np.argmax(np.abs(seg))]),
                           "sigma_f": _f(sigma), "neighbors_used": int(nn[s0])}, "invalid"), s0, e0)

        # step: |dT| > 20 F between consecutive hours, confirmed by residual
        smask = step_mask(np.where(src_bad, np.nan, temp), th["step"])
        for i in np.flatnonzero(smask).tolist():
            if spike[i] or spike[max(i - 1, 0)] or rmask[i]:
                continue
            ri = resid[i]
            confirmed = bool(np.isfinite(ri) and abs(ri) > float(th["step"]["confirm_sigma"]) * sigma)
            if np.isfinite(ri) and not confirmed:
                continue
            add(Detection(station_id, "step", sev["step"], hour(i), hour(i), 1,
                          {"delta_f": _f(temp[i] - temp[i - 1]), "residual_f": _f(ri), "sigma_f": _f(sigma),
                           "confirmed": confirmed if np.isfinite(ri) else None}, "suspect"), i, i)

        # clock shift over the last 72 h: station vs its neighbor-based expected series
        # (expected_f carries the station's own diurnal shape; raw neighbor medians from
        # stations at other elevations peak at other hours and give false lags)
        cs = th["clock_shift"]
        w = int(cs["window_hours"])
        if w <= H:
            x = np.where(observed & ~src_bad & ~stuck & ~rmask & ~spike, temp, np.nan)[-w:]
            m = np.where(enough, e_f, np.nan)[-w:]
            lag, c0, cb = best_lag(x, m, [int(v) for v in cs["lags"]], min_pairs=(2 * w) // 3)
            if lag != 0 and np.isfinite(c0) and np.isfinite(cb) and cb - c0 > float(cs["min_gain"]):
                add(Detection(station_id, "clock_shift", sev["clock_shift"], hour(H - w), None, w,
                              {"lag_hours": int(lag), "corr_lag0": _f(c0), "corr_best": _f(cb),
                               "gain": _f(cb - c0)}, "invalid"), H - w, None)

    if insufficient:
        for d in dets:
            d.evidence.setdefault("hours_insufficient_neighbors", insufficient)
    return dets, status


def offline_detections(
    station_ids: list[int],
    last_obs: pd.Series,
    as_of: pd.Timestamp,
    report_from: pd.Timestamp,
    window_start: pd.Timestamp,
    obs_frame: dict[int, tuple[pd.DatetimeIndex, np.ndarray]],
    th: dict[str, Any] | None = None,
) -> list[Detection]:
    """Offline: ongoing gaps (>= warn_hours up to as_of) and closed gaps that ended in the report window."""
    th = th or config.thresholds()
    o = th["offline"]
    warn, crit = float(o["warn_hours"]), float(o["critical_hours"])
    out: list[Detection] = []
    for sid in station_ids:
        lo = last_obs.get(sid, pd.NaT)
        if pd.isna(lo):
            continue  # never reported: nothing to compare against (new / not yet backfilled)
        start = lo + pd.Timedelta(hours=1)
        gap_h = (as_of - start) / pd.Timedelta(hours=1) + 1
        if gap_h >= warn:
            out.append(Detection(int(sid), "offline", "critical" if gap_h >= crit else "warn", start, None,
                                 int(gap_h), {"last_observed": lo.isoformat() if pd.notna(lo) else None,
                                              "hours_without_data": int(gap_h)}, None))
        # closed gaps inside the loaded window
        if sid in obs_frame:
            hrs, obs = obs_frame[sid]
            for s, e in runs(~obs):
                if e == len(obs) - 1 or s == 0:
                    continue  # ongoing (handled above) or no observation before it in the window
                n = e - s + 1
                if n >= warn and hrs[e] >= report_from:
                    out.append(Detection(int(sid), "offline", "critical" if n >= crit else "warn", hrs[s], hrs[e],
                                         n, {"hours_without_data": n}, None))
    return out


# =========================================================================== orchestration
def _accepted_offsets(offs: pd.DataFrame, ids: list[int], as_of: pd.Timestamp) -> dict[int, float]:
    out: dict[int, float] = {}
    if offs is None or offs.empty:
        return out
    a = offs[(offs["kind"] == "accepted") & offs["station_id"].isin(ids)].copy()
    a = a[pd.to_datetime(a["window_end"], utc=True) <= as_of]
    for sid, g in a.groupby("station_id"):
        last = g.iloc[-1]
        out[int(sid)] = float(np.nanmean([last["night_f"], last["day_f"]]))
    return out


def run_detection(as_of: datetime, lookback_days: int = 7, ctx: Any | None = None,
                  apply: bool = True) -> list[Detection]:
    """Detect station problems for data up to ``as_of``; report those touching the lookback window.

    Also sets ``qc_status`` on affected readings (``qc_base`` worst-combined with suggestions).
    """
    from ..context import get_context

    ctx = ctx or get_context()
    th = config.thresholds()
    as_of_ts = utc(as_of).floor("h")
    report_from = as_of_ts - pd.Timedelta(days=lookback_days) + pd.Timedelta(hours=1)
    hist_days = max(int(th["residual"]["sigma_window_days"]), lookback_days)
    window_start = as_of_ts - pd.Timedelta(days=hist_days) + pd.Timedelta(hours=1)
    hours = pd.date_range(window_start, as_of_ts, freq="h")
    ids = ctx.active_station_ids(as_of_ts)
    rd = ctx.store.readings(window_start, as_of_ts, ids)
    last_obs = ctx.store.last_observed(as_of_ts)
    offs = ctx.store.offsets()

    rd_by = dict(tuple(rd.groupby("station_id"))) if len(rd) else {}
    obs_frame: dict[int, tuple[pd.DatetimeIndex, np.ndarray]] = {}
    series: dict[int, tuple[np.ndarray, np.ndarray, bool]] = {}
    for sid in ids:
        g = rd_by.get(sid)
        present = g is not None
        if g is None:
            g = pd.DataFrame({"ts": hours, "temp_f": np.nan, "qc_base": "missing"})
        g = g.set_index("ts").reindex(hours)
        temp = g["temp_f"].to_numpy(dtype="float64")
        qc = g["qc_base"].astype(object).where(g["qc_base"].notna(), "missing").to_numpy()
        series[sid] = (temp, qc, present)
        obs_frame[sid] = (hours, np.isfinite(temp))
    accepted = _accepted_offsets(offs, ids, as_of_ts)
    sigma_prior = float(ctx.model.unc_model.get("_all", {}).get("sd", np.nan))

    def detect_all(exp: pd.DataFrame, targets: list[int]) -> dict[int, tuple[list[Detection], np.ndarray]]:
        ex_by = dict(tuple(exp.groupby("station_id"))) if len(exp) else {}
        out = {}
        for sid in targets:
            try:
                temp, qc, _ = series[sid]
                e = ex_by.get(sid)
                e = e.set_index("ts").reindex(hours) if e is not None else None
                out[sid] = detect_station(sid, hours, temp, qc, e, as_of_ts, report_from, accepted.get(sid), th,
                                          sigma_prior)
            except Exception:  # one station must never stop the others
                log.exception("detection failed for station %s", sid)
        return out

    # pass A: neighbors validated by source QC only
    exp_a_raw = ctx.expected(window_start, as_of_ts, ids, with_offsets=False, valid_col="qc_base")
    res = detect_all(ctx._with_offsets(exp_a_raw), ids)
    # pass B: drop neighbor hours pass A marked invalid, re-test stations near them
    flagged = {sid for sid, (_, st) in res.items() if (st == "invalid").any()}
    if flagged:
        redo = ctx._near(ids, flagged)
        if redo:
            rd_a = rd.copy()
            rd_a["qc_status"] = rd_a["qc_base"]
            for sid in flagged:
                st = pd.Series(res[sid][1], index=hours)
                m = rd_a["station_id"] == sid
                bad = rd_a.loc[m, "ts"].map(st).eq("invalid").to_numpy()
                idx = rd_a.index[m][bad]
                rd_a.loc[idx, "qc_status"] = "invalid"
            exp_b = ctx.expected_from_readings(rd_a, window_start, as_of_ts, redo, base=exp_a_raw)
            res.update(detect_all(exp_b, redo))

    dets: list[Detection] = []
    updates = []
    for sid, (d, status) in res.items():
        dets.extend(d)
        temp, qc, present = series[sid]
        if not present:
            continue
        base = pd.Series(qc)
        sug = pd.Series(status)
        final = base.where(base.map(QC_ORDER) >= sug.map(QC_ORDER).fillna(-1), sug)
        g = rd_by[sid].set_index("ts")
        has_row = hours.isin(g.index)
        updates.append(pd.DataFrame({"station_id": sid, "ts": hours[has_row],
                                     "qc_status": final.to_numpy()[has_row]}))

    # offline + system outage
    off = offline_detections(ids, last_obs, as_of_ts, report_from, window_start, obs_frame, th)
    ongoing = [d for d in off if d.end_ts is None]
    so = th["system_outage"]
    if ids and (len(ongoing) >= int(so["min_stations"]) or len(ongoing) / len(ids) >= float(so["min_fraction"])):
        start = min(pd.Timestamp(d.start_ts) for d in ongoing)
        dets.append(Detection(0, "system_outage", th["severity"]["system_outage"], start, None,
                              int((as_of_ts - start) / pd.Timedelta(hours=1)) + 1,
                              {"stations_offline": sorted(d.station_id for d in ongoing),
                               "n_offline": len(ongoing), "n_active": len(ids),
                               "fraction": _f(len(ongoing) / len(ids))}, None))
        off = [d for d in off if d.end_ts is not None]
    dets.extend(off)

    if apply and updates:
        ctx.store.update_readings(pd.concat(updates, ignore_index=True), ["qc_status"])
    order = {t: i for i, t in enumerate(
        ["system_outage", "offline", "range", "stuck", "spike", "step", "offset", "drift", "clock_shift"])}
    dets.sort(key=lambda d: (d.station_id, order[d.type], pd.Timestamp(d.start_ts)))
    return dets
