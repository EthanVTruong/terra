import numpy as np
import pandas as pd
import pytest

from chillwatch.context import Context
from chillwatch.demo import faults
from chillwatch.qc.detect import classify_shift, cusum_events, detect_station, run_detection
from chillwatch.qc.rules import range_mask, step_mask, stuck_runs
from chillwatch.store import MemoryStore
from conftest import utc

AS_OF = utc("2025-11-10 08:00")  # end of Nov 9 PST


def ctx_for(stations, readings, model):
    return Context(MemoryStore(stations, readings), model)


def types_for(dets, sid):
    return {d.type for d in dets if d.station_id == sid}


# ---------------------------------------------------------------- pure rules
def test_range_and_step_rules():
    t = np.array([50, 51, 130, 52, 20, -20.0])
    assert range_mask(t).tolist() == [False, False, True, False, False, True]
    assert step_mask(np.array([50, 51, 75, 74.0])).tolist() == [False, False, True, False]


def test_stuck_needs_moving_neighbors():
    t = np.r_[np.linspace(40, 45, 5), np.full(8, 44.0), np.linspace(44, 50, 5)]
    moving = np.r_[np.linspace(40, 45, 5), np.linspace(44, 38, 8), np.linspace(38, 50, 5)]
    calm = np.r_[np.linspace(40, 45, 5), np.full(8, 44.2), np.linspace(44, 50, 5)]
    assert len(stuck_runs(t, moving)) == 1
    assert stuck_runs(t, calm) == []  # calm night: neighbors flat too -> no alarm
    assert stuck_runs(t, None) == []


def test_cusum_onset_within_tolerance():
    rng = np.random.default_rng(0)
    r = rng.normal(0, 1, 400)
    r[200:] += 4.0
    ev = cusum_events(r / 1.0, r, 1.0, 0.5, 8.0, 3.0)
    assert len(ev) == 1 and abs(ev[0].onset - 200) <= 6 and ev[0].alarm >= 200


def test_classify_step_vs_ramp():
    rng = np.random.default_rng(1)
    step = rng.normal(0, 0.8, 500)
    step[250:] += 4
    ramp = rng.normal(0, 0.8, 500)
    ramp[150:] += np.clip(np.arange(350) / 240, 0, 1) * 5
    assert classify_shift(step, 245, 270, 499, 6)["kind"] == "offset"
    c = classify_shift(ramp, 150, 330, 499, 6)
    assert c["kind"] == "drift" and abs(c["tau"] - 150) <= 24


# ---------------------------------------------------------------- run_detection on injected faults
def test_clean_data_has_no_rule_alarms(syn_stations, syn_readings, syn_model):
    ctx = ctx_for(syn_stations, syn_readings, syn_model)
    dets = run_detection(AS_OF, ctx=ctx)
    assert not [d for d in dets if d.type in ("offline", "system_outage", "stuck", "range", "clock_shift")]


@pytest.mark.parametrize("kind,kw,expect", [
    ("stuck", {"hours": 24}, {"stuck"}),
    ("spike", {"hours": 2, "delta_f": 22.0}, {"spike"}),
    ("offset", {"delta_f": 5.0}, {"offset", "drift"}),
])
def test_detects_injected(kind, kw, expect, syn_stations, syn_readings, syn_model):
    start = utc("2025-11-07 10:00")
    bad, labels = faults.INJECTORS[kind](syn_readings, 71, start, **kw)
    dets = run_detection(AS_OF, ctx=ctx_for(syn_stations, bad, syn_model))
    got = types_for(dets, 71)
    assert got & expect, (kind, got)
    # the faulty readings get flagged
    ctx = ctx_for(syn_stations, bad, syn_model)
    run_detection(AS_OF, ctx=ctx)
    r = ctx.store.readings(start, min(labels[0].end_ts, AS_OF), [71])
    assert (r["qc_status"] == "invalid").mean() > 0.5


