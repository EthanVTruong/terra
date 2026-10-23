import numpy as np
import pandas as pd

from chillwatch.demo import faults
from conftest import utc

S = utc("2025-10-10 09:00")


def sel(df, sid, a, b):
    return df[(df.station_id == sid) & (df.ts >= a) & (df.ts <= b)]


def test_outage(syn_readings):
    out, lab = faults.outage(syn_readings, 70, S, days=2)
    assert lab[0].end_ts == S + pd.Timedelta(hours=47)
    seg = sel(out, 70, lab[0].start_ts, lab[0].end_ts)
    assert len(seg) == 48 and seg.temp_f.isna().all() and (seg.qc_base == "missing").all()
    assert syn_readings.temp_f.notna().all()  # input untouched


def test_stuck(syn_readings):
    out, lab = faults.stuck(syn_readings, 70, S, hours=10)
    seg = sel(out, 70, lab[0].start_ts, lab[0].end_ts)
    assert len(seg) == 10 and seg.temp_f.nunique() == 1


def test_spike(syn_readings):
    out, lab = faults.spike(syn_readings, 70, S, hours=2, delta_f=-20)
    d = sel(out, 70, S, S + pd.Timedelta(hours=1)).temp_f.to_numpy() - sel(syn_readings, 70, S, S + pd.Timedelta(hours=1)).temp_f.to_numpy()
    assert np.allclose(d, -20)


def test_drift_ramp(syn_readings):
    out, lab = faults.drift(syn_readings, 70, S, max_delta_f=5, days=2)
    d = sel(out, 70, lab[0].start_ts, lab[0].end_ts).temp_f.to_numpy() - sel(syn_readings, 70, lab[0].start_ts, lab[0].end_ts).temp_f.to_numpy()
    assert d[0] == 0 and np.isclose(d[-1], 5) and np.all(np.diff(d) > 0)


def test_offset_persistent(syn_readings):
    out, lab = faults.offset(syn_readings, 70, S, delta_f=3)
    assert lab[0].end_ts == syn_readings[syn_readings.station_id == 70].ts.max()
    d = sel(out, 70, S, lab[0].end_ts).temp_f.to_numpy() - sel(syn_readings, 70, S, lab[0].end_ts).temp_f.to_numpy()
    assert np.allclose(d, 3)


def test_clock_shift(syn_readings):
    out, lab = faults.clock_shift(syn_readings, 70, S, hours=2, duration_hours=24)
    o = sel(out, 70, S, S + pd.Timedelta(hours=23)).temp_f.to_numpy()
    t = sel(syn_readings, 70, S - pd.Timedelta(hours=2), S + pd.Timedelta(hours=21)).temp_f.to_numpy()
    assert np.allclose(o, t)
    assert lab[0].params["hours"] == 2


def test_random_faults_seeded_and_non_overlapping():
    a = faults.random_faults([1, 2, 3, 4, 5], utc("2023-10-01 09:00"), utc("2024-04-01 08:00"), 20, seed=42)
    b = faults.random_faults([1, 2, 3, 4, 5], utc("2023-10-01 09:00"), utc("2024-04-01 08:00"), 20, seed=42)
    assert a == b and len(a) == 20
    assert {f["type"] for f in a} == {"outage", "stuck", "spike", "drift", "offset", "clock_shift"}


def test_hill_station(syn_stations):
    sid = faults.pick_hill_station(syn_stations)
    assert syn_stations.set_index("station_id").at[sid, "elevation_ft"] > 1000
