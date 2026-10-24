"""Strict, finite configuration for extreme or sustained residual notes only.

Missing station override fields inherit global settings. Explicit null thresholds
inherit the station's calibrated value, including when a global override exists.
This module resolves configuration; it does not classify readings or fit bounds.
"""
from __future__ import annotations

import json
import math
from numbers import Integral, Real
from pathlib import Path

VERSION = "terra-temporal-v1"
STATIONS = (6, 226, 139, 212, 235)
THRESHOLDS = ("sustained_low_residual_f", "sustained_high_residual_f", "extreme_abs_residual_f")
CALIBRATED_FIELDS = {"sustained_low_residual_f": "typical_low_residual_f",
                     "sustained_high_residual_f": "typical_high_residual_f",
                     "extreme_abs_residual_f": "extreme_abs_residual_f"}


def _keys(value, allowed, required, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    if any(not isinstance(key, str) or key not in allowed for key in value):
        raise ValueError(f"Unknown {label} field")
    if not set(required).issubset(value):
        raise ValueError(f"Missing required {label} field")


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{label} must be a finite number")
    try:
        number = float(value)
    except (ValueError, OverflowError):
        raise ValueError(f"{label} must be a finite number") from None
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def _thresholds(value):
    return {key: None if item is None else _number(item, key) for key, item in value.items() if key in THRESHOLDS}


def _check_order(thresholds):
    low, high, outer = (thresholds.get(key) for key in THRESHOLDS)
    if low is not None and high is not None and low >= high:
        raise ValueError("Sustained residual bounds must be ordered")
    if outer is not None:
        if outer <= 0:
            raise ValueError("Extreme absolute residual must be positive")
        if any(edge is not None and abs(edge) > outer for edge in (low, high)):
            raise ValueError("Extreme bound must enclose sustained bounds")


def default_policy(sustained_readings=3):
    return validate({"version": VERSION, "variables": {"temperature_f": {
        "units": "F", "sustained_readings": sustained_readings, "extreme_readings": 1,
        **{field: None for field in THRESHOLDS}}}, "station_overrides": {}})


def validate(payload):
    """Return a fresh normalized JSON-safe policy, rejecting unsupported behavior."""
    _keys(payload, {"version", "variables", "station_overrides"}, {"version", "variables"}, "policy")
    if payload["version"] != VERSION:
        raise ValueError("Unsupported temporal policy version")
    variables = payload["variables"]
    _keys(variables, {"temperature_f"}, {"temperature_f"}, "variables")
    temperature = variables["temperature_f"]
    _keys(temperature, {"units", "sustained_readings", "extreme_readings", *THRESHOLDS},
          {"units", "sustained_readings", "extreme_readings"}, "temperature policy")
    if temperature["units"] != "F":
        raise ValueError("Temperature residual policy requires Fahrenheit units")
    readings = temperature["sustained_readings"]
    if isinstance(readings, bool) or not isinstance(readings, Integral) or readings < 2:
        raise ValueError("Sustained readings must be an integer of at least two")
    extreme = temperature["extreme_readings"]
    if isinstance(extreme, bool) or not isinstance(extreme, Integral) or extreme != 1:
        raise ValueError("Extreme departures require exactly one reading")
    global_values = {field: None for field in THRESHOLDS}
    global_values.update(_thresholds(temperature))
    _check_order(global_values)
    overrides = payload.get("station_overrides", {})
    if not isinstance(overrides, dict):
        raise ValueError("Station overrides must be an object")
    normalized_overrides = {}
    for station, values in overrides.items():
        if not isinstance(station, str) or station not in {str(item) for item in STATIONS}:
            raise ValueError("Unknown station override")
        _keys(values, set(THRESHOLDS), set(), "station override")
        normalized = _thresholds(values)
        _check_order({**global_values, **normalized})
        normalized_overrides[station] = normalized
    return {"version": VERSION, "variables": {"temperature_f": {
        "units": "F", "sustained_readings": int(readings), "extreme_readings": 1, **global_values}},
        "station_overrides": {station: normalized_overrides[station]
                              for station in sorted(normalized_overrides, key=int)}}


def resolve(payload, station_id, calibrated_bounds):
    """Resolve one station, preserving the calibrated typical display band's enclosure."""
    if isinstance(station_id, bool) or not isinstance(station_id, Integral) or station_id not in STATIONS:
        raise ValueError("Resolve requires a known integer station ID")
    policy = validate(payload)
    if not isinstance(calibrated_bounds, dict):
        raise ValueError("Calibrated residual bounds must be an object")
    try:
        calibrated = {field: _number(calibrated_bounds[key], key) for field, key in CALIBRATED_FIELDS.items()}
    except KeyError:
        raise ValueError("Missing calibrated residual bound") from None
    _check_order(calibrated)
    variable = policy["variables"]["temperature_f"]
    selected = {field: variable[field] for field in THRESHOLDS}
    selected.update(policy["station_overrides"].get(str(station_id), {}))
    selected = {field: calibrated[field] if value is None else value for field, value in selected.items()}
    _check_order(selected)
    if selected["extreme_abs_residual_f"] < max(abs(calibrated["sustained_low_residual_f"]), abs(calibrated["sustained_high_residual_f"])):
        raise ValueError("Extreme bound must also enclose the calibrated typical band")
    return {"required_readings": variable["sustained_readings"], **selected}


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate temporal policy JSON key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("Nonfinite temporal policy JSON number")


def read_policy(path):
    """Read strict UTF-8 JSON; errors never include supplied configuration contents."""
    try:
        content = Path(path).read_text(encoding="utf-8", errors="strict")
    except OSError:
        raise ValueError("Cannot read temporal policy file") from None
    except UnicodeError:
        raise ValueError("Temporal policy must be valid UTF-8 JSON") from None
    try:
        payload = json.loads(content, object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
    except json.JSONDecodeError:
        raise ValueError("Invalid temporal policy JSON") from None
    return validate(payload)
