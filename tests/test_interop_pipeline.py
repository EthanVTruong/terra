import numpy as np
import pandas as pd
import pytest

from chillwatch.chill.models import chill_hours, chill_portions
from chillwatch.context import Context
from chillwatch.demo.logger import make_logger_demo
from chillwatch.interop import run
from chillwatch.interop.contract import f_to_c
from chillwatch.interop.readers import decode_capture
from chillwatch.interop.report import render
from chillwatch.qc.model import idw_only_bundle
from chillwatch.store import MemoryStore


def _flagged_outside(h, faults):
    flagged = h["issues"].str.contains("residual")
    inside = np.zeros(len(h), dtype=bool)
    for a, b in faults.values():
        inside |= ((h["ts"] > pd.Timestamp(a, tz="UTC")) & (h["ts"] <= pd.Timestamp(b, tz="UTC") + pd.Timedelta(hours=1))).to_numpy()
    return int((flagged & ~inside).sum())


def _in(h, window):
    a, b = (pd.Timestamp(x, tz="UTC") for x in window)
    return h[(h["ts"] > a) & (h["ts"] <= b)]


@pytest.fixture(scope="module")
def physics_run():
    d = make_logger_demo()
    s, r = decode_capture(d.entries)
    ctx = Context(MemoryStore(d.ref_stations, d.ref_readings), model=idw_only_bundle())
    return d, run(s, r, d.node, ctx, s["received_at"].min(), s["received_at"].max(), target_portions=30.0)


def test_every_injected_fault_is_caught(physics_run):
    d, res = physics_run
    h, p = res.hourly, res.packet()
    assert p["issues"]["samples"]["bad_checksum"] == 1
    assert p["issues"]["samples"]["sensor_error"] == 1
    assert len(p["stream"]["reboots"]) == 1 and p["stream"]["lost_samples"] == 121
    warm = _in(h, d.faults["warm"])
    assert (warm["qc_source"] == "suspect").all() and warm["issues"].str.contains("residual").all()
    assert _in(h, d.faults["stuck"])["issues"].str.contains("stuck").all()
    outage = [g for g in p["chill"]["gaps"] if g["status"] == "missing"]
    assert outage and outage[0]["hours"] == 10
    assert _flagged_outside(h, d.faults) == 0


def test_chill_totals_track_truth(physics_run):
    d, res = physics_run
    c = res.chill
    truth_c = f_to_c(d.truth_f.to_numpy()) + 1.0             # the orchard runs 1 C warmer than its site
    true_hours = float(np.nansum(chill_hours(truth_c)))
    true_cp = chill_portions(truth_c)
    assert c["complete"] is True and c["hours"]["unfilled"] == 0
    assert abs(c["chill_hours"]["with_estimates"] - true_hours) <= 0.02 * true_hours
    assert abs(c["chill_portions"]["with_estimates"] - true_cp) <= 0.05 * true_cp
    # gap-filling closes most of the shortfall of counting measured hours only
    assert abs(c["chill_portions"]["with_estimates"] - true_cp) < abs(c["chill_portions"]["observed_only"] - true_cp)
    assert c["chill_portions"]["p10"] <= c["chill_portions"]["with_estimates"] <= c["chill_portions"]["p90"]


def test_report_shows_total_and_gaps(physics_run):
    _, res = physics_run
    page = render(res.packet(), res.hourly)
    assert page.startswith("<!doctype html>") and "<script" not in page
    for text in ("Chill Portions (Dynamic Model)", "Data gaps", "missing", "physics only", "<svg"):
        assert text in page


def test_ml_core_is_fused_by_uncertainty(syn_stations, syn_model):
    faults = {"warm": ("2025-11-15 06:00", "2025-11-15 18:00")}
    d = make_logger_demo(start="2025-10-01 09:00", end="2025-11-30 08:00", faults=faults)
    s, r = decode_capture(d.entries)
    ctx = Context(MemoryStore(d.ref_stations, d.ref_readings), model=syn_model)
    res = run(s, r, d.node, ctx, s["received_at"].min(), s["received_at"].max())
    ref = res.packet()["reference"]
    assert ref["ml_available"] and ref["method"].startswith("physics+ml")
    w = res.hourly["w_physics"].dropna()
    assert 0.1 < w.mean() < 0.9 and w.std() > 0          # weights follow each hour's uncertainties
    both = res.hourly.dropna(subset=["physics_sd", "ml_sd"])
    assert (both["expected_sd"] <= both[["physics_sd", "ml_sd"]].min(axis=1) + 1e-9).all()
    assert _in(res.hourly, faults["warm"])["issues"].str.contains("residual").all()
    assert _flagged_outside(res.hourly, faults) <= 2


def test_calibration_offset_is_applied():
    from dataclasses import replace

    d = make_logger_demo(start="2025-11-01 09:00", end="2025-11-04 08:00", faults={})
    s, r = decode_capture(d.entries)
    raw = run(s, r, d.node, None, s["received_at"].min(), s["received_at"].max())
    fixed = run(s, r, replace(d.node, calibration_offset_c=0.5), None, s["received_at"].min(), s["received_at"].max())
    diff = (raw.hourly["temp_f"] - fixed.hourly["temp_f"]).dropna()
    assert np.allclose(diff, 0.9) and fixed.packet()["node"]["calibration_offset_c"] == 0.5
