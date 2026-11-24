import numpy as np
import pandas as pd
import pytest

from chillwatch.interop import Node, estimate_rho, fuse, score
from chillwatch.interop.fusion import Expectation

NODE = Node(node_id="orchard-1", lat=38.54, lon=-121.76, elevation_ft=52)


def test_inverse_variance_when_independent():
    mean, sd, w = fuse([40.0], [1.0], [45.0], [2.0])
    assert w[0] == pytest.approx(0.8)                     # 1/1 : 1/4 -> 0.8 / 0.2
    assert mean[0] == pytest.approx(41.0)
    assert sd[0] == pytest.approx(np.sqrt(1 / (1 + 1 / 4)))
    mean, sd, w = fuse([40.0], [1.5], [42.0], [1.5])
    assert w[0] == pytest.approx(0.5) and mean[0] == pytest.approx(41.0) and sd[0] == pytest.approx(1.5 / np.sqrt(2))


def test_correlated_errors_shift_weight_and_keep_variance_honest():
    _, sd0, w0 = fuse([40.0], [1.0], [45.0], [2.0], rho=0.0)
    _, sd5, w5 = fuse([40.0], [1.0], [45.0], [2.0], rho=0.5)
    assert w5[0] > w0[0] and sd5[0] > sd0[0]
    _, sd, w = fuse([40.0], [1.0], [45.0], [1.5], rho=0.9)   # optimum would be > 1; clipped to physics only
    assert w[0] == 1.0 and sd[0] == pytest.approx(1.0)


def test_one_core_or_none():
    mean, sd, w = fuse([40.0, np.nan, np.nan], [1.0, np.nan, np.nan], [np.nan, 44.0, np.nan], [np.nan, 2.0, np.nan])
    assert (mean[0], sd[0], w[0]) == (40.0, 1.0, 1.0)
    assert (mean[1], sd[1], w[1]) == (44.0, 2.0, 0.0)
    assert np.isnan(mean[2]) and np.isnan(w[2])


def test_estimate_rho():
    rng = np.random.default_rng(0)
    common = rng.normal(size=4000)
    a = common + rng.normal(size=4000)
    b = common + rng.normal(size=4000)
    assert estimate_rho(a, b) == pytest.approx(0.5, abs=0.05)
    with pytest.raises(ValueError):
        estimate_rho(a[:10], b[:10])


def _hourly_and_expectation(hours=24 * 14, offset_f=3.0, fault=None):
    ts = pd.date_range("2026-12-01 01:00", periods=hours, freq="h", tz="UTC")
    rng = np.random.default_rng(1)
    truth = 40 + 8 * np.sin(2 * np.pi * np.arange(hours) / 24)
    obs = truth + offset_f + rng.normal(0, 0.4, hours)
    if fault:
        a, b, d = fault
        obs[a:b] += d
    hourly = pd.DataFrame({"ts": ts, "temp_f": obs, "qc_source": "ok", "issues": ""})
    night = ((ts.hour + 16) % 24 < 7) | ((ts.hour + 16) % 24 >= 19)
    frame = pd.DataFrame({"ts": ts, "physics_f": truth, "physics_sd": 1.2, "ml_f": truth, "ml_sd": 1.2,
                          "expected_f": truth, "expected_sd": 0.85, "w_physics": 0.5,
                          "cores_disagree": False, "n_neighbors": 8, "night": night})
    return hourly, Expectation(frame=frame, ml_available=True, model_version="t")


def test_steady_orchard_offset_is_learned_not_flagged():
    hourly, exp = _hourly_and_expectation(offset_f=3.0)
    out = score(hourly, exp, NODE)
    assert (out["qc_source"] == "ok").all()
    assert out["offset_f"].iloc[-1] == pytest.approx(3.0, abs=0.3)
    assert out["offset_f"].iloc[0] == pytest.approx(3.0, abs=0.3)     # backfilled, not 0


def test_fault_flagged_and_estimate_fills_it():
    hourly, exp = _hourly_and_expectation(fault=(200, 212, 7.0))
    out = score(hourly, exp, NODE)
    bad = out.index[out["qc_source"] == "suspect"]
    assert set(range(200, 212)) <= set(bad) and len(bad) <= 13
    assert out["is_estimated"].iloc[205]
    assert out["used_temp_f"].iloc[205] == pytest.approx(exp.frame["expected_f"].iloc[205] + 3.0, abs=0.6)


def test_core_disagreement_withholds_judgment():
    hourly, exp = _hourly_and_expectation(fault=(200, 212, 7.0))
    exp.frame.loc[195:215, "cores_disagree"] = True
    out = score(hourly, exp, NODE)
    assert (out.loc[200:211, "qc_source"] == "ok").all()
    assert out.loc[200:211, "issues"].str.contains("reference_disagrees").all()
