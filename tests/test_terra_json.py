"""Controlled/software checks for the composition; no natural fault claims."""
import copy
import json

import numpy as np
import pandas as pd
import pytest

from terra_monitoring.assessment import assess_location

HOURS = pd.date_range("2026-01-11T09:00:00Z", periods=8, freq="h")


@pytest.fixture
def stations():
    return pd.DataFrame({
        "station_id": [6, 226, 139, 212, 235],
        "lat": [38.535694, 38.672722, 38.501258, 38.278056, 38.797944],
        "lon": [-121.77636, -121.81172, -121.97853, -121.74111, -121.61136],
        "elevation_ft": [60.0, 82.0, 136.0, 7.0, 24.0],
    })


@pytest.fixture
def observations():
    return pd.DataFrame([{"station_id": s, "timestamp": t, "temperature_f": 40.0,
                          "quality": "usable", "source_flag": "", "source": "cimis",
                          "temperature_unit": "F", "timestamp_verified": True}
                         for t in HOURS for s in [6, 226, 139, 212, 235]])


def packet(observations, stations, **changes):
    args = dict(lat=38.6, lon=-121.8, elevation_ft=60.0, valid_utc=HOURS[-1], as_of=HOURS[-1],
                observations=observations, stations=stations, snapshot_id="controlled-fixture-v1",
                data_mode="synthetic", location_id="test-orchard")
    args.update(changes)
    return assess_location(**args)


def at_davis(observations, stations, **changes):
    return packet(observations, stations, lat=38.535694, lon=-121.77636,
                  target_station_id=6, **changes)


def test_uninstrumented_location_has_estimate_but_no_invented_discrepancy(observations, stations):
    result = packet(observations, stations)
    assert result["schema_version"] == "terra-location-assessment-v1"
    assert result["location_estimate"]["status"] == "ok"
    assert result["location_estimate"]["temperature"] is not None
    assert result["target_comparison"]["status"] == "unassessable"
    assert result["target_comparison"]["reason"] == "no_independent_target_observation"
    assert result["target_comparison"]["observed_f"] is None
    assert result["target_comparison"]["departure_f"] is None
    assert result["provenance"]["publication_visibility"] == "snapshot_only"
    assert result["provenance"]["peer_exclusion_basis"] == "causal_station_qc"
    json.dumps(result, allow_nan=False)


def test_real_statistical_finding_excludes_its_station_from_estimate(observations, stations):
    spike = observations.copy()
    spike.loc[spike.station_id.eq(6) & spike.timestamp.eq(HOURS[-1]), "temperature_f"] = 120.0
    result = packet(spike, stations)
    findings = [f for f in result["station_findings"] if f["station_id"] == 6]
    assert len(findings) == 1 and findings[0]["reason"] == "extreme_departure"
    assert findings[0]["severity"] == "strong"
    assert 6 not in result["location_estimate"]["used_station_ids"]
    assert any(e["station_id"] == 6 and e["reason"].startswith("terra_finding:")
               for e in result["location_estimate"]["excluded"])
    assert findings[0]["reference_id"] == result["provenance"]["qc_reference_id"]


def test_measured_known_station_uses_its_calibrated_comparison(observations, stations):
    spike = observations.copy()
    spike.loc[spike.station_id.eq(6) & spike.timestamp.eq(HOURS[-1]), "temperature_f"] = 120.0
    result = at_davis(spike, stations)
    comparison = result["target_comparison"]
    assert comparison["status"] == "assessable"
    assert comparison["classification"] == "suspected_anomaly"
    assert comparison["observed_f"] == 120.0
    assert comparison["departure_f"] == pytest.approx(120.0 - comparison["expected_f"])
    assert comparison["expected_method"] == "terra-pair-adjusted-median"
    assert result["location_estimate"]["method"] == "idw"
    assert 6 not in result["location_estimate"]["used_station_ids"]
    assert result["provenance"]["peer_exclusion_basis"] == "target_held_out"
    assert all(f["basis"] == "target_held_out" for f in result["estimator_qc_exclusions"])


def test_whole_pipeline_location_expectation_never_uses_held_out_target(observations, stations):
    baseline = at_davis(observations, stations)
    for value, flag, quality in [(130.0, "", "usable"), (-120.0, "Y", "usable"),
                                 (np.nan, "M", "missing"), (120.0, "R", "flagged")]:
        modified = observations.copy()
        target = modified.station_id.eq(6)
        modified.loc[target, "temperature_f"] = value
        modified.loc[target, "source_flag"] = flag
        modified.loc[target, "quality"] = quality
        result = at_davis(modified, stations)
        assert result["location_estimate"] == baseline["location_estimate"]
        assert result["estimator_qc_exclusions"] == baseline["estimator_qc_exclusions"]


