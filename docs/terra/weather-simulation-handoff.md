# Humidity, wind and solar handoff

The active location-assessment JSON now adds `weather` with schema
`terra-weather-assessment-v1` and policy `terra-weather-policy-v1`.
Use this same backend result in Nathan's redesigned sim and the Terra conversation.
Browser code must not recompute findings. The temperature estimator/map and frozen
temperature QC retain their existing meanings and numerical reference.

Each variable has per-station observed/peer/departure values, unit, eligibility,
reason, level and classification. `weather.findings` contains numerical station
discrepancies; `weather.data_health` contains missing/invalid/source-warning/stale
inputs and insufficient peers. A missing humidity value does not remove valid
temperature, wind or solar measurements. No orchard RH/wind/solar estimate is
invented. The checked initial policy and limitations are in [weather-contract](weather-contract.md).

## Operator API

Existing `POST /api/demo/action` accepts:

```json
{"type":"weather","station_id":6,"variable":"wind_speed_mph","value":60,"source_flag":""}
```

Variables are `relative_humidity_pct` (%), `wind_speed_mph` (hourly average mph),
and `solar_radiation_w_m2` (W/m²). Use `value:null` for missing data and
`source_flag:"Y"` to demonstrate a source warning. These are independent controls;
warnings cannot produce a numerical discrepancy from an ineligible reading.
Values and explicit quality overrides persist across distinct hourly advances.
Restore the current recorded value and stop the continuing override with:

```json
{"type":"weather_reset","station_id":6,"variable":"wind_speed_mph"}
```

Existing advance/reset/temperature/online actions remain available. Station
payloads expose `stations[].weather`; the full result is `assessment.weather`.
The existing station lab puts the three controls in one collapsed disclosure.
The grower chat uses real persistent Monitor events and the same facts for Q&A.
The native full-flow bridge reevaluates each QA farm at its own coordinates.
The existing connected ChatGPT plan may phrase weather-only proactive notes
through the same writer. Code supplies exact values, units and source clocks;
invalid/refused wording uses a grounded local fallback. Direct variable questions
and evidence follow-ups remain factual without requiring paid inference.

## Repeatable scenarios

- Recorded baseline: no new discrepancy findings in the 360 March station-hours.
- Davis RH 0% at the starting night hour: immediate strong peer discrepancy.
- Davis average wind 60 mph: immediate strong peer discrepancy.
- Advance six hours to daylight, then Davis solar 1800 W/m²: solar discrepancy.
- Apply the same solar value to all five stations: regional change, no current
  station-to-peer discrepancy. Transient intermediate single-station states are
  separately evaluated because controls submit real individual actions.
- Set a field missing or source-warning: data-health result, no anomaly for that
  reading. Lose usable support for that variable: a variable data-limited note.
- Restore supported values: recovery requires three distinct valid observation
  hours; same-hour resubmissions and missing intervals cannot establish recovery.
- Whole-source orchard support loss keeps the existing consolidated monitoring
  notice, with per-variable detail retained in the evidence, avoiding extra pings.

Solar zero at night remains an observed zero with `status:low_daylight`. The
daylight peer rule is not assessed then; it is neither a sensor fault nor missing
data. Wind means hourly average, not gusts. Weather discrepancies never imply
confirmed sensor failure, crop damage or an irrigation/spray recommendation.

## Provenance and acceptance

The new immutable March variable snapshot is separate from the original
temperature file. Its SHA and retrieval clock are in
`demo-simulation/data/weather-provenance.json`. All 360 overlapping temperature
values match the original snapshot exactly (0.0°F maximum difference). An
additional 120 recent station-hours verified current units/QC and produced no
provisional discrepancy findings. See [source audit](weather-source-audit.json).
This checks source parsing and observed background burden; it does not validate
natural fault sensitivity or prove absence of false positives in other weather.
Thresholds were not tuned to these two snapshots; no reference was refitted.
Historical provider publication/correction visibility is unknown and explicitly
labelled snapshot-only. Missing values are not filled and warnings remain intact.

Use a fresh isolated replay state directory when adopting the additional pinned
source. Existing demo journals are preserved and rejected if their source version
does not match; they are never reset automatically. Existing internal `tara`
API/storage identifiers remain compatible; user-visible naming is Terra.

The implementation chat notified the existing simulation, consolidation and live
runtime owners. Consolidation remains the sole main merge owner; runtime remains
the sole live/SMS/journal owner. A redesigned sim can reuse the versioned action
and assessment contracts above without adopting this interim control composition.
No external SMS or LLM requests are required by the offline replay verification.
