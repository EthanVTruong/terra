"""The interoperability contract: one record format shared by the logger, the host and the backend.

Three layers meet here:

* **Device record (wire v1)** - what the Arduino logger prints over serial, one line per sample.
  See :mod:`chillwatch.interop.wire` for the exact text format and checksum.
* **Sample frame** - every reader (serial capture, DIY CSV upload, replay file) turns its input
  into this table, so validation never cares where a sample came from.
* **Canonical hourly frame** - the existing ChillWatch hourly schema (``sources.base``): UTC
  hour-ending ``ts``, ``temp_f`` and a ``qc_source`` status. The chill models, the physics and the
  ML core all read this one shape.

Units: the device speaks degrees Celsius (the DHT22's native unit); the backend stores degrees
Fahrenheit like the rest of ChillWatch. Conversion happens once, in :func:`samples_to_hourly`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import pandas as pd

WIRE_VERSION = "CM1"

# One row per received sample, whatever the source.
SAMPLE_COLUMNS = [
    "source",        # "logger" (serial capture), "diy" (CSV/JSON upload), "replay"
    "node_id",       # logger / station identifier from the registry
    "received_at",   # UTC time the host stamped the sample (the Uno has no clock)
    "seq",           # device sample counter; restarts at 0 after a reboot (nullable)
    "uptime_ms",     # device millis() at the sample; wraps after ~49.7 days (nullable)
    "temp_c",        # air temperature, degrees C (NaN when the sensor read failed)
    "rh_pct",        # relative humidity, percent (NaN when not reported)
    "device_status", # "OK" or an error code reported by the device ("ERR_READ", ...)
    "raw",           # the original line / row, kept for provenance
]

# Per-hour statuses, ordered from best to worst (matches normalize.QC_ORDER).
HOURLY_STATUSES = ("ok", "suspect", "invalid", "missing")


class Issue(StrEnum):
    """Reason codes attached to samples and hours. Stable strings: reports and tests use them."""

    BAD_CHECKSUM = "bad_checksum"
    BAD_FORMAT = "bad_format"
    WRONG_VERSION = "wrong_version"
    UNKNOWN_NODE = "unknown_node"
    SENSOR_ERROR = "sensor_error"
    OUT_OF_RANGE = "out_of_range"
    RH_OUT_OF_RANGE = "rh_out_of_range"
    DUPLICATE = "duplicate"
    STEP = "step"
    STUCK = "stuck"
    REBOOT = "reboot"
    SEQ_GAP = "seq_gap"
    CLOCK_JUMP = "clock_jump"
    LOW_COVERAGE = "low_coverage"
    NO_SAMPLES = "no_samples"
    RESIDUAL_EXTREME = "residual_extreme"
    RESIDUAL_PERSISTENT = "residual_persistent"
    REFERENCE_DISAGREES = "reference_disagrees"
    NO_REFERENCE = "no_reference"


# Issues that make a sample unusable; anything else only marks it suspect.
INVALIDATING = frozenset({
    Issue.BAD_CHECKSUM, Issue.BAD_FORMAT, Issue.WRONG_VERSION, Issue.UNKNOWN_NODE,
    Issue.SENSOR_ERROR, Issue.OUT_OF_RANGE, Issue.DUPLICATE,
})


@dataclass(frozen=True)
class DeviceRecord:
    """One decoded wire-v1 line."""

    node_id: str
    seq: int
    uptime_ms: int
    temp_c: float
    rh_pct: float
    device_status: str
    device_chill_hours: float | None = None


@dataclass(frozen=True)
class Node:
    """A logger's registry entry. Location drives the physics/ML expected value."""

    node_id: str
    lat: float
    lon: float
    elevation_ft: float
    name: str = ""
    tz: str = "America/Los_Angeles"
    sensor: str = "DHT22"
    # 1-sigma sensor error in degrees F, added to the reference uncertainty when scoring.
    # DHT22 datasheet accuracy is +-0.5 C (0.9 F); treated as ~2 sigma -> 0.45 F.
    sensor_sd_f: float = 0.45
    sample_interval_s: int = 60
    shield: str = ""
    # Subtracted from every reading before validation; set it from ``chillwatch logger calibrate``
    # (its bias_c) after the co-located reference test. 0 = uncorrected.
    calibration_offset_c: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Node:
        known = {f for f in cls.__dataclass_fields__ if f != "extra"}
        return cls(
            node_id=str(d["node_id"] if "node_id" in d else d["id"]),
            lat=float(d["lat"]),
            lon=float(d["lon"]),
            elevation_ft=float(d.get("elevation_ft", 0.0)),
            **{k: d[k] for k in known - {"node_id", "lat", "lon", "elevation_ft"} if k in d},
            extra={k: v for k, v in d.items() if k not in known and k != "id"},
        )


def empty_samples() -> pd.DataFrame:
    df = pd.DataFrame({c: pd.Series(dtype=object) for c in SAMPLE_COLUMNS})
    df["received_at"] = pd.to_datetime(df["received_at"], utc=True)
    for c in ("temp_c", "rh_pct"):
        df[c] = df[c].astype("float64")
    for c in ("seq", "uptime_ms"):
        df[c] = df[c].astype("Int64")
    return df


def coerce_samples(df: pd.DataFrame) -> pd.DataFrame:
    """Give any reader's output exactly the sample columns and dtypes."""
    out = df.copy()
    for c in SAMPLE_COLUMNS:
        if c not in out:
            out[c] = pd.NA
    out["received_at"] = pd.to_datetime(out["received_at"], utc=True, errors="coerce")
    for c in ("temp_c", "rh_pct"):
        out[c] = pd.to_numeric(out[c], errors="coerce").astype("float64")
    for c in ("seq", "uptime_ms"):
        out[c] = pd.to_numeric(out[c], errors="coerce").astype("Int64")
    out["device_status"] = out["device_status"].fillna("OK").astype(str)
    out["node_id"] = out["node_id"].astype(str)
    return out[SAMPLE_COLUMNS].reset_index(drop=True)


def c_to_f(c: Any) -> Any:
    return c * 9.0 / 5.0 + 32.0


def f_to_c(f: Any) -> Any:
    return (f - 32.0) * 5.0 / 9.0
