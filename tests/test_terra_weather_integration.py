"""Actual provider and sim/monitor integration; numerical findings stay separate from QC."""
from terra_monitoring.live import normalize_cimis_payload
from terra_monitoring.demo_simulation.engine import DemoSimulation
from terra_monitoring.weather import assess_weather


def weather_record(station=6):
    return {"Station": str(station), "Date": "2026-03-11", "Hour": "0300", "Scope": "hourly",
            "HlyAirTmp": {"Value": "45", "Unit": "(F)", "Qc": " "},
            "HlyRelHum": {"Value": "70", "Unit": "(%)", "Qc": " "},
            "HlyWindSpd": {"Value": "5", "Unit": "(mph)", "Qc": " "},
            "HlySolRad": {"Value": "0", "Unit": "(Ly/d)", "Qc": " "}}


def test_raw_variable_quality_never_invalidates_temperature_or_wind():
    row = weather_record()
    row["HlyRelHum"]["Qc"] = "Y"
    payload = {"Data": {"Providers": [{"Records": [row]}]}}
    frame, _ = normalize_cimis_payload(payload, requested_station_ids=[6], observed_at="2026-03-11T12:00:00Z")
    result = frame.iloc[0]
    assert result.quality == "usable" and result.temperature_f == 45
    assert not result.relative_humidity_pct_available
    assert result.relative_humidity_pct_source_flag == "Y"
    assert result.wind_speed_mph_available and result.solar_radiation_w_m2_available


def test_omitted_requested_variable_is_missing_data_not_normal_weather():
    records = [weather_record(station) for station in [6, 139, 212, 226, 235]]
    for row in records:
        del row["HlyRelHum"]
    payload = {"Data": {"Providers": [{"Records": records}]}}
    frame, _ = normalize_cimis_payload(payload, requested_station_ids=[6,139,212,226,235], observed_at="2026-03-11T12:00:00Z")
    stations = [{"station_id": int(row["Station"]), "lat":38.5, "lon":-121.8} for row in records]
    result = assess_weather(frame, stations, "2026-03-11T11:00:00Z", "2026-03-11T12:00:00Z")
    humidity = next(v for v in result["variables"] if v["variable"] == "relative_humidity_pct")
    assert all(s["status"] == "unavailable" for s in humidity["stations"])
    assert len([h for h in result["data_health"] if h["variable"] == "relative_humidity_pct"]) == 5
    assert not any(f["variable"] == "relative_humidity_pct" for f in result["findings"])


def test_real_sim_weather_change_quality_missing_and_restart(tmp_path):
    sim = DemoSimulation(state_dir=tmp_path, grid_size=2)
    baseline = sim.state()
    assert baseline["assessment"]["weather"]["enabled"]
    assert baseline["assessment"]["weather"]["findings"] == []
    original_temperature = baseline["assessment"]["location_estimate"]["temperature"]
    action = {"type": "weather", "station_id": 6, "variable": "wind_speed_mph", "value": 60, "source_flag": ""}
    high = sim.action(action)
    findings = high["assessment"]["weather"]["findings"]
    assert any(f["variable"] == "wind_speed_mph" and f["station_id"] == 6 for f in findings)
    assert high["assessment"]["location_estimate"]["temperature"] == original_temperature
    notes = [m for m in high["messages"] if "weather_discrepancy" in m["event_types"]]
    assert len(notes) == 1 and "wind" in notes[0]["text"].lower() and "mph" in notes[0]["text"]
    assert high["notifications"][-1]["provider_delivery"] is False
    reply = sim.question("What is the wind speed?", request_id="weather-wind-question")["message"]
    assert "60" in reply["text"] and "mph" in reply["text"]
    assert reply["generation_outcome"] == "local_weather_response"
    assert sim.question("What is the wind speed?", request_id="weather-wind-question")["message"]["id"] == reply["id"]
    # The reply becomes an additional immutable message, not another alert.
    high = sim.state()
    assert sim.action(action)["messages"] == high["messages"]
    restarted = DemoSimulation(state_dir=tmp_path, grid_size=2).state()
    assert restarted["messages"] == high["messages"]
    warned = sim.action({**action, "source_flag": "Y"})
    assert not any(f["variable"] == "wind_speed_mph" and f["station_id"] == 6 for f in warned["assessment"]["weather"]["findings"])
    assert any(h["station_id"] == 6 and h["variable"] == "wind_speed_mph" and "source_warning" in h["reasons"] for h in warned["assessment"]["weather"]["data_health"])
    missing = sim.action({**action, "value": None})
    assert any(h["station_id"] == 6 and h["variable"] == "wind_speed_mph" for h in missing["assessment"]["weather"]["data_health"])
    assert missing["assessment"]["location_estimate"]["temperature"] == original_temperature
    details = sim.details(notes[0]["id"])
    assert details["demo_snapshot"]["assessment"]["weather"]["findings"] == findings
    assert baseline["source"]["weather_variables"]["temperature_overlap"]["max_absolute_difference_f"] == 0


def test_sim_three_variables_react_and_regional_changes_do_not(tmp_path):
    sim = DemoSimulation(state_dir=tmp_path, grid_size=2)
    # Current source RH is high at night; a very dry station is a discrepancy.
    dry = sim.action({"type": "weather", "station_id": 6, "variable": "relative_humidity_pct", "value": 0, "source_flag": ""})
    assert any(f["variable"] == "relative_humidity_pct" for f in dry["assessment"]["weather"]["findings"])
    sim.action({"type": "weather_reset", "station_id": 6, "variable": "relative_humidity_pct"})
    # Actual daylight is required for solar peer comparisons.
    sim.action({"type": "advance", "hours": 6})
    solar = sim.action({"type": "weather", "station_id": 6, "variable": "solar_radiation_w_m2", "value": 1800, "source_flag": ""})
    assert any(f["variable"] == "solar_radiation_w_m2" for f in solar["assessment"]["weather"]["findings"])
    for station in [6, 139, 212, 226, 235]:
        regional = sim.action({"type": "weather", "station_id": station, "variable": "solar_radiation_w_m2", "value": 1800, "source_flag": ""})
    assert not any(f["variable"] == "solar_radiation_w_m2" for f in regional["assessment"]["weather"]["findings"])
