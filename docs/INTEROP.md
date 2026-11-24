# Interoperability layer: logger records, validation, reference fusion and chill

This is the contract between the orchard logger (Arduino Uno + DHT22 in the monkey shield) and
everything that uses its data. Code: `backend/chillwatch/interop/` and `backend/chillwatch/chill/`.
Settings: `config/thresholds.yaml` (`logger`, `fusion`, `chill`, `chill_summary`) and
`config/nodes.yaml` (one entry per logger; copy `config/nodes.example.yaml`).

```
Uno (wire v1, USB serial) -> bridge (host UTC stamp, JSONL) -> readers -> rules (per sample)
   -> hourly (hour-ending UTC, coverage) -> fusion (physics + ML weighted by uncertainty)
   -> scoring (flags, gap estimates) -> chill (chill hours + Chill Portions) -> packet / HTML report
```

One call runs it all: `chillwatch.interop.run(samples, rejects, node, ctx, start, end)`, or from the
shell `chillwatch logger validate capture.jsonl --node orchard-1 --html-out report.html`.

## 1. Record contract (wire v1)

The sketch prints one line per sample at 9600 baud:

```
$CM1,<node_id>,<seq>,<uptime_ms>,<temp_c>,<rh_pct>,<status>,<chill_h>*<CS>
$CM1,orchard-1,42,2520000,4.6,81.2,OK,12.0*7D
```

| Field | Meaning |
| --- | --- |
| `CM1` | record version; anything else is rejected as `wrong_version` |
| `node_id` | must match a registry entry (`unknown_node` otherwise) |
| `seq` | sample counter from boot; restarts at 1 after a reboot |
| `uptime_ms` | `millis()`; wraps after ~49.7 days, handled as modulo 2^32 |
| `temp_c`, `rh_pct` | one decimal; empty when the read failed |
| `status` | `OK` or `ERR_READ` |
| `chill_h` | the device's own running chill-hour count (advisory; recomputed by the backend) |
| `CS` | XOR of every character between `$` and `*`, two upper-case hex digits (NMEA style) |

The Uno has no clock. The bridge (`chillwatch logger bridge --port ... --out capture.jsonl`) stamps
each line with the host's UTC time and appends `{"received_at": ..., "line": ...}` to a JSONL file.
Lines without `$` (boot banners, `#` comments) are skipped. The firmware is checked against this
contract by compiling the real sketch on the host and decoding its output
(`tests/test_interop_tools.py::test_firmware_speaks_wire_v1`).

Hand-logged CSV/JSON (the DIY formats in `chillwatch.sources.diy`) can be read with the same reader
(`read_any`); they carry no `seq`/`uptime`, so only value rules apply.

## 2. Sample rules

Every decoded sample gets `status` (`ok`, `suspect`, `invalid`) and a list of `issues`. Lines that
cannot be decoded go to `rejects` with their issue.

| Issue | Rule (default) | Effect |
| --- | --- | --- |
| `bad_checksum`, `bad_format`, `wrong_version` | decode failed | rejected |
| `unknown_node` | `node_id` is not the node being validated | invalid |
| `sensor_error` | status not `OK`, or no temperature | invalid |
| `out_of_range` | outside -20..50 C | invalid |
| `duplicate` | identical line again within 600 s | invalid |
| `rh_out_of_range` | humidity outside 0-100 % | noted only |
| `reboot` | `seq` did not increase | noted only (event) |
| `seq_gap` | `seq` skipped; counted as lost samples | event |
| `clock_jump` | host time and device uptime disagree by > max(30 s, 5 %) | suspect |
| `step` | > 5 C change between readings <= 10 min apart | suspect |
| `stuck` | same value (+-0.05 C) for >= 3 h | suspect |

A one-sample spike flags both edges (the jump up and the jump back), so the sample after a spike is
also suspect. That costs at most one good minute.

## 3. Hourly series

Samples are grouped by hour-ending UTC (`ts = received_at.ceil("h")`), the same convention as the
CIMIS data the reference uses. With `sample_interval_s` from the registry, an hour expects
3600 / interval samples.

| Hour status | When |
| --- | --- |
| `ok` | ok samples cover >= 75 % of the hour; value = mean of ok samples |
| `suspect` | some ok samples but < 75 % (`low_coverage`), or only suspect samples |
| `invalid` | samples arrived but none were usable |
| `missing` | nothing arrived (`no_samples`) |

`gaps()` merges adjacent missing/invalid hours into runs (`2026-12-01 09:00 .. 11:00, 3 h`).

## 4. Reference expectation: physics and ML weighted by uncertainty

For every hour the ChillWatch reference network (CIMIS stations in `config/region.yaml`, ingested
with `chillwatch ingest`) gives two independent estimates of the temperature at the logger's
coordinates. The logger itself is never one of the inputs.

* **Physics core**: inverse-distance weighting of neighbors after a Theil-Sen lapse-rate correction
  to the logger's elevation, with its held-out error (`est_sd`) from the calibrated uncertainty bins.