def test_unknown_measured_station_gets_numbers_without_calibrated_fault_label(observations, stations):
    extra = observations[observations.station_id.eq(6)].copy()
    extra["station_id"], extra["temperature_f"] = 999, 70.0
    observations = pd.concat([observations, extra], ignore_index=True)
    stations = pd.concat([stations, pd.DataFrame([{"station_id": 999, "lat": 38.6, "lon": -121.8,
                                                  "elevation_ft": 60.0}])], ignore_index=True)
    result = packet(observations, stations, target_station_id=999)
    comparison = result["target_comparison"]
    assert comparison["status"] == "comparison_only"
    assert comparison["reason"] == "no_calibrated_station_policy"
    assert comparison["classification"] is None and comparison["reference_id"] is None
    assert comparison["observed_f"] == 70.0 and comparison["departure_f"] is not None
    assert 999 not in result["location_estimate"]["used_station_ids"]


def test_future_rows_and_not_yet_visible_revision_cannot_affect_past_packet(observations, stations):
    observations["visible_at"] = observations.timestamp
    baseline = packet(observations, stations)
    future = observations.iloc[[-1]].copy()
    future["timestamp"] = HOURS[-1] + pd.Timedelta(hours=1)
    future["visible_at"] = future.timestamp
    future["temperature_f"] = 130.0
    revision = observations[observations.station_id.eq(6) & observations.timestamp.eq(HOURS[-1])].copy()
    revision["temperature_f"] = 130.0
    revision["visible_at"] = HOURS[-1] + pd.Timedelta(hours=1)
    combined = pd.concat([observations, future, revision], ignore_index=True)
    result = packet(combined, stations)
    assert result == baseline


def test_unknown_publication_visibility_withholds_observation(observations, stations):
    observations["visible_at"] = observations.timestamp
    observations.loc[observations.station_id.eq(6), "visible_at"] = pd.NaT
    result = packet(observations, stations)
    assert 6 not in result["location_estimate"]["used_station_ids"]
    assert result["provenance"]["publication_visibility"] == "explicit_visible_at"


def test_sustained_finding_is_not_visible_before_its_fifth_reading(observations, stations):
    from terra_qc import qc
    from terra_qc.reference import load_reference

    reference = load_reference()
    scored = qc.score(observations, reference)
    target_rows = scored[scored.station_id.eq(6)].set_index("timestamp")
    for i, row in observations[observations.station_id.eq(6)].iterrows():
        expected = target_rows.loc[row.timestamp]
        moderate = (expected.sustained_high_residual_f + expected.extreme_abs_residual_f) / 2
        observations.loc[i, "temperature_f"] = expected.expected_f + moderate
    before = packet(observations, stations, valid_utc=HOURS[3], as_of=HOURS[3])
    assert not any(f["station_id"] == 6 for f in before["station_findings"])
    after = packet(observations, stations, valid_utc=HOURS[3], as_of=HOURS[4])
    event = next(f for f in after["station_findings"] if f["station_id"] == 6)
    assert pd.Timestamp(event["detected_at_utc"]) == HOURS[4]
    assert pd.Timestamp(event["visible_at_utc"]) == HOURS[4]
    assert pd.Timestamp(event["start_utc"]) == HOURS[0]


def test_stale_numbers_are_explicit_and_cannot_be_classified_current(observations, stations):
    result = at_davis(observations, stations, as_of=HOURS[-1] + pd.Timedelta(hours=7), data_mode="current")
    assert result["location_estimate"]["status"] == "stale"
    assert result["location_estimate"]["temperature"] is not None
    assert result["target_comparison"]["status"] == "stale"
    assert result["target_comparison"]["classification"] is None


def test_missing_peers_yield_strict_json_nulls_not_anomaly(observations, stations):
    sparse = observations[observations.station_id.isin([6, 226])]
    result = at_davis(sparse, stations)
    assert result["location_estimate"]["status"] == "insufficient_data"
    assert result["location_estimate"]["temperature"] is None
    assert result["target_comparison"]["status"] == "unavailable"
    assert result["target_comparison"]["classification"] is None
    assert result["station_findings"] == []
    json.dumps(result, allow_nan=False)


def test_source_warning_is_health_evidence_not_numeric_fault_truth(observations, stations):
    observations.loc[observations.station_id.eq(6), "source_flag"] = "Y"
    result = at_davis(observations, stations)
    assert result["target_comparison"]["status"] == "unavailable"
    assert result["target_comparison"]["classification"] is None
    assert not any(f["station_id"] == 6 for f in result["station_findings"])
    health = next(h for h in result["data_health"] if h["station_id"] == 6)
    assert "source_warning" in health["reasons"] and health["source_flag"] == "Y"


