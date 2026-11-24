"""Synthetic logger capture for tests and demos (NOT real data; never present its numbers).

One station of the synthetic network plays the orchard logger: its hourly truth is sampled every
``sample_interval_s`` seconds, shifted by an orchard offset, with known faults injected so the
validation layer has something to find. The remaining stations are the reference network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..interop.contract import DeviceRecord, Node, f_to_c
from ..interop.wire import encode
from .synthetic import make_readings, make_stations

DEFAULT_FAULTS: dict[str, tuple[str, str]] = {
    "outage": ("2025-11-20 00:00", "2025-11-20 10:00"),     # host asleep: lines never captured
    "warm": ("2025-12-01 06:00", "2025-12-01 18:00"),       # +6 C, e.g. sun reaching the sensor
    "stuck": ("2025-12-05 00:00", "2025-12-05 05:00"),      # sensor repeats one value
    "err_read": ("2025-11-10 12:00", "2025-11-10 12:05"),   # DHT22 did not answer once
    "corrupt": ("2025-11-12 03:00", "2025-11-12 03:05"),    # one line garbled on the cable
    "reboot": ("2025-11-25 18:00", "2025-11-25 18:05"),     # power blip: seq and uptime restart
}


@dataclass
class LoggerDemo:
    node: Node
    entries: list[dict[str, Any]]          # bridge JSONL entries
    ref_stations: pd.DataFrame
    ref_readings: pd.DataFrame
    truth_f: pd.Series                     # hour-ending truth at the logger (before offset/faults)
    faults: dict[str, tuple[str, str]] = field(default_factory=dict)


def make_logger_demo(start: str = "2025-11-01 09:00", end: str = "2025-12-15 08:00", n_stations: int = 20,
                     truth_station: int = 70, sample_interval_s: int = 300, orchard_offset_c: float = 1.0,
                     faults: dict[str, tuple[str, str]] | None = None, seed: int = 1) -> LoggerDemo:
    faults = DEFAULT_FAULTS if faults is None else faults
    st = make_stations(n_stations, seed=0)
    r = make_readings(st, start, end, seed=11, missing_rate=0.0)
    truth = r[r["station_id"] == truth_station].set_index("ts")["temp_f"].sort_index()
    row = st[st["station_id"] == truth_station].iloc[0]
    node = Node(node_id="orchard-1", name="Synthetic orchard logger", lat=float(row.lat), lon=float(row.lon),
                elevation_ft=float(row.elevation_ft), sample_interval_s=sample_interval_s, shield="monkey")
    rng = np.random.default_rng(seed)
    times = pd.date_range(truth.index.min() - pd.Timedelta(hours=1) + pd.Timedelta(seconds=97),
                          truth.index.max(), freq=f"{sample_interval_s}s")
    temp_c = f_to_c(truth.reindex(times.ceil("h")).to_numpy()) + orchard_offset_c + rng.normal(0, 0.1, len(times))

    def inside(name: str, t: pd.Timestamp) -> bool:
        if name not in faults:
            return False
        a, b = faults[name]
        return pd.Timestamp(a, tz="UTC") <= t < pd.Timestamp(b, tz="UTC")

    entries, seq, up = [], 0, 0
    for t, v in zip(times, temp_c, strict=True):
        seq, up = seq + 1, up + sample_interval_s * 1000
        if inside("reboot", t):
            seq, up = 1, 2000
        if inside("outage", t):
            continue
        if inside("warm", t):
            v = v + 6.0
        if inside("stuck", t):
            v = 3.3
        rh = round(float(rng.uniform(70, 95)), 1)
        if inside("err_read", t):
            rec = DeviceRecord(node.node_id, seq, up, float("nan"), float("nan"), "ERR_READ", None)
        else:
            rec = DeviceRecord(node.node_id, seq, up, round(float(v), 1), rh, "OK", None)
        line = encode(rec)
        if inside("corrupt", t):
            line = line.replace(",OK,", ",0K,")   # checksum no longer matches
        entries.append({"received_at": t.isoformat(), "line": line})
    return LoggerDemo(node=node, entries=entries, ref_stations=st[st["station_id"] != truth_station].reset_index(drop=True),
                      ref_readings=r[r["station_id"] != truth_station].reset_index(drop=True), truth_f=truth,
                      faults=dict(faults))