* **ML core**: the active LightGBM model (trained on CIMIS, leave-one-station-out residual bins for
  `est_sd`). If no model is active, only the physics core is used, and the report says so.

Both standard deviations are floored at `fusion.min_sd_f` (0.5 F) and combined by minimum variance:

```
w_p = (s_m^2 - rho s_p s_m) / (s_p^2 + s_m^2 - 2 rho s_p s_m)      clipped to [0, 1]
mu  = w_p mu_p + (1 - w_p) mu_m
sd^2 = w_p^2 s_p^2 + w_m^2 s_m^2 + 2 w_p w_m rho s_p s_m
```

With `rho = 0` this is inverse-variance weighting: the core that is more certain in that hour counts
more, and the combined sd is smaller than either. `rho` (correlation of the two cores' errors) is a
setting because both cores see the same neighbors; `estimate_rho()` measures it from paired held-out
residuals. When the cores differ by more than 3 combined sd (`cores_disagree`), the hour is not judged
(`reference_disagrees`) rather than trusting either one.

Tree models cannot predict below the coldest labels they were trained on. Train the ML core on full
winters (the CIMIS model is), or frost nights will look like faults.

## 5. Scoring and gap estimates

* `residual = logger - expected`. The orchard runs warmer or colder than the open, grass-sited
  stations (canopy, shield, slope). That steady difference is learned, not flagged: `offset` is the
  median residual over the trailing 168 h, lagged 24 h, separately for night and day. Before enough
  history exists, the first learned offset of the same phase is used.
* `z = (residual - offset) / sqrt(sd^2 + sensor_sd^2)`, with `sensor_sd_f` from the registry
  (DHT22: 0.45 F).
* **Flags** (Terra's detector rule): one hour with |z| >= 4 (`residual_extreme`), or five consecutive
  hours with |z| >= 2.5 (`residual_persistent`). Missing, invalid or unjudged hours reset the run.
  Flagged ok hours become suspect.
* **Estimates**: any hour that is not ok gets `used_temp_f = expected + offset` with
  `est_sd = sd`, and `is_estimated = true`. Measured ok hours keep their value.

`calibration_summary()` reports how the logger compares with the reference over ok hours: median
difference (overall, night, day), its robust spread, the mean reference sd and the mean physics weight.

## 6. Chill

Season window: `chill.start_mmdd`-`chill.end_mmdd` (Nov 1 - Mar 31) in PST dates, converted to
hour-ending UTC. Temperatures are in C.

* **Chill hours**: 1 for every hour with 0 <= T <= 7.2 C (32-45 F). The prototype's LCD count uses
  the same band on uptime hours.
* **Chill Portions**: the Dynamic Model (Fishman, Erez & Couvillon 1987) with the standard
  parameters. `chillwatch.chill.models` documents the recursion. It matches chillR's `chilling()`
  exactly (chillR takes hour 1 as its starting state), checked by `tests/test_chill_models.py`.

Each total is reported three ways:

| Field | Meaning |
| --- | --- |
| `observed_only` | ok hours only; a lower bound when hours are missing |
| `with_estimates` | ok hours plus estimated hours (the headline number) |
| `p10`, `p90` | 200 Monte Carlo draws of the estimated hours from N(estimate, est_sd), with one shared error per contiguous gap (errors inside a gap are correlated) |

`complete` is true when ok hours cover >= 95 % of the period. With `--target-portions` or
`--target-hours`, `target_reached` requires the p10 to meet the target, so a total that only reaches
it through uncertain estimates is not called reached.

## 7. Calibration test

`chillwatch logger calibrate capture.jsonl reference.csv --node orchard-1` pairs each ok logger
sample with the nearest reference-thermometer reading within 120 s, restricted to 0-10 C (the
testing plan's range). It passes when |bias| <= 0.3 C and >= 95 % of pairs are within 0.3 C. If it
fails only because of a fixed offset (`bias_corrected_share_within` >= 0.95), set
`calibration_offset_c` in `config/nodes.yaml` to `suggested_calibration_offset_c`; the pipeline
subtracts it from every reading before validation.

## 8. Output packet (`chill-monitor-v1`)

`Result.packet()` (and `--json-out`) holds: `node`, `samples` (received / decoded / rejected /
by_status), `stream` (reboots, seq gaps, clock jumps, lost samples), `hours` (by status, gaps),
`issues` (counted separately for samples and hours: an hour carries the issues of its samples),
`reference` (method, model version, agreement) and `chill` (section 6). The HTML report renders the
same packet plus the hourly series on one self-contained page.

## Try it without hardware

```sh
chillwatch logger demo                 # physics core only, ~15 s
chillwatch logger demo --with-model    # trains a small synthetic ML core first, ~40 s
```

Both write `data/logger-demo/report.html` from SYNTHETIC data with injected faults: a 10 h outage,
a +6 C warm fault, a 5 h stuck sensor, one failed read, one corrupted line and one reboot. Every one
of them is caught (`tests/test_interop_pipeline.py`).