def test_drift_detected_and_classified(syn_stations, syn_readings, syn_model):
    bad, _ = faults.drift(syn_readings, 71, utc("2025-10-25 09:00"), max_delta_f=8.0, days=12)
    dets = run_detection(AS_OF, ctx=ctx_for(syn_stations, bad, syn_model))
    d = [x for x in dets if x.station_id == 71 and x.type in ("drift", "offset")]
    assert d and d[0].type == "drift"
    # onset of a slow ramp is only identifiable to within a few days at ~1-2 F residual noise
    assert abs(pd.Timestamp(d[0].start_ts) - utc("2025-10-25 09:00")) < pd.Timedelta(days=3)


def test_offline_and_system_outage(syn_stations, syn_readings, syn_model):
    bad, _ = faults.outage(syn_readings, 71, utc("2025-11-07 09:00"), days=10)
    dets = run_detection(AS_OF, ctx=ctx_for(syn_stations, bad, syn_model))
    off = [d for d in dets if d.type == "offline" and d.station_id == 71]
    assert off and off[0].severity == "critical" and off[0].end_ts is None
    assert off[0].start_ts == utc("2025-11-07 09:00")
    # four stations dark at once -> one system outage detection, no per-station spam
    for sid in (6, 70, 39):
        bad, _ = faults.outage(bad, sid, utc("2025-11-08 09:00"), days=10)
    dets = run_detection(AS_OF, ctx=ctx_for(syn_stations, bad, syn_model))
    so = [d for d in dets if d.type == "system_outage"]
    assert len(so) == 1 and so[0].station_id == 0
    assert set(so[0].evidence["stations_offline"]) == {6, 39, 70, 71}
    assert not [d for d in dets if d.type == "offline" and d.end_ts is None]


def test_offline_warn_after_36h(syn_stations, syn_readings, syn_model):
    bad, _ = faults.outage(syn_readings, 71, utc("2025-11-08 09:00"), days=10)
    dets = run_detection(utc("2025-11-09 20:00"), ctx=ctx_for(syn_stations, bad, syn_model))
    assert [d.severity for d in dets if d.type == "offline"] == ["warn"]
    dets = run_detection(utc("2025-11-09 18:00"), ctx=ctx_for(syn_stations, bad, syn_model))
    assert not [d for d in dets if d.type == "offline"]


def test_range_detection(syn_stations, syn_readings, syn_model):
    bad = syn_readings.copy()
    m = (bad.station_id == 71) & (bad.ts == utc("2025-11-08 12:00"))
    bad.loc[m, "temp_f"] = 140.0
    dets = run_detection(AS_OF, ctx=ctx_for(syn_stations, bad, syn_model))
    assert "range" in types_for(dets, 71)


def test_clock_shift(syn_stations, syn_readings, syn_model):
    bad, _ = faults.clock_shift(syn_readings, 71, utc("2025-11-05 09:00"), hours=2)
    dets = run_detection(AS_OF, ctx=ctx_for(syn_stations, bad, syn_model))
    cs = [d for d in dets if d.type == "clock_shift" and d.station_id == 71]
    assert cs and cs[0].evidence["lag_hours"] == 2


def test_idempotent(syn_stations, syn_readings, syn_model):
    bad, _ = faults.stuck(syn_readings, 71, utc("2025-11-07 10:00"), hours=30)
    bad, _ = faults.offset(bad, 39, utc("2025-11-06 10:00"), delta_f=5.0)
    ctx = ctx_for(syn_stations, bad, syn_model)
    a = [d.to_dict() for d in run_detection(AS_OF, ctx=ctx)]
    b = [d.to_dict() for d in run_detection(AS_OF, ctx=ctx)]
    assert a == b and a


def test_detect_station_skips_insufficient_neighbors():
    hours = pd.date_range(utc("2025-01-01 09:00"), periods=200, freq="h")
    temp = 50 + 10 * np.sin(np.arange(200) / 24 * 2 * np.pi)
    exp = pd.DataFrame({"expected_f": temp + 10, "n_neighbors": 1, "nbr_median": temp}, index=hours)
    dets, _ = detect_station(1, hours, temp, np.full(200, "ok", dtype=object), exp, hours[-1], hours[0])
    assert not dets  # a 10 F residual, but < 2 neighbors -> residual tests skipped
