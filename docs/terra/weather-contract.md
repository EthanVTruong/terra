# Terra weather discrepancy contract

The `terra-weather-assessment-v1` envelope adds relative humidity, wind speed and
solar radiation to station monitoring. The existing temperature reference,
temperature classification, location estimate and chill calculation are not
modified. These are observed **station** measurements: they are not estimated
humidity, wind or solar radiation at an unsensored orchard.

`backend/terra_monitoring/weather.py` owns the pure functions. The provider
adapter owns station identity, fixed-PST hour-ending verification, raw-response
retention and first-seen publication visibility. The monitor owns incident
lifecycle, deduplication, delivery and recovery. The simulation uses the same
normalization and assessment functions as current observations.

## Provider and canonical fields

| CIMIS field | Canonical field | Canonical unit | Accepted source units |
|---|---|---|---|
| `HlyRelHum` | `relative_humidity_pct` | `%` | `%`, `pct`, `percent`, with optional parentheses |
| `HlyWindSpd` | `wind_speed_mph` | `mph` | `mph`, `m/s`, `km/h`, recognized explicit equivalents |
| `HlySolRad` | `solar_radiation_w_m2` | `W/m²` | `W/m2`, `W/m²`, `W/m^2`, `Ly/d`, `Ly/day`, recognized explicit equivalents |

The wind measurement is hourly wind speed, not gust speed. `Ly/d` is the rate
unit supplied for an hourly solar measurement; conversion to `W/m²` multiplies by
`41840 / 86400`. It is not an instruction to sum another day's energy. A value
already canonical is never converted again using retained raw units.

`normalize_cimis_weather(record)` returns the three canonical values and, for
each field, these suffixes:

- `_unit`: the canonical unit.
- `_source_value`, `_source_unit`, `_source_flag`: the original source evidence.
- `_quality`, `_available`, `_availability_reason`, `_availability_reasons`:
  variable-specific eligibility.

An absent value, unit or QC code is not assumed usable. All nonblank source QC
codes remain visible and are unavailable for this initial peer rule policy.
Codes are not diagnosed as confirmed station faults. Missing/not-in-service
codes, pending QC, other source warnings and unknown QC have distinct reasons.
Temperature QC never automatically invalidates another variable, nor does a
humidity warning automatically invalidate temperature, wind or solar.

## Assessment input and output

```python
weather = assess_weather(
    observations,  # DataFrame or list of normalized row dicts
    stations,      # DataFrame/list: station_id, lat, lon; optional operating dates
    valid_time_utc=observation_hour,
    as_of_utc=actual_or_simulated_clock,
    max_age_hours=6,
)
```

Observation rows require `station_id`, an aware hourly `timestamp`,
`timestamp_verified=True`, the canonical variable values and their individual
metadata. Optional `visible_at` gives the actual first-seen revision time. Only
rows observed and visible by the assessment clock enter comparisons. The latest
visible revision wins; equal-visibility conflicting duplicate values invalidate
only the affected variable. Equivalent duplicates collapse deterministically.

The returned finite JSON contains:

- `schema_version`, `policy_version`, `validation_status`, `scope`,
  `valid_time_utc`, `as_of_utc`, `maximum_age_hours`, and shared `policy` metadata.
- `variables`: one object per variable with `label`, `unit`, `departure_unit`,
  `thresholds`, and `stations`.
- Each station result contains `station_id`, retained `observed`, peer-median
  `expected`, `departure`, `peer_count`, `used_station_ids`, `status`, `reason`,
  `classification`, `level`, and source value/unit/QC/quality evidence.
- `findings`: current-hour causal numerical discrepancies. Every finding names
  its variable, station, unit, departure, evidence hours, policy, severity and
  `cause: "unconfirmed"`.
- `data_health`: separate per-variable missing, invalid, source-warned, stale,
  unverified or insufficient-peer evidence. Original problematic rows remain
  attached as `raw_source_rows`.
- `audit`: duplicate/conflict/discard/quarantine evidence and explicit
  `gap_filling: false`, `temperature_policy_changed: false`.

Station `status` is `assessable`, `unavailable`, `stale` or `low_daylight`.
`classification` is `no_alert` for an assessable hour without a triggered rule,
`suspected_discrepancy` when triggered, and null when withheld. `level` is
`none`, `moderate` or `strong` independently of temporal triggering; it must not
be mistaken for an incident on its own.

