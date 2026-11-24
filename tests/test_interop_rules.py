import math

import numpy as np
import pandas as pd
import pytest

from chillwatch.interop import DeviceRecord, Issue, Node, encode, gaps, samples_to_hourly, validate_samples
from chillwatch.interop.readers import decode_capture

NODE = Node(node_id="orchard-1", lat=38.54, lon=-121.76, elevation_ft=52, sample_interval_s=60)
T0 = pd.Timestamp("2026-12-01 08:00:30", tz="UTC")


def stream(temps, *, start=T0, step_s=60, seq0=1, up0=60_000, node="orchard-1"):
    """Bridge entries for one sample per minute; ``temps`` may hold NaN for a failed read."""
    out = []
    for i, v in enumerate(temps):
        bad = isinstance(v, float) and math.isnan(v)
        rec = DeviceRecord(node, seq0 + i, up0 + i * step_s * 1000, v, math.nan if bad else 80.0,
                           "ERR_READ" if bad else "OK")
        out.append({"received_at": (start + pd.Timedelta(seconds=i * step_s)).isoformat(), "line": encode(rec)})
    return out


def run(entries):
    s, _ = decode_capture(entries)
    return validate_samples(s, NODE)


def wobble(n, base=4.0):
    return list(np.round(base + 0.5 * np.sin(np.arange(n) / 7.0), 1))


def issues_of(df, i):
    return set(filter(None, df["issues"].iloc[i].split(",")))


def test_clean_stream_is_all_ok():
    df, ev = run(stream(wobble(180)))
    assert (df["status"] == "ok").all()
    assert ev.to_dict() == {"reboots": [], "seq_gaps": [], "clock_jumps": [], "lost_samples": 0}


def test_value_rules():
    temps = wobble(30)
    temps[3] = math.nan          # sensor did not answer
    temps[10] = 61.0             # beyond the DHT22's range
    df, _ = run(stream(temps))
    assert issues_of(df, 3) == {Issue.SENSOR_ERROR} and df["status"].iloc[3] == "invalid"
    assert Issue.OUT_OF_RANGE in issues_of(df, 10) and df["status"].iloc[10] == "invalid"


def test_unknown_node_is_invalid():
    df, _ = run(stream(wobble(3), node="orchard-9"))
    assert (df["status"] == "invalid").all()
    assert all(Issue.UNKNOWN_NODE in issues_of(df, i) for i in range(3))


def test_duplicate_line_but_not_after_reboot():
    e = stream(wobble(20))
    dup = dict(e[5], received_at=(T0 + pd.Timedelta(seconds=5 * 60 + 2)).isoformat())
    # after a reboot the counter restarts at 1 - same seq as the first sample, but a new line
    after = stream(wobble(5), start=T0 + pd.Timedelta(minutes=20), seq0=1, up0=2_000)
    df, ev = run(e + [dup] + after)
    assert (df["issues"].str.contains(Issue.DUPLICATE)).sum() == 1
    assert len(ev.reboots) == 1
    assert (df["status"] == "invalid").sum() == 1


def test_seq_gap_and_clock_jump():
    a = stream(wobble(10))
    b = stream(wobble(10), start=T0 + pd.Timedelta(minutes=15), seq0=16, up0=60_000 + 15 * 60_000)
    df, ev = run(a + b)
    assert ev.seq_gaps[0]["missing"] == 5 and not ev.clock_jumps
    # host clock jumps forward two hours while the Uno's uptime says one minute passed
    c = stream(wobble(10))
    for x in c[6:]:
        x["received_at"] = (pd.Timestamp(x["received_at"]) + pd.Timedelta(hours=2)).isoformat()
    df, ev = run(c)
    assert len(ev.clock_jumps) == 1 and Issue.CLOCK_JUMP in issues_of(df, 6)
    assert df["status"].iloc[6] == "suspect"


def test_step_and_stuck():
    temps = wobble(60)
    temps[30] = temps[29] + 9.0                     # one-sample spike
    df, _ = run(stream(temps))
    assert Issue.STEP in issues_of(df, 30)
    df, _ = run(stream(wobble(30) + [3.3] * 200))   # frozen for over 3 hours
    stuck = df["issues"].str.contains(Issue.STUCK)
    assert stuck.sum() >= 180 and not stuck.iloc[:25].any()


def test_hourly_coverage_and_gaps():
    # hour 1 full; hour 2 half; hour 3 nothing; hour 4 only failed reads
    full = stream(wobble(60))
    half = stream(wobble(30), start=T0 + pd.Timedelta(hours=1), seq0=61, up0=61 * 60_000)
    bad = stream([math.nan] * 60, start=T0 + pd.Timedelta(hours=3), seq0=181, up0=181 * 60_000)
    df, _ = run(full + half + bad)
    h = samples_to_hourly(df, NODE, "2026-12-01 09:00", "2026-12-01 12:00")
    assert list(h["qc_source"]) == ["ok", "suspect", "missing", "invalid"]
    assert "low_coverage" in h["issues"].iloc[1] and "no_samples" in h["issues"].iloc[2]
    assert h["coverage"].iloc[0] == pytest.approx(1.0)
    assert h["temp_f"].iloc[0] == pytest.approx(np.mean(wobble(60)) * 9 / 5 + 32, abs=0.01)
    g = gaps(h)
    assert [(x["hours"], x["status"]) for x in g] == [(2, "invalid/missing")]   # adjacent runs merge
