"""Deterministic weather discrepancy/availability controls; no provider calls."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pandas as pd
import pytest

from terra_monitoring.weather import (
    LY_PER_DAY_TO_W_M2, MPS_TO_MPH, VARIABLES, assess_weather, normalize_cimis_weather,
)

NOW = datetime(2026, 1, 11, 21, tzinfo=timezone.utc)
STATIONS = [
    {"station_id": sid, "lat": 38.5 + index * .02, "lon": -121.7, "elevation_ft": 10}
    for index, sid in enumerate([6, 226, 139, 212, 235])
]
NORMAL = {"relative_humidity_pct": 55, "wind_speed_mph": 5, "solar_radiation_w_m2": 600}
FIELDS = {"relative_humidity_pct": "HlyRelHum", "wind_speed_mph": "HlyWindSpd", "solar_radiation_w_m2": "HlySolRad"}
UNITS = {"relative_humidity_pct": "%", "wind_speed_mph": "mph", "solar_radiation_w_m2": "W/m²"}


def raw(**changes):
    record = {FIELDS[variable]: {"Value": str(NORMAL[variable]), "Unit": UNITS[variable], "Qc": " "}
        for variable in VARIABLES}
    return {**record, **changes}


def rows(hours=3):
    result = []
    for offset in reversed(range(hours)):
        for station in STATIONS:
            result.append({"station_id": station["station_id"], "timestamp": NOW - timedelta(hours=offset),
                "timestamp_verified": True, "source": "cimis", **normalize_cimis_weather(raw())})
    return result


def assess(data=None, **changes):
    return assess_weather(rows() if data is None else data, STATIONS,
        changes.pop("valid_time_utc", NOW), changes.pop("as_of_utc", NOW + timedelta(minutes=10)), **changes)


def station(result, variable, sid=6):
    return next(s for v in result["variables"] if v["variable"] == variable for s in v["stations"] if s["station_id"] == sid)


def change(data, variable, value, *, sid=6, only_latest=False, **metadata):
    for row in data:
        if row["station_id"] == sid and (not only_latest or row["timestamp"] == NOW):
            row.update({variable: value, **{variable + "_" + key: val for key, val in metadata.items()}})


def test_actual_cimis_units_are_normalized_once_and_originals_retained():
    result = normalize_cimis_weather(raw(HlyRelHum={"Value": "78", "Unit": "(%)", "Qc": ""},
        HlyWindSpd={"Value": "4", "Unit": "(m/s)", "Qc": " "},
        HlySolRad={"Value": "1000", "Unit": "(Ly/d)", "Qc": ""}))
    assert result["relative_humidity_pct"] == 78
    assert result["wind_speed_mph"] == pytest.approx(4 * MPS_TO_MPH)
    assert result["solar_radiation_w_m2"] == pytest.approx(1000 * LY_PER_DAY_TO_W_M2)
    assert result["solar_radiation_w_m2_source_value"] == "1000"
    assert result["solar_radiation_w_m2_source_unit"] == "(Ly/d)"
    assert all(result[v + "_available"] for v in VARIABLES)
    data = rows(1)
    for row in data:
        row.update(result)
    assessment = assess(data)
    assert station(assessment, "solar_radiation_w_m2")["observed"] == pytest.approx(1000 * LY_PER_DAY_TO_W_M2)


@pytest.mark.parametrize("variable,value,reason", [
    ("relative_humidity_pct", -1, "physical_bounds"),
    ("relative_humidity_pct", 101, "physical_bounds"),
    ("wind_speed_mph", -0.1, "physical_bounds"),
    ("solar_radiation_w_m2", -1, "below_policy_minimum"),
    ("wind_speed_mph", float("inf"), "nonfinite_value"),
    ("relative_humidity_pct", True, "invalid_numeric"),
    ("solar_radiation_w_m2", "bad", "invalid_numeric"),
    ("solar_radiation_w_m2", None, "missing_value"),
])
def test_invalid_measurements_are_data_health_only(variable, value, reason):
    record = raw(**{FIELDS[variable]: {"Value": value, "Unit": UNITS[variable], "Qc": ""}})
    parsed = normalize_cimis_weather(record)
    assert parsed[variable + "_availability_reason"] == reason
    data = rows(1)
    data[0].update(parsed)
    result = assess(data)
    assert not result["findings"]
    health = next(h for h in result["data_health"] if h["station_id"] == 6 and h["variable"] == variable)
    assert health["reason"] == reason
    assert station(result, next(v for v in VARIABLES if v != variable))["status"] == "assessable"
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("variable", VARIABLES)
@pytest.mark.parametrize("flag,reason", [("Y", "source_warning"), ("R", "source_warning"),
    ("M", "source_missing"), ("N", "source_missing"), ("P", "source_pending"), (None, "source_qc_unknown")])
def test_source_flags_are_independent_unavailable_evidence(variable, flag, reason):
    parsed = normalize_cimis_weather(raw(**{FIELDS[variable]: {"Value": NORMAL[variable], "Unit": UNITS[variable], "Qc": flag}}))
    data = rows()
    for row in data:
        if row["station_id"] == 6:
            row.update(parsed)
    result = assess(data)
    assert not result["findings"]
    current = station(result, variable)
    assert current["status"] == "unavailable" and current["reason"] == reason
    assert all(station(result, other)["status"] == "assessable" for other in VARIABLES if other != variable)
    health = next(h for h in result["data_health"] if h["station_id"] == 6 and h["variable"] == variable)
    assert health["source_flag"] == flag and health["raw_source_rows"]


@pytest.mark.parametrize("unit", [None, "", "fraction", "W/m2 per hour"])
def test_absent_and_unknown_units_are_not_guessed(unit):
    result = normalize_cimis_weather(raw(HlySolRad={"Value": 600, "Unit": unit, "Qc": ""}))
    assert result["solar_radiation_w_m2"] is None
    assert result["solar_radiation_w_m2_availability_reason"] == "unknown_unit"


def test_normal_weather_and_regional_changes_do_not_raise_station_findings():
    data = rows()
    for row in data:
        row.update(relative_humidity_pct=90, wind_speed_mph=40, solar_radiation_w_m2=1100)
    result = assess(data)
    assert result["findings"] == [] and result["data_health"] == []
    assert all(s["classification"] == "no_alert" for v in result["variables"] for s in v["stations"])
    assert result["audit"]["temperature_policy_changed"] is False


@pytest.mark.parametrize("variable,value", [("relative_humidity_pct", 90), ("wind_speed_mph", 19), ("solar_radiation_w_m2", 1000)])
def test_three_same_direction_moderate_hours_produce_only_target_finding(variable, value):
    data = rows()
    change(data, variable, value)
    result = assess(data)
    assert [(f["variable"], f["station_id"], f["severity"]) for f in result["findings"]] == [(variable, 6, "moderate")]
    finding = result["findings"][0]
    assert len(finding["evidence"]) == 3 and finding["cause"] == "unconfirmed"
    assert finding["classification"] == "suspected_discrepancy"
    assert finding["validation_status"] == "provisional_conservative_peer_discrepancy_rules"
    assert station(result, variable)["classification"] == "suspected_discrepancy"
    assert all(station(result, variable, sid)["classification"] == "no_alert" for sid in [226, 139, 212, 235])
    assert finding["departure_unit"] == ("percentage points" if variable == "relative_humidity_pct" else UNITS[variable])


@pytest.mark.parametrize("variable,baseline,extreme", [("relative_humidity_pct", 10, 90),
    ("wind_speed_mph", 5, 35), ("solar_radiation_w_m2", 200, 1000)])
def test_one_extreme_hour_produces_immediate_discrepancy(variable, baseline, extreme):
    data = rows(1)
    for row in data:
        row[variable] = baseline
    change(data, variable, extreme)
    result = assess(data)
    assert len(result["findings"]) == 1
    finding = result["findings"][0]
    assert finding["severity"] == "strong" and len(finding["evidence"]) == 1
    assert finding["start_utc"] == finding["end_utc"] == finding["detected_at_utc"]


@pytest.mark.parametrize("failure", ["gap", "source_warning", "opposite_direction", "normal_middle"])
def test_missing_or_unusable_or_direction_change_resets_sustained_run(failure):
    data = rows()
    variable = "relative_humidity_pct"
    change(data, variable, 90)
    middle = NOW - timedelta(hours=1)
    if failure == "gap":
        data = [row for row in data if not (row["station_id"] == 6 and row["timestamp"] == middle)]
    else:
        for row in data:
            if row["station_id"] == 6 and row["timestamp"] == middle:
                if failure == "source_warning":
                    row[variable + "_source_flag"] = "Y"
                else:
                    row[variable] = 20 if failure == "opposite_direction" else 55
    assert assess(data)["findings"] == []


def test_two_moderate_hours_and_small_noise_stay_quiet():
    data = rows(2)
    change(data, "relative_humidity_pct", 90)
    change(data, "wind_speed_mph", 6)
    assert assess(data)["findings"] == []


def test_nighttime_zero_solar_is_valid_without_a_discrepancy_or_health_issue():
    data = rows()
    for row in data:
        row["solar_radiation_w_m2"] = 0
    result = assess(data)
    assert result["findings"] == [] and result["data_health"] == []
    assert all(s["status"] == "low_daylight" and s["observed"] == 0
        for v in result["variables"] if v["variable"] == "solar_radiation_w_m2" for s in v["stations"])


def test_low_daylight_withholds_solar_classification_even_for_large_departure():
    data = rows()
    for row in data:
        row["solar_radiation_w_m2"] = 0
    change(data, "solar_radiation_w_m2", 1000)
    result = assess(data)
    assert result["findings"] == []
    assert station(result, "solar_radiation_w_m2")["status"] == "low_daylight"


def test_insufficient_peers_are_coverage_health_without_an_anomaly():
    data = rows()
    change(data, "wind_speed_mph", 100)
    for sid in [212, 235]:
        change(data, "wind_speed_mph", None, sid=sid)
    result = assess(data)
    assert result["findings"] == []
    assert station(result, "wind_speed_mph")["reason"] == "insufficient_peers"
    assert station(result, "wind_speed_mph")["observed"] == 100  # High wind itself is not invalid.
    assert next(h for h in result["data_health"] if h["variable"] == "wind_speed_mph" and h["station_id"] == 6)["reason"] == "insufficient_peers"


def test_distant_and_not_operating_stations_do_not_supply_peers():
    geometry = deepcopy(STATIONS)
    geometry[3]["lon"] = -115
    geometry[4]["disconnect_date"] = "2020-01-01"
    result = assess_weather(rows(), geometry, NOW, NOW)
    assert station(result, "wind_speed_mph")["reason"] == "insufficient_peers"
    assert station(result, "wind_speed_mph", 235)["reason"] == "station_not_operating"
    assert station(result, "wind_speed_mph")["used_station_ids"] == [139, 226]


def test_stale_readings_retain_values_but_never_trigger_current_findings():
    data = rows()
    change(data, "wind_speed_mph", 35)
    result = assess(data, as_of_utc=NOW + timedelta(hours=7))
    assert result["findings"] == []
    assert station(result, "wind_speed_mph")["status"] == "stale"
    assert station(result, "wind_speed_mph")["observed"] == 35
    assert all(h["reason"] == "observation_older_than_age_limit" for h in result["data_health"])


def test_duplicate_conflict_affects_only_its_variable():
    data = rows(1)
    conflict = deepcopy(data[0])
    conflict["relative_humidity_pct"] = 90
    data.append(conflict)
    result = assess(data)
    assert station(result, "relative_humidity_pct")["reason"] == "conflicting_duplicate"
    assert station(result, "wind_speed_mph")["status"] == "assessable"
    assert result["audit"]["conflicting_variable_groups"] == 1
    assert result["findings"] == []


def test_exact_duplicates_collapse_deterministically_without_mutating_input():
    data = rows()
    data.extend(deepcopy(data))
    original = deepcopy(data)
    result = assess(data)
    assert result["audit"]["duplicate_rows_collapsed"] == 15
    assert not result["data_health"]
    assert data == original
    assert result == assess(list(reversed(data)))


def test_latest_visible_revision_wins_and_future_revision_is_not_used():
    data = rows(1)
    for row in data:
        row["visible_at"] = NOW
    target = deepcopy(data[0])
    target.update(relative_humidity_pct=90, visible_at=NOW + timedelta(minutes=20))
    data.append(target)
    early = assess(data)
    late = assess(data, as_of_utc=NOW + timedelta(minutes=30))
    assert station(early, "relative_humidity_pct")["observed"] == 55
    assert station(late, "relative_humidity_pct")["observed"] == 90
    assert early["audit"]["discarded_rows"] == {"not_yet_visible": 1}


def test_future_observation_is_not_used_for_current_or_history():
    data = rows()
    extra = deepcopy(data[0])
    extra.update(timestamp=NOW + timedelta(hours=1), wind_speed_mph=100)
    data.append(extra)
    result = assess(data)
    assert result["findings"] == []
    assert result["audit"]["discarded_rows"] == {"future_observation": 1}


def test_invalid_timestamp_is_quarantined_and_missing_status_remains_explicit():
    data = rows(1)
    data[0]["timestamp"] = "2026-01-11T21:30:00Z"
    result = assess(data)
    assert result["findings"] == []
    assert result["audit"]["quarantined"][0]["reason"] == "invalid_identity_or_timestamp"
    assert station(result, "relative_humidity_pct")["reason"] == "missing_observation"


def test_timestamp_trust_is_required_for_each_weather_variable():
    data = rows(1)
    data[0]["timestamp_verified"] = False
    result = assess(data)
    assert all(station(result, variable)["reason"] == "timestamp_unverified" for variable in VARIABLES)
    assert result["findings"] == []


def test_temperature_quality_is_independent_and_cannot_poison_weather():
    data = rows()
    for row in data:
        row.update(temperature_f=None, quality="missing", source_flag="M")
    result = assess(data)
    assert not result["findings"] and not result["data_health"]
    assert all(s["status"] == "assessable" for v in result["variables"] for s in v["stations"])


def test_legacy_temperature_only_rows_get_explicit_weather_unavailable():
    data = [{"station_id": station["station_id"], "timestamp": NOW, "temperature_f": 45,
        "quality": "usable", "source_flag": "", "timestamp_verified": True} for station in STATIONS]
    result = assess(data)
    assert result["findings"] == [] and len(result["data_health"]) == 15
    assert all(s["status"] == "unavailable" for v in result["variables"] for s in v["stations"])


def test_dataframe_contract_and_strict_finite_json():
    result = assess_weather(pd.DataFrame(rows()), pd.DataFrame(STATIONS), pd.Timestamp(NOW), pd.Timestamp(NOW))
    assert result["schema_version"] == "terra-weather-assessment-v1"
    assert result["scope"] == "observed_station_weather_only"
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("changes", [{"valid_time_utc": NOW + timedelta(hours=1)}, {"as_of_utc": NOW.replace(tzinfo=None)},
    {"max_age_hours": float("nan")}, {"max_age_hours": -1}, {"max_age_hours": True}])
def test_invalid_assessment_clocks_and_age_limits_are_rejected(changes):
    with pytest.raises(ValueError):
        assess(**changes)
