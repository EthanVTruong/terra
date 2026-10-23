"""Rule-based checks (range, step, stuck, offline). Pure functions on one station's series."""

from __future__ import annotations

from typing import Any

import numpy as np

from .. import config


def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end] index pairs (inclusive) of consecutive True runs."""
    m = np.asarray(mask, dtype=bool)
    if not m.any():
        return []
    d = np.diff(np.concatenate([[0], m.astype(np.int8), [0]]))
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1) - 1
    return list(zip(starts.tolist(), ends.tolist(), strict=True))


def range_mask(temp: np.ndarray, th: dict[str, Any] | None = None) -> np.ndarray:
    th = th or config.thresholds()["range"]
    t = np.asarray(temp, dtype="float64")
    return np.isfinite(t) & ((t < th["min_f"]) | (t > th["max_f"]))


def step_mask(temp: np.ndarray, th: dict[str, Any] | None = None) -> np.ndarray:
    """True at hour t when |T_t - T_{t-1}| exceeds the step threshold (consecutive hours only)."""
    th = th or config.thresholds()["step"]
    t = np.asarray(temp, dtype="float64")
    d = np.abs(np.diff(t, prepend=np.nan))
    return np.isfinite(d) & (d > th["max_delta_f"])


def stuck_runs(
    temp: np.ndarray, nbr_median: np.ndarray | None, th: dict[str, Any] | None = None
) -> list[tuple[int, int, float]]:
    """Runs of identical values >= min_hours while the neighbor median moves > neighbor_change_f.

    Returns (start, end, neighbor_change_f). Without a neighbor series no run is flagged
    (a flat calm night is not evidence on its own).
    """
    th = th or config.thresholds()["stuck"]
    t = np.asarray(temp, dtype="float64")
    n = len(t)
    if n == 0 or nbr_median is None:
        return []
    nm = np.asarray(nbr_median, dtype="float64")
    res = float(th["resolution_f"])
    same = np.zeros(n, dtype=bool)
    same[1:] = np.isfinite(t[1:]) & np.isfinite(t[:-1]) & (np.abs(t[1:] - t[:-1]) <= res)
    out = []
    # a run of k "same" flags means k+1 identical hours
    for s, e in runs(same):
        start = s - 1
        hours = e - start + 1
        if hours < int(th["min_hours"]):
            continue
        seg = nm[start : e + 1]
        seg = seg[np.isfinite(seg)]
        change = float(seg.max() - seg.min()) if len(seg) >= 2 else 0.0
        if change > float(th["neighbor_change_f"]):
            out.append((start, e, change))
    return out