def test_empty_observations_and_distant_coordinates_are_honest_unavailable(observations, stations):
    empty = packet(observations.iloc[0:0], stations)
    far = packet(observations, stations, lat=0.0, lon=0.0)
    assert empty["location_estimate"]["temperature"] is None
    assert far["location_estimate"]["status"] == "insufficient_data"
    assert far["station_findings"] == []


@pytest.mark.parametrize("change", [
    {"lat": 91.0}, {"lon": -181.0}, {"elevation_ft": float("nan")},
    {"valid_utc": "2026-01-11T16:00:00"}, {"as_of": "2026-01-11T16:00:00"},
    {"valid_utc": "2026-01-11T16:30:00Z"}, {"snapshot_id": ""},
    {"data_mode": "live-ish"}, {"max_age_hours": -1.0},
    {"valid_utc": HOURS[-1] + pd.Timedelta(hours=1)},
])
def test_rejects_ambiguous_or_invalid_request(observations, stations, change):
    with pytest.raises(ValueError):
        packet(observations, stations, **change)


def test_rejects_target_identity_at_different_coordinates(observations, stations):
    with pytest.raises(ValueError, match="Target station geometry"):
        packet(observations, stations, target_station_id=6)


def test_does_not_mutate_inputs_and_is_deterministic(observations, stations):
    old_obs, old_stations = observations.copy(deep=True), stations.copy(deep=True)
    first = packet(observations, stations)
    assert first == packet(observations, stations)
    pd.testing.assert_frame_equal(observations, old_obs)
    pd.testing.assert_frame_equal(stations, old_stations)


def test_rejects_mutated_reference_even_without_calibrated_observations(observations, stations):
    from terra_qc.reference import load_reference

    reference = copy.deepcopy(load_reference())
    reference.manifest["range_calibration"]["bounds"][6]["extreme_abs_residual_f"] += 1
    with pytest.raises(ValueError, match="identity"):
        packet(observations.iloc[0:0], stations, reference=reference)


@pytest.mark.parametrize("late_station", [235, 999])
def test_unrelated_or_unused_late_publication_does_not_delay_finding(observations, stations, late_station):
    observations.loc[observations.station_id.eq(6), "temperature_f"] = 100.0
    observations["visible_at"] = observations.timestamp
    late = HOURS[0] + pd.Timedelta(hours=2)
    if late_station == 235:
        unused = observations.station_id.eq(235) & observations.timestamp.eq(HOURS[0])
        observations.loc[unused, "source_flag"] = "Y"  # explicitly excluded from E1's participating peers
        observations.loc[unused, "visible_at"] = late
    else:
        unrelated = observations[observations.station_id.eq(226) & observations.timestamp.eq(HOURS[0])].copy()
        unrelated["station_id"], unrelated["visible_at"] = 999, late
        observations = pd.concat([observations, unrelated], ignore_index=True)
        stations = pd.concat([stations, pd.DataFrame([{"station_id": 999, "lat": 38.6, "lon": -121.8,
                                                      "elevation_ft": 60.0}])], ignore_index=True)
    result = packet(observations, stations, valid_utc=HOURS[0], as_of=HOURS[-1], max_age_hours=24)
    event = next(f for f in result["station_findings"] if f["station_id"] == 6)
    assert pd.Timestamp(event["detected_at_utc"]) == HOURS[0]
    assert pd.Timestamp(event["visible_at_utc"]) == HOURS[0]
    opening = next(e for e in event["evidence"] if pd.Timestamp(e["timestamp"]) == HOURS[0])
    assert late_station not in {p["station_id"] for p in opening["peer_evidence"]}


def test_contributing_late_publication_delays_opening_but_continuation_does_not(observations, stations):
    observations.loc[observations.station_id.eq(6), "temperature_f"] = 100.0
    observations["visible_at"] = observations.timestamp
    late = HOURS[0] + pd.Timedelta(hours=2)
    contributor = observations.station_id.eq(226) & observations.timestamp.eq(HOURS[0])
    observations.loc[contributor, "visible_at"] = late
    result = packet(observations, stations, valid_utc=HOURS[0], as_of=HOURS[-1], max_age_hours=24)
    event = next(f for f in result["station_findings"] if f["station_id"] == 6)
    opening = next(e for e in event["evidence"] if pd.Timestamp(e["timestamp"]) == HOURS[0])
    assert 226 in {p["station_id"] for p in opening["peer_evidence"]}
    assert pd.Timestamp(event["detected_at_utc"]) == HOURS[0]
    assert pd.Timestamp(event["visible_at_utc"]) == late
    assert pd.Timestamp(event["evidence"][-1]["timestamp"]) == HOURS[-1]
    assert pd.Timestamp(event["visible_at_utc"]) < HOURS[-1]