## Initial rule policy and limits

The versioned `config/terra/weather-policy-v1.json` is explicit about its
`provisional_conservative_peer_discrepancy_rules` validation status. These
thresholds are initial engineering rules, not trained ranges, naturally labelled
sensor-fault accuracy or agronomic danger thresholds.

| Variable | Moderate absolute station/peer departure | Strong absolute departure |
|---|---:|---:|
| Relative humidity | 30 percentage points for 3 consecutive hours | 60 percentage points in one hour |
| Wind speed | 12 mph for 3 consecutive hours | 25 mph in one hour |
| Solar radiation | 350 W/m² for 3 consecutive hours | 650 W/m² in one hour |

Each comparison requires at least three other usable, operating, simultaneous
stations within 75 km. The target is excluded from its own peer median. A
sustained run must remain on the same side of its peers. Missing, unavailable,
below-threshold or opposite-direction hours reset the run. A simultaneous
regional change shared by peers does not create a station-departure finding.

Solar classification additionally requires a peer median of at least 100 W/m².
Zero at night remains a valid observation with `low_daylight` context; it is not
missing data and does not prove recovery from a daytime discrepancy. Relative
humidity outside 0–100% and negative wind become data-quality issues rather than
numerical anomalies. Negative solar also becomes unavailable, with the distinct
reason `below_policy_minimum`: small negative solar noise is possible, so this
is a conservative application eligibility rule, not a claim that every negative
source value is physically impossible. Very high nonnegative wind/solar alone is
not declared physically invalid.

Current assessments older than the configured age limit retain historical values
with `stale` status and no new numerical findings. Historical replay should use
its actual simulated observation clock, preserving causality.

## Simulation and agent handoff

Use normalized source-shaped input for each simulated variable and mark the
session/data mode as synthetic or injected. Change the selected variable's value
without fabricating QC warnings; inject missing/QC conditions separately. Pass
successive distinct observation hours through the same assessment. Hold station
identity, location and source metadata fixed unless deliberately testing them.

Required controls demonstrate baseline/noise, region-wide change, single-station
moderate departure through three hours, one strong departure, a missing value,
a source QC warning, insufficient peers, and daylight versus nighttime solar.
Keep `findings` and `data_health` separate in displays, agent facts and any event
delivery. Numerical discrepancies say the station differed from its peers and
the cause is unconfirmed. They do not assert a broken sensor or a crop-specific
weather hazard. No extra-variable readings may be inferred from a temperature
slider or a temperature-only saved dataset.

The pure assessor reports the current causal evidence window. The persistent
monitor is responsible for opening one incident per station/variable,
deduplicating continued departures and recording a recovery only from distinct
assessable, below-threshold observations. A gap or unavailable/nighttime solar
comparison cannot establish numerical recovery.

## Verification

`tests/test_terra_weather.py` exercises source unit/QC independence, normalized
original reasons, invalid values, normal/regional controls, moderate/extreme
rules, sustained resets, unavailable peers, operating dates, solar nighttime,
staleness, variable-specific duplicate conflicts, causal revisions, future
observations, timestamp trust, temperature independence and finite JSON.

These are deterministic software and synthetic controls. They do not establish
field precision/recall, absence of all false positives, or crop-risk accuracy.

## Primary source references

- [CIMIS current REST API](https://cimis.water.ca.gov/web-api/rest-api/latest):
  current data-item names include `hly-rel-hum`, `hly-wind-spd` and `hly-sol-rad`.
- [CIMIS data overview](https://cimis.water.ca.gov/resources/data-overview) and
  [current QC flags](https://cimis.water.ca.gov/docs/CurrentFlags.pdf): general
  missing/not-collected/pending codes are separate from variable-specific tests.
  The same QC letter can describe different checks for different parameters;
  Terra retains the code rather than inventing a precise cause. Humidity QC may
  inherit air-temperature/vapor-pressure warnings; low or constant wind and
  solar tests must not be reinterpreted as proven failed sensors. Solar provider
  QC accounts for daylight and the sun, supporting the explicit daylight gate
  and caution about small negative values here.

These sources were checked on October 24, 2026. Provider codes remain evidence,
not natural fault labels for validating the new detector.
