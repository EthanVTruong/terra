"""Chill models on hourly air temperature (degrees C).

* **Chill hours** (the 0-7.2 C / 32-45 F model): one hour counts when its temperature is in
  [0, 7.2] C. The prototype's on-device count uses the same rule.
* **Dynamic Model** (Fishman, Erez & Couvillon 1987; Erez et al. 1990), the model the final
  version reports in Chill Portions. Parameters are the standard ones used by chillR's
  ``Dynamic_Model`` and the UC ANR / FNRIC chill spreadsheets:
  E0 = 4153.5, E1 = 12888.8, A0 = 139500, A1 = 2.567e18, slope = 1.6, Tf = 277 K.

The Dynamic Model is a two-step process: warm-reversible intermediate product builds up, and once
it reaches a threshold a fraction ``xi`` of it converts irreversibly into a chill portion. Per
hour ``i`` with ``TK = T + 273``::

    xi  = sr / (1 + sr),  sr = exp(slope * Tf * (TK - Tf) / TK)
    xs  = (A0 / A1) * exp((E1 - E0) / TK)
    eak1 = exp(-A1 * exp(-E1 / TK))
    S_i = E_{i-1}                          if E_{i-1} < 1
          E_{i-1} * (1 - xi_{i-1})         otherwise
    E_i = xs_i - (xs_i - S_i) * eak1_i
    portion_i = E_i * xi_i                 if E_i >= 1, else 0

This is the Erez/Fishman spreadsheet form. chillR's ``chilling()`` (R/chilling.R) runs the same
recursion on the same hour's ``xs``, ``eak1`` and ``xi``, but takes hour 1 as its starting state and
credits nothing there, so ``chillR(T)[1:] == dynamic_model(T[1:])`` exactly; season totals differ
by at most the first hour. ``tests/test_chill_models.py`` checks that equivalence against a line-
by-line port of the chillR loop.

Missing hours: the Dynamic Model is stateful, so a gap cannot just be skipped. Callers fill gaps
first (ChillWatch's fused physics/ML estimate, labeled ``is_estimated``). Hours still missing
after that are held: the state carries over unchanged and nothing accumulates, which undercounts
and is reported as such.
"""

from __future__ import annotations

import numpy as np

E0, E1, A0, A1, SLOPE, TF = 4153.5, 12888.8, 139500.0, 2.567e18, 1.6, 277.0
CHILL_HOURS_LO_C, CHILL_HOURS_HI_C = 0.0, 7.2


def chill_hours(temp_c: np.ndarray, lo: float = CHILL_HOURS_LO_C, hi: float = CHILL_HOURS_HI_C) -> np.ndarray:
    """Per-hour chill-hour increments (1.0 or 0.0; NaN where the temperature is missing)."""
    t = np.asarray(temp_c, dtype="float64")
    out = ((t >= lo) & (t <= hi)).astype("float64")
    out[~np.isfinite(t)] = np.nan
    return out


def dynamic_model(temp_c: np.ndarray, e0: float = E0, e1: float = E1, a0: float = A0, a1: float = A1,
                  slope: float = SLOPE, tf: float = TF, state: float = 0.0) -> tuple[np.ndarray, float]:
    """Per-hour Chill Portion increments and the carried intermediate state.

    Each hour the intermediate product moves toward its equilibrium; when it reaches 1, the share
    ``xi`` converts to a portion and the rest carries over. The returned state is the amount carried
    into the next hour, so ``dynamic_model(b, state=dynamic_model(a)[1])`` continues ``a`` exactly.
    Hours with a missing temperature keep the state and accumulate nothing (increment NaN).
    """
    t = np.asarray(temp_c, dtype="float64")
    tk = t + 273.0
    with np.errstate(over="ignore", invalid="ignore"):
        sr = np.exp(slope * tf * (tk - tf) / tk)
        xi = sr / (1.0 + sr)
        xs = (a0 / a1) * np.exp((e1 - e0) / tk)
        eak1 = np.exp(-a1 * np.exp(-e1 / tk))
    inc = np.full(len(t), np.nan)
    s = float(state)
    for i in range(len(t)):
        if not np.isfinite(t[i]):
            continue  # hold the state across an unfilled gap
        e = xs[i] - (xs[i] - s) * eak1[i]
        if e >= 1.0:
            inc[i] = e * xi[i]
            s = e * (1.0 - xi[i])
        else:
            inc[i] = 0.0
            s = e
    return inc, s


def chill_portions(temp_c: np.ndarray) -> float:
    """Total Chill Portions over a contiguous hourly series."""
    inc, _ = dynamic_model(temp_c)
    return float(np.nansum(inc))
