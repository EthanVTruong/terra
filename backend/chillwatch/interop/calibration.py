"""Calibration test: the logger against a co-located reference thermometer (the +-0.3 C criterion).

The reference log is a CSV/JSON with ``timestamp, temp, unit`` (the DIY upload format). Each
logger sample is paired with the nearest reference reading within ``max_gap_s``; the report gives
bias, RMSE, the share within tolerance, and pass/fail on the deck's criterion.

This is separate from :mod:`.fusion`: fusion compares the orchard with distant CIMIS stations
(which *should* differ); calibration compares two sensors at the same spot (which should not).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def compare(logger: pd.DataFrame, reference: pd.DataFrame, tolerance_c: float = 0.3,
            max_gap_s: float = 120.0, lo_c: float | None = 0.0, hi_c: float | None = 10.0) -> dict[str, Any]:
    """``logger`` / ``reference``: sample frames (``received_at``, ``temp_c``); only ``ok`` logger samples.

    ``lo_c``/``hi_c`` restrict scoring to the testing plan's 0-10 C range (None = no limit).
    """
    lg = logger
    if "status" in lg:
        lg = lg[lg["status"] == "ok"]
    lg = lg[["received_at", "temp_c"]].dropna().sort_values("received_at")
    rf = reference[["received_at", "temp_c"]].dropna().sort_values("received_at").rename(columns={"temp_c": "ref_c"})
    if lg.empty or rf.empty:
        return {"pairs": 0, "passed": None}
    m = pd.merge_asof(lg, rf, on="received_at", direction="nearest",
                      tolerance=pd.Timedelta(seconds=max_gap_s)).dropna(subset=["ref_c"])
    if lo_c is not None:
        m = m[m["ref_c"] >= lo_c]
    if hi_c is not None:
        m = m[m["ref_c"] <= hi_c]
    if m.empty:
        return {"pairs": 0, "passed": None}
    d = (m["temp_c"] - m["ref_c"]).to_numpy()
    within = float(np.mean(np.abs(d) <= tolerance_c))
    bias = float(np.mean(d))
    return {
        "pairs": int(len(d)),
        "range_c": [lo_c, hi_c],
        "bias_c": round(bias, 3),
        "rmse_c": round(float(np.sqrt(np.mean(d ** 2))), 3),
        "max_abs_c": round(float(np.max(np.abs(d))), 3),
        "share_within_tolerance": round(within, 4),
        "tolerance_c": tolerance_c,
        # pass: the mean difference is inside the tolerance and 95% of pairs are too
        "passed": bool(abs(bias) <= tolerance_c and within >= 0.95),
        "bias_corrected_share_within": round(float(np.mean(np.abs(d - bias) <= tolerance_c)), 4),
        # put this in config/nodes.yaml as calibration_offset_c if the corrected share passes
        "suggested_calibration_offset_c": round(bias, 2),
    }
