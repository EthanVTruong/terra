import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from chillwatch.cli import app
from chillwatch.demo.logger import make_logger_demo
from chillwatch.interop import decode, get_node, load_nodes
from chillwatch.interop.bridge import append_jsonl
from chillwatch.interop.calibration import compare

ROOT = Path(__file__).resolve().parents[1]
SKETCH = ROOT / "firmware" / "chill_logger"


def _frames(offset_c, noise=0.05, n=600):
    t = pd.date_range("2026-12-01 08:00:30", periods=n, freq="60s", tz="UTC")
    rng = np.random.default_rng(0)
    ref = 2 + 6 * np.sin(np.arange(n) / 90)
    lg = pd.DataFrame({"received_at": t + pd.Timedelta(seconds=7), "temp_c": np.round(ref + offset_c + rng.normal(0, noise, n), 1),
                       "status": "ok"})
    return lg, pd.DataFrame({"received_at": t, "temp_c": ref})


def test_calibration_pass_and_fail():
    ok = compare(*_frames(0.05))
    assert ok["passed"] is True and ok["pairs"] > 100 and ok["range_c"] == [0.0, 10.0]
    biased = compare(*_frames(0.6))
    assert biased["passed"] is False and biased["bias_c"] == pytest.approx(0.6, abs=0.05)
    assert biased["bias_corrected_share_within"] > 0.95       # a fixed offset could be calibrated out
    assert biased["suggested_calibration_offset_c"] == pytest.approx(0.6, abs=0.05)


def test_registry_example_loads():
    nodes = load_nodes(ROOT / "config" / "nodes.example.yaml")
    n = get_node("orchard-1", ROOT / "config" / "nodes.example.yaml")
    assert nodes["orchard-1"] == n and n.shield == "monkey" and n.sample_interval_s == 60
    assert n.calibration_offset_c == 0.0
    with pytest.raises(KeyError):
        get_node("nope", ROOT / "config" / "nodes.example.yaml")


def test_cli_validate_without_reference(tmp_path):
    d = make_logger_demo(start="2025-11-01 09:00", end="2025-11-08 08:00", faults={})
    cap = tmp_path / "cap.jsonl"
    append_jsonl(d.entries, cap)
    reg = tmp_path / "nodes.yaml"
    reg.write_text(json.dumps({"nodes": [{"node_id": "orchard-1", "lat": d.node.lat, "lon": d.node.lon,
                                          "elevation_ft": d.node.elevation_ft, "sample_interval_s": 300}]}))
    out_json, out_html = tmp_path / "r.json", tmp_path / "r.html"
    res = CliRunner().invoke(app, ["logger", "validate", str(cap), "--node", "orchard-1", "--registry", str(reg),
                                   "--no-reference", "--json-out", str(out_json), "--html-out", str(out_html)])
    assert res.exit_code == 0, res.output
    assert "chill portions" in res.output
    p = json.loads(out_json.read_text())
    assert p["reference"] is None and p["chill"]["hours"]["coverage_observed"] > 0.99
    assert "Chill report" in out_html.read_text()


@pytest.mark.skipif(shutil.which("g++") is None, reason="needs g++ to build the sketch against host stubs")
def test_firmware_speaks_wire_v1(tmp_path):
    exe = tmp_path / "chill_host"
    subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(SKETCH / "host_test"),
                    str(SKETCH / "host_test" / "main.cpp"), "-o", str(exe)], check=True)
    # 2 cold hours, 1 warm hour, then 4 failed reads (error beeps after the 3rd)
    temps = ["3.0"] * 120 + ["12.0"] * 60 + ["nan"] * 4
    out = subprocess.run([str(exe)], input="\n".join(temps), capture_output=True, text=True, check=True).stdout
    lines = [x for x in out.splitlines() if x.startswith("$")]
    recs = [decode(x) for x in lines]                      # every line passes the backend's checksum
    assert [r.seq for r in recs] == list(range(1, len(temps) + 1))
    assert all(r.node_id == "orchard-1" for r in recs)
    assert recs[0].temp_c == 3.0 and recs[-1].device_status == "ERR_READ" and np.isnan(recs[-1].temp_c)
    assert np.all(np.diff([r.uptime_ms for r in recs]) >= 59_000)
    assert recs[-1].device_chill_hours == 2.0              # two 0-7.2 C hours, the warm hour not counted
    state = out.splitlines()[-1]
    assert "chill_h=2.0" in state and "tones=" in state and "lcd1=[Chill hours 2" in state
    assert int(state.split("tones=")[1].split()[0]) >= 1 + 3 * 2   # start chirp + two rounds of 3 beeps


def test_store_snapshot_keeps_offsets(tmp_path, syn_stations, syn_readings):
    from chillwatch.ingest import export_parquet
    from chillwatch.store import MemoryStore

    st = MemoryStore(syn_stations, syn_readings)
    st.add_offset({"station_id": int(syn_stations["station_id"].iloc[0]), "kind": "learned", "night_f": 1.2,
                   "day_f": -0.4, "hourly_f": json.dumps([0.1] * 24), "window_start": "2025-10-01T00:00:00Z",
                   "window_end": "2025-10-29T00:00:00Z", "created_at": "2025-10-29T00:00:00Z"})
    rp, sp = export_parquet(st, "t", tmp_path)
    back = MemoryStore.from_parquet(rp, sp, tmp_path / "offsets_t.parquet")
    assert len(back.readings()) == len(st.readings())
    o = back.offsets().iloc[0]
    assert o["night_f"] == 1.2 and json.loads(o["hourly_f"]) == [0.1] * 24


def test_cli_chill_season_in_progress(tmp_path):
    d = make_logger_demo(start="2025-11-01 09:00", end="2025-11-08 08:00", faults={})
    cap = tmp_path / "cap.jsonl"
    append_jsonl(d.entries, cap)
    reg = tmp_path / "nodes.yaml"
    reg.write_text(json.dumps({"nodes": [{"node_id": "orchard-1", "lat": d.node.lat, "lon": d.node.lon,
                                          "elevation_ft": d.node.elevation_ft, "sample_interval_s": 300}]}))
    res = CliRunner().invoke(app, ["logger", "chill", str(cap), "--node", "orchard-1", "--registry", str(reg),
                                   "--season", "2025-26", "--as-of", "2025-11-08 08:00", "--no-reference"])
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["hours"]["expected"] == 7 * 24 and out["period"]["first_local_date"] == "2025-11-01"
    assert out["hours"]["coverage_observed"] > 0.99
