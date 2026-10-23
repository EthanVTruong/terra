"""Demo scenario on synthetic data: all four faults detected (slow: ~3 min)."""

import pytest

from chillwatch import config
from chillwatch.context import Context
from chillwatch.demo.scenario import run_scenario
from chillwatch.demo.synthetic import make_readings
from chillwatch.store import MemoryStore


@pytest.mark.slow
def test_demo_scenario_all_detected(syn_stations, syn_model):
    r = make_readings(syn_stations, "2025-10-01 09:00", "2025-12-15 08:00", seed=2025)
    ctx = Context(MemoryStore(syn_stations, r), syn_model)
    res = run_scenario(config.config_dir() / "scenarios" / "demo_2025_26.yaml", ctx, days=75, echo=lambda s: None)
    m = {x["type"]: x for x in res["matches"]}
    assert all(x["detected"] for x in res["matches"]), res["matches"]
    runs = {k: int(v["hours_to_detect"] // 24) + 1 for k, v in m.items()}
    # stuck: first daily run after onset
    assert runs["stuck"] == 1
    # outage: the 36 h offline rule cannot fire on run 1 for an outage that starts at 01:00 PST;
    # it fires on the first run with >= 36 h of silence (run 2)
    assert runs["outage"] <= 2
    assert runs["offset"] <= 3
    assert m["drift"]["detected_as"] in ("drift", "offset")
    # repaired series exists for the outage
    rep = res["repaired"]
    out = rep[(rep.station_id == 70) & rep.temp_f.isna()]
    assert out["is_estimated"].mean() > 0.9
