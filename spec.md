# Orchard Chill Monitor: scope and requirements

ENG 3 design project, UC Davis. Team: Nathan Ward, Carlo Repetto, Ken Nakao.

## Problem

Fruit and nut trees need enough winter chill to break dormancy evenly. Growers track chill from the
nearest CIMIS station, which can sit kilometres away on irrigated grass and miss the orchard's own
microclimate (cold-air pooling, canopy shade). A cheap logger in the tree measures chill where it
matters, but cheap sensors fail quietly: they drift, freeze, get sun on them, lose power or drop
readings. A chill total is only useful if you know how much of it was actually measured.

## What the system does

1. **Measure**: Arduino Uno + DHT22 in a decorative "monkey" radiation shield, hung from a branch
   with a hook. One reading a minute. Temperature, humidity and a running chill-hour count on a
   16x2 I2C LCD; a buzzer for sensor errors and for reaching the chill target.
2. **Transfer**: every reading is a checksummed text line on USB serial (wire v1). A laptop or
   Raspberry Pi stamps it with UTC time and appends it to a capture file.
3. **Validate** (interoperability layer): decode, reject corrupt lines, flag duplicates, reboots,
   lost samples, clock problems, out-of-range, jumping and frozen values; build hourly values with
   coverage; cross-check each hour against nearby CIMIS stations using a physics estimate and an ML
   estimate weighted by their uncertainty; flag departures; estimate the hours that are missing or
   rejected.
4. **Report chill**: chill hours (0-7.2 C; the prototype) and Dynamic Model Chill Portions (the final
   version) for the season, each as measured-only, with-estimates and a p10-p90 range, with the share
   of hours measured and a list of every gap, as JSON and as a one-page HTML report.

## Requirements and how they are checked

| Requirement | Check |
| --- | --- |
| Logger reads within +-0.3 C of a reference thermometer over 0-10 C | `chillwatch logger calibrate` (pass rule: \|bias\| <= 0.3 C and >= 95 % of paired readings within 0.3 C); a fixed bias may be corrected with `calibration_offset_c` |
| A new user can find the chill total and any data gaps | the HTML report leads with the totals and the gap table (usability test with someone outside the team) |
| Every reading the backend uses is traceable and validated | sample rules and hourly coverage, `tests/test_interop_rules.py` |
| Faults are caught, not silently counted | demo with injected outage, warm fault, frozen sensor, failed read, corrupt line, reboot: all caught (`tests/test_interop_pipeline.py`) |
| Physics and ML references are combined by their uncertainty | minimum-variance fusion, `tests/test_interop_fusion.py`, ML path with a trained model |
| Chill models are standard | chill-hour band 0-7.2 C; Dynamic Model equal to chillR's (`tests/test_chill_models.py`) |
| Firmware and backend agree on the record | the sketch is compiled on the host and its output decoded (`tests/test_interop_tools.py`) |
| Totals are honest about missing data | complete only at >= 95 % measured hours; a target counts as reached only if the p10 reaches it |

## Design decisions

* **No clock on the logger.** The Uno sends sequence number and uptime; the host adds UTC time.
  Sequence and uptime let the backend detect reboots, lost samples and host clock jumps.
* **Hour-ending UTC hours**, the CIMIS convention, so logger and reference hours line up.
* **Reference = ChillWatch.** The existing ChillWatch layer (CIMIS ingest, station QC, Theil-Sen
  lapse + IDW physics core, LightGBM ML core, held-out uncertainty bins) predicts the logger's
  temperature from the stations around it. The logger is never one of its inputs.
* **Learned orchard offset.** The orchard differs from grass-sited stations; that steady difference
  (night and day separately) is learned from trailing data, and only departures from it are flagged.
* **Estimates are labeled.** Gap-filled hours carry `is_estimated` and an uncertainty, which drives
  the p10-p90 range of the totals.

## Out of scope

Forecasts, bloom prediction and orchard management advice; texting the grower (a LoRa/Meshtastic
add-on was costed separately and is not part of this code); multi-user hosting. The Terra web app,
chat, SMS and PostgreSQL storage that this repository used to contain are outside this project's
scope and remain in Git history (`87ee26b`).
