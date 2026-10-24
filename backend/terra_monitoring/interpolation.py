"""Interpolate a weather variable at any point, with uncertainty measured from the data itself.

No model is trained. The expected value at a point is an inverse-distance-weighted
median of nearby reference stations, so one faulty station cannot drag its
neighbours' expectations. Its uncertainty comes from leave-one-out errors: each
reference station predicted from the others over recent hours. A station's usual
offset from its interpolated value (a humid Delta site, say) is its bias; the
robust spread of the remaining errors sets the band. A reading outside
``band_multiplier`` spreads is unusual; outside ``extreme_multiplier`` spreads it is
extreme. A per-variable floor keeps a calm or dark hour from producing a zero-width band,
and a cautious prior spread applies until there is enough history to measure one.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import median

POLICY_VERSION = "terra-interpolation-policy-v1"
POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "terra" / "interpolation-policy-v1.json"
MAD_TO_SD = 1.4826  # Median absolute deviation to standard deviation for normal errors.


def load_policy() -> dict:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    if policy.get("version") != POLICY_VERSION:
        raise ValueError("Unsupported interpolation policy")
    return policy


def distance_km(a: dict, b: dict) -> float:
    lat1, lat2 = math.radians(a["lat"]), math.radians(b["lat"])
    dlat, dlon = lat2 - lat1, math.radians(b["lon"] - a["lon"])
    chord = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(min(1.0, max(0.0, chord))))


def interpolate(point: dict, peers: list[tuple[dict, float]], policy: dict) -> float | None:
    """Inverse-distance-weighted median of (location, value) peers in range, or None without enough peers."""
    near = sorted((value, max(distance_km(point, where), policy["minimum_distance_km"]) ** -policy["power"])
                  for where, value in peers if distance_km(point, where) <= policy["maximum_peer_distance_km"])
    if len(near) < policy["minimum_peers"]:
        return None
    half, running = sum(w for _, w in near) / 2, 0.0
    for value, weight in near:
        running += weight
        if running >= half:
            return value
    return near[-1][0]


def bias(residuals: list[float], policy: dict) -> float:
    return median(residuals) if len(residuals) >= policy["sustained_readings"] else 0.0


def spread(variable: str, residuals: list[float], policy: dict, hours: int) -> float:
    # Too little history to measure: use the cautious prior rather than an overconfident floor.
    if hours < policy["minimum_history_hours"] or len(residuals) < policy["minimum_residuals"]:
        return policy["spread_prior"][variable]
    center = median(residuals)
    return max(policy["spread_floor"][variable], MAD_TO_SD * median(abs(r - center) for r in residuals))


def level(departure: float, width: float, policy: dict) -> str:
    size = abs(departure)
    return ("strong" if size > policy["extreme_multiplier"] * width
            else "moderate" if size > policy["band_multiplier"] * width else "none")
