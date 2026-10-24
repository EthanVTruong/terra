"""Frozen-checkpoint parity and the pure runtime boundary."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest

from terra_qc import qc
from terra_qc.reference import DEFAULT_REFERENCE, REFERENCE_ID, REFERENCE_SHA256, load_reference

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = json.loads((ROOT / "tests/fixtures/terra/golden.json").read_text(encoding="utf-8"))


def test_selected_reference_keeps_the_verified_bytes_and_quieter_policy():
    reference = load_reference()
    assert hashlib.sha256(DEFAULT_REFERENCE.read_bytes()).hexdigest() == REFERENCE_SHA256
    assert reference.manifest["model_id"] == REFERENCE_ID
    policy = reference.manifest["temporal_policy"]["variables"]["temperature_f"]
    assert policy["sustained_readings"] == 5
    assert policy["extreme_readings"] == 1


def test_changed_reference_is_rejected(tmp_path):
    altered = tmp_path / "altered.json"
    altered.write_bytes(DEFAULT_REFERENCE.read_bytes() + b" ")
    with pytest.raises(ValueError, match="SHA-256"):
        load_reference(altered)


@pytest.mark.parametrize("case", ["measured", "missing"])
def test_actual_replay_matches_the_original_cleaned_detector(case):
    request = json.loads((ROOT / f"docs/terra/examples/{case}.request.json").read_text(encoding="utf-8"))
    frame = pd.DataFrame(request["observations"])
    frame["timestamp"] = pd.to_datetime(frame.timestamp, utc=True)
    scored = qc.score(frame, load_reference())
    actual = scored[scored.timestamp.eq(pd.Timestamp(request["query"]["valid_utc"]))].set_index("station_id")
    for row in GOLDEN["cases"][case]["rows"]:
        for key, expected in row.items():
            if key == "station_id":
                continue
            value = actual.loc[row["station_id"], key]
            if key == "timestamp":
                assert value == pd.Timestamp(expected)
            elif expected is None:
                assert pd.isna(value)
            elif isinstance(expected, (int, float)):
                assert value == pytest.approx(expected, abs=1e-10)
            else:
                assert value == expected
    episodes = qc.episodes(scored, 6)
    expected = GOLDEN["cases"][case]["episodes"]
    assert len(episodes) == len(expected)
    for current, original in zip(episodes, expected):
        for key in ("station_id", "start", "end", "detected_at", "severity", "reason"):
            assert current[key] == original[key]


def test_imported_source_matches_the_reviewed_checkpoint_manifest():
    manifest = json.loads((ROOT / "docs/terra/source-import.json").read_text(encoding="utf-8"))
    for name, entry in manifest["source_files"].items():
        raw = (ROOT / "backend/terra_qc" / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == entry["shared_sha256"], name


def test_actual_episode_peak_time_is_distinct_from_detection_time():
    from terra_monitoring.assessment import assess_location

    request = json.loads((ROOT / "docs/terra/examples/location.request.json").read_text(encoding="utf-8"))
    packet = assess_location(**request["query"], observations=pd.DataFrame(request["observations"]),
                             stations=pd.DataFrame(request["stations"]), snapshot_id=request["snapshot_id"],
                             data_mode=request["data_mode"])
    finding = next(f for f in packet["station_findings"] if f["station_id"] == 6)
    assert pd.Timestamp(finding["detected_at_utc"]) == pd.Timestamp("2026-01-11T08:00:00Z")
    assert pd.Timestamp(finding["evidence_peak_utc"]) == pd.Timestamp("2026-01-11T09:00:00Z")
    assert finding["summary"]["observed_f"] == 61.0


def test_cold_runtime_scores_and_estimates_with_all_ml_imports_forbidden():
    script = r'''
import importlib.abc, json, sys
from pathlib import Path
class NoML(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'sklearn','scipy','lightgbm','joblib','threadpoolctl'}:
            raise AssertionError('Unexpected ML dependency: '+fullname)
sys.meta_path.insert(0, NoML())
sys.path.insert(0, 'backend')
import pandas as pd
from terra_monitoring.assessment import assess_location
request=json.loads(Path('docs/terra/examples/measured.request.json').read_text(encoding='utf-8'))
packet=assess_location(**request['query'],observations=pd.DataFrame(request['observations']),
    stations=pd.DataFrame(request['stations']),snapshot_id=request['snapshot_id'],data_mode=request['data_mode'])
json.dumps(packet,allow_nan=False)
print(packet['schema_version'])
'''
    result = subprocess.run([sys.executable, "-B", "-c", script], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "terra-location-assessment-v1" in result.stdout
