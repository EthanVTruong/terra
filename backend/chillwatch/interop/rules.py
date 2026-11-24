"""Sample-level validation rules (pure functions on one node's samples).

Each sample gets a ``status`` (ok / suspect / invalid) and the reason codes that set it. Events
that are about the *stream* rather than a value (reboots, lost samples) are returned separately
so the report can show them without discarding good readings.

Thresholds live in ``config/thresholds.yaml`` under ``logger:``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .. import config
from .contract import INVALIDATING, Issue, Node


@dataclass
class StreamEvents:
    reboots: list[dict[str, Any]] = field(default_factory=list)
    seq_gaps: list[dict[str, Any]] = field(default_factory=list)
    clock_jumps: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"reboots": self.reboots, "seq_gaps": self.seq_gaps, "clock_jumps": self.clock_jumps,
                "lost_samples": int(sum(g["missing"] for g in self.seq_gaps))}


def logger_thresholds() -> dict[str, Any]:
    return dict(config.thresholds()["logger"])


def _add(issues: list[set[str]], mask: np.ndarray, issue: Issue) -> None:
    for i in np.flatnonzero(mask):
        issues[i].add(str(issue))


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    out, start = [], None
    for i, m in enumerate(mask):
        if m and start is None:
            start = i
        elif not m and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(mask) - 1))
    return out


def validate_samples(samples: pd.DataFrame, node: Node,
                     th: dict[str, Any] | None = None) -> tuple[pd.DataFrame, StreamEvents]:
    """Return ``samples`` sorted by ``received_at`` with ``status`` and ``issues`` columns, and events."""
    th = th or logger_thresholds()
    df = samples.sort_values(["received_at", "seq"], kind="stable", na_position="last").reset_index(drop=True)
    n = len(df)
    issues: list[set[str]] = [set() for _ in range(n)]
    ev = StreamEvents()
    if n == 0:
        df["status"], df["issues"] = pd.Series(dtype=object), pd.Series(dtype=object)
        return df, ev

    temp = df["temp_c"].to_numpy(dtype="float64")
    rh = df["rh_pct"].to_numpy(dtype="float64")
    at = df["received_at"]

    _add(issues, (df["node_id"].astype(str) != str(node.node_id)).to_numpy(), Issue.UNKNOWN_NODE)
    _add(issues, ((df["device_status"].astype(str) != "OK") | np.isnan(temp)).to_numpy(), Issue.SENSOR_ERROR)
    lo, hi = th["range_c"]
    _add(issues, np.isfinite(temp) & ((temp < lo) | (temp > hi)), Issue.OUT_OF_RANGE)
    rlo, rhi = th["rh_range_pct"]
    _add(issues, np.isfinite(rh) & ((rh < rlo) | (rh > rhi)), Issue.RH_OUT_OF_RANGE)

    # duplicates: the same line captured twice within dup_window_s (a re-read or a capture file
    # appended twice). After a reboot the counter repeats, so seq alone is not a duplicate key.
    t = (at - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds().to_numpy()
    has_seq = (df["seq"].notna() & df["uptime_ms"].notna()).to_numpy()
    key = np.where(has_seq, df["raw"].astype(str), at.astype(str) + "|" + df["temp_c"].astype(str))
    dup = np.zeros(n, dtype=bool)
    last_seen: dict[str, float] = {}
    for i in range(n):
        k = key[i]
        if k in last_seen and t[i] - last_seen[k] <= float(th["dup_window_s"]):
            dup[i] = True
        else:
            last_seen[k] = t[i]
    _add(issues, dup, Issue.DUPLICATE)

    # stream continuity: reboots, lost samples and host clock jumps (serial captures only)
    keep = ~dup
    seq = df["seq"].to_numpy(dtype="float64", na_value=np.nan)
    up = df["uptime_ms"].to_numpy(dtype="float64", na_value=np.nan)
    prev = None
    wrap = 2.0 ** 32
    for i in range(n):
        if not keep[i] or not np.isfinite(seq[i]):
            continue
        if prev is not None:
            ds = seq[i] - seq[prev]
            if ds <= 0:  # the counter restarted: the Uno rebooted (power loss, reset, upload)
                ev.reboots.append({"at": at.iloc[i].isoformat(), "seq_before": int(seq[prev]), "seq_after": int(seq[i])})
                _add(issues, np.arange(n) == i, Issue.REBOOT)
            else:
                if ds > 1:
                    ev.seq_gaps.append({"after": at.iloc[prev].isoformat(), "before": at.iloc[i].isoformat(),
                                        "missing": int(ds - 1)})
                if np.isfinite(up[i]) and np.isfinite(up[prev]):
                    d_up = (up[i] - up[prev]) % wrap / 1000.0   # millis() wraps after ~49.7 days
                    d_at = t[i] - t[prev]
                    tol = max(float(th["clock_tolerance_s"]), float(th["clock_tolerance_frac"]) * d_up)
                    if abs(d_at - d_up) > tol:
                        ev.clock_jumps.append({"at": at.iloc[i].isoformat(), "host_s": round(d_at, 1),
                                               "device_s": round(d_up, 1)})
                        _add(issues, np.arange(n) == i, Issue.CLOCK_JUMP)
        prev = i

    # value checks on the usable series
    usable = keep & np.isfinite(temp) & ~np.array([bool(s & {str(x) for x in INVALIDATING}) for s in issues])
    idx = np.flatnonzero(usable)
    if len(idx) > 1:
        dt_min = np.diff(t[idx]) / 60.0
        dT = np.abs(np.diff(temp[idx]))
        step = (dt_min <= float(th["step_window_min"])) & (dT > float(th["max_step_c"]))
        # flag the sample that jumped (both ends of a one-sample spike end up flagged)
        _add(issues, np.isin(np.arange(n), idx[1:][step]), Issue.STEP)
        same = np.r_[False, np.abs(np.diff(temp[idx])) <= float(th["stuck_resolution_c"])]
        for a, b in _runs(same):
            first = idx[a - 1]
            last = idx[b]
            if (t[last] - t[first]) / 3600.0 >= float(th["stuck_hours"]):
                _add(issues, np.isin(np.arange(n), idx[a - 1:b + 1]), Issue.STUCK)

    inval = {str(x) for x in INVALIDATING}
    df["issues"] = [",".join(sorted(s)) for s in issues]
    df["status"] = [
        "invalid" if s & inval else ("suspect" if s - {str(Issue.REBOOT), str(Issue.RH_OUT_OF_RANGE)} else "ok")
        for s in issues
    ]
    return df, ev
