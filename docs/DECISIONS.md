# Decisions and VERIFY items

One line each. "UNVERIFIED" means it could not be checked against a live response: no
`CIMIS_APP_KEY` was available in the build environment (and et.water.ca.gov was intermittently
unreachable), so the item follows the published API docs and must be checked before the demo.
The station list needs no key and **was** verified live (recorded in
`tests/fixtures/cimis_stations_live.json`, 2026-10-23).

## CIMIS API generation (found 2026-10-23)

- **The legacy API the prompt describes (`et.water.ca.gov/api/data?appKey=...`) was announced to run only through 2026-07-31.** CIMIS moved to a new web API (docs: cimis.water.ca.gov/web-api/rest-api/latest, release 2026-05-19). The adapter now defaults to it (`CIMIS_API=new`; `CIMIS_API=legacy` keeps the old path).
- New data endpoint: `GET https://et.water.ca.gov/StationWeb/GetDataByStationNumber?stationNbrs=..&startDate=..&endDate=..&isHourly=true|false&unitOfMeasure=E&dataItems=..`; key in the **`Ocp-Apim-Subscription-Key` header** (not a query parameter). Station list: `GET .../StationWeb/GetAllStations`. Both answer 401 without a key (verified); the legacy `/api/station` still answers without one, so `stations sync` falls back to it when no key is set.
- Response envelope and record shape are unchanged (`Date`, `Hour` `"0100"`, `HlyAirTmp: {Value, Qc, Unit}`), per the docs' examples; units now print as `"(°F)"`. Hour names `hly-air-tmp, hly-rel-hum, hly-dew-pnt, hly-sol-rad, hly-wind-spd` are confirmed in the new docs' item table.
- Record limit refusal is HTTP 400 `ERR2112 DATA VOLUME VIOLATION`; only that halves the window. Other 400s (`ERR1017` invalid station, `ERR1010` future date, ...) raise `CimisBadRequest` immediately. 503/504 are retried.
- **Verified live with a key (2026-10-23)**: hourly records match the parser (`Hour` "0100", `HlyAirTmp {Value, Qc, Unit "(°F)"}`, `HlySolRad` unit "(Ly/d)"); a full Oct 1 - Mar 31 season (4,392 hourly records, 5 items) returns in one request (~3 s), so new-API hourly windows are 183 days (legacy stays at 60).
- Getting a key (new system): register at cimis.water.ca.gov (Azure AD B2C sign-up + a profile form that requires name, phone, street address, city, state, zip), then request the key from the Web API account page.

## VERIFY items from the prompt

- **CIMIS station coordinates** (VERIFIED live): `HmsLatitude` / `HmsLongitude` look like `"38º32'8N / 38.535694"`; code takes the decimal after `/` (`sources/cimis.py:parse_hms_coord`). All 276 stations parse.
- **Elevation**: feet, from `Elevation` (prompt). Stored as `elevation_ft`.
- **ConnectDate / DisconnectDate / IsActive** (VERIFIED live): `m/d/Y` strings; open stations carry `12/31/2050`, treated as "still connected"; `IsActive` is `"True"`/`"False"`. Durham (12) disconnected **2025-07-30**, `IsActive` False.
- **Region** (from the live list): 132 CIMIS stations within 150 km of the five seeds or included (91 active). It reaches Owens Valley and the Central Coast; neighbors are still limited to 80 km.
- **HILL_STATION** for the demo scenario: no high station lies within 80 km of Modesto, so the scenario uses the highest active station within 150 km of Modesto that has ≥ 3 active neighbors within 80 km: **13 Camino (2,780 ft, El Dorado)**.
- **CIMIS firewall**: `/api/data` with an invalid key answers HTTP 200 with an HTML "Request Rejected" page; the adapter raises `CimisAuthError` without retrying.
- **Hourly item names** (VERIFIED in the new API docs): `hly-air-tmp, hly-rel-hum, hly-dew-pnt, hly-sol-rad, hly-wind-spd`; response keys `HlyAirTmp, ...` each `{Value, Qc, Unit}` (docs example; not yet checked against a live keyed response).
- **Response envelope** (UNVERIFIED): `{"Data": {"Providers": [{"Records": [...]}]}}`; anything else (e.g. `{"Message": ...}`) is treated as a refusal and the window is halved.
- **Hour convention** (UNVERIFIED, implemented per prompt): hour-ending `0100`..`2400` PST all year; `ts = Date 00:00 PST + Hour`, so `2400` is next-day 00:00 PST; UTC = PST + 8 h.
- **Units with `unitOfMeasure=E`** (UNVERIFIED): temperatures °F; solar radiation arrives in Ly/day and is converted to W/m² (1 Ly/day = 41,840 J/m² / 86,400 s); wind mph. If a unit string says °C or m/s it is converted.
- **QC codes** (UNVERIFIED): `config/cimis_flags.yaml` maps M, R, I, N, S, Q → severe (invalid) and Y, H, A, P, T → informative (ok); unknown codes → suspect; blank → ok. Every entry carries `# TODO verify`. `A` (historical average replaced) is arguably severe for model labels; decide once the live legend is checked.
- **Record limit**: 1,750 records/request (prompt). Hourly requests use 60-day windows (1,440 records); on any 4xx other than 401/403 the window is halved recursively. 401/403 raise `CimisAuthError` (no retry).
- **Rate limit**: 1 request/s enforced client-side; transient errors (network, 5xx, non-JSON) retried 4x with exponential backoff (tenacity).
- **PRISM** (UNVERIFIED, P2): URL template `https://services.nacse.org/prism/data/get/us/800m/{var}/{yyyymmdd}` is a guess, overridable via `PRISM_URL_TEMPLATE`; terms, daily window and latency not checked. The endpoint failed TLS through the build proxy.

## Model vs physics: what the validation showed (CIMIS, 2026-10-24)

Experiments in `/tmp/claude-0/ablation` during the build; all variants scored on identical rows.

- **Never-seen stations** (GroupKFold by station, seasons 2019-20..2021-22, 99 stations, 1.18M hours): physics (IDW + lapse)
  2.426 F MAE; model with direct target 2.61 F (2.60 F without lat/lon, 2.61 F without lat/lon/elevation); model trained on the
  residual over physics 2.43-2.44 F. The model does not beat physics for places it has no history for.
- **Known stations, later season** (train 2019-20 + 2020-21, test 2021-22, 90 stations, 379k hours): physics 2.39 F; physics +
  per-station night/day offset 2.18 F; model 2.12 F; **model + per-station offset 2.06 F** (median season chill-hour error 81 h vs
  120 h for physics). For QC and gap filling at stations with history, the station-aware model is clearly better.
- Decision: production QC / fill uses the model + learned offsets (as built). Places with no station history (chill map,
  virtual stations, blind tests, the Durham rescue) use the physics estimate with its own leave-stations-out uncertainty bins
  (`model.unseen_method: idw_lapse`; set `model` to switch). Training now reports `holdout_known_stations` (held-out seasons at
  trained stations, with and without offsets) as the headline accuracy.
- **Production model `cimis-v1`** (132 stations, 18 training seasons, 7.21M hours, 839 trees): on the held-out 2023-24 +
  2024-25 seasons at 84 known stations (724k hours, identical rows): model + offset **1.946 F** MAE (night 2.005, day 1.888;
  season chill-hour error median 61 h, mean 90 h, bias -1 h); model 2.009 F; physics + offset 2.023 F (63 h / 103 h);
  physics 2.194 F (104 h / 155 h). Leave-stations-out (never-seen stations, identical rows): model 2.532 F, physics 2.479 F.
- **Durham rescue** (station 12 hidden entirely, 20 pre-closure seasons, fitted physics): 1.96 F MAE, season chill-hour error
  median 80 h / mean 92 h, consistently warm-biased. With Durham's own night/day offset learned from the previous season:
  median 30 h / mean 49 h (2023-24: 887 h predicted vs 880 h observed; 2024-25: 1,058 vs 1,033 h).
- **Fitted physics for places with no station** (`chillwatch fit-physics`, leave-stations-out): grid over lapse shrink
  {0, .25, .5, .75, 1} x elevation-difference weight scale {none, 300, 600, 1000, 2000 ft}, fitted on 2018-19..2022-23.
  Best: shrink 0.5, exp(-(dz/600 ft)^2) weights. Held-out 2023-24 + 2024-25: 2.190 -> 2.141 F MAE (-2.2%), chill median
  103 -> 101 h. The full regional lapse over-corrects across elevation; weighting neighbors at similar elevation helps most.
  The model's own `t_base` feature keeps the spec's estimate so inference matches training.
- An independent review of these experiments found no leakage or scoring bug; it noted the residual models early-stopped
  at 1-6 rounds (so "residual ML ~ physics" is really "physics + a constant"), and that every model variant kept
  station-identifying features, so a fully station-agnostic model remains untested.
- ASOS (`asos-v1`, 83 airport stations, leave-stations-out): model 2.71 F vs physics 2.49 F on their own (not identical) rows,
  consistent with the CIMIS finding.

## Terra integration, hourly offsets, year-round data (2026-10-24)

See `docs/TERRA_COMPATIBILITY.md` for the contract comparison; numbers below come from
`bench/terra_compat/` and `bench/experiments/`.

- **`estimate_location`** (`backend/chillwatch/estimate.py`) is the Terra-facing estimate for a place with no station.
  - Inputs: observations, findings and geometry.
  - Output per hour:
    - the estimate, its method and a parameter-hash version;
    - used and excluded station ids with reasons;
    - lapse stations, data-through and as-of times;
    - status: `ok`, `stale` or `insufficient_data`.
  - Eligibility follows Terra's rule by default (usable, blank CIMIS flag). Findings and readings respect `as_of` and `visible_at`.
  - The lapse is fitted only from stations within 80 km, and only when they span 500 ft or more. On Terra's 129 ft network it
    is off: there it sat at the ±10 °F/kft clip half the time and added nothing.
  - It reproduces the evaluated method exactly (`bench/terra_compat/estimate_check.json`).
- **Hour-of-day offsets**: 24 PST bins, each pulled toward its night/day median with weight n/(n+14).
  - Held-out 2023-25 with weekly 28-day learning: MAE 1.734 → 1.678 °F and chill-hour net error 104 → 97 h vs night/day
    (`bench/experiments/offset_bins.json`).
  - In the mock pipeline they recover an injected pattern exactly.
- **Year-round seasons**: seasons are Sep 1 – Aug 31; April–September 2006–2026 is now ingested (17.3M rows).
  - Season labels follow the configured start.
  - Chill counts Nov 1 – Mar 31.
  - The benchmark, chill map and demo scenario use the Oct–Mar focus window.
  - `cimis-v2`:
    - Training data: Sep–Aug seasons 2005-06..2022-23, 10M sampled rows. Seasons are built one at a time and subsampled as
      they arrive; building them all at once ran out of memory.
    - It is active.
    - Held-out 2023-25, known stations, hour-of-day offsets: Oct–Mar 1.987 → 1.931 °F and Apr–Sep 2.618 → 2.020 °F vs
      `cimis-v1`; chill net error median 69.5 → 60.5 h.
- **Physics refit year-round**: shrink 0.75 with 600 ft weighting (the Oct–Mar fit chose 0.5).
  - Held-out 2023-25: 2.384 → 2.333 °F vs the spec physics.
  - The no-station path now uses p10/p90 bins fitted on its own leave-one-station-out residuals: 79.9% coverage of the
    nominal 80% on held-out hours.
  - On Terra's network they hold 77–93% pooled, but 54–96% at single sites, so the Terra-facing intervals stay off by default.
- **Detection of drifts and offsets**:
  - **Why recall was low:** the spec gate (24 h mean residual > 3 hourly sigma) cannot fire at noisy stations. Station 193
    has an hourly sigma of 2.8 °F, so a 5.3 °F offset's 24 h mean never reaches 8.4 °F.
  - **Offset absorption is not the cause:** a 14-day lag on offset learning changed nothing (drift 25%, offset 38%).
  - **Option added:** `residual.mean24_basis: daily` judges the 24 h mean against the spread of 24 h means.
  - **Causal snapshot harness** (`bench/experiments/drift_offset_snapshots.json`), tuned on 2023-24 and checked on 2024-25:
    the 4× daily gate raises drift recall 31 → 59% and offset recall 23 → 46%; drift/offset false alarms rise 0.07 → 0.24
    per station-month. The full-replay confirmation is in progress.
- **Tests**: the SQL tests default to a separate `chillwatch_test` database. The end-to-end test deletes and rewrites rows
  for synthetic ids that are real CIMIS ids (6 Davis, 70, 71, ...).
- **Mock pipeline** (`bench/experiments/mock_pipeline.py`): the whole pipeline on synthetic data with known truth passes
  10 of 10 checks. It covers interpolation vs baselines, lapse at a foothill site, coverage, offsets, detection, the
  findings handoff and gap filling.

## Design decisions

- **Station ids**: CIMIS stations keep their CIMIS number as `station_id`; other sources get ids from 100000 up (`store.assign_station_id`).
- **Region "recently closed"**: not defined in the prompt; `recently_closed_since: 2005-10-01` (backfill start) in `config/region.yaml` (TODO source).
- **Demo season excluded from training**: `exclude_from_training: ["2025-26"]` in `region.yaml` so the demo never runs on training data (not in the prompt; delete the line to train on it).
- **`qc_base` vs `qc_status`**: readings keep the source-derived status (`qc_base`) separately from the final status (`qc_status` = `qc_base` combined with detector flags). Detection reads only `qc_base`, which is what makes it idempotent (a second run never sees its own flags). Neighbor validity for expected values uses `qc_status` (flagged neighbors are dropped).
- **Two-pass detection**: pass A computes expected values with neighbors validated by source QC only; any station that pass A marks invalid is removed from its neighbors' inputs and those neighbors are re-tested (pass B). Without this a stuck neighbor raised a false offset alarm on the station next to it.
- **Target leak through the lapse rate**: the hourly Theil–Sen lapse rate is fitted across all region stations, which would include the target's own reading. Features for a station use the lapse rate refitted without that station (`Field.gamma_excluding`). Caught by `tests/test_model.py::test_features_never_use_target_value`.
- **Night**: 18:00–06:00 PST = hour-ending 19..23 and 0..6.
- **Model use threshold**: the model is used when ≥ 1 valid neighbor exists; the IDW baseline needs ≥ 2 (prompt); residual tests need ≥ 2 (prompt).
- **Huber delta**: LightGBM default `alpha=0.9` (°F) — not in the prompt (TODO source).
- **Early stopping**: 15% of stations held out (GroupShuffleSplit) for early stopping (TODO source); LOSO metrics use GroupKFold by station (5 folds by default, `--folds 0` for true leave-one-station-out) with the round count from the main fit.
- **Training "rule-invalid" rows**: source-severe flags (via `qc_status`), range, and stuck runs (identical ≥ 6 h while the neighbor median moves > 2 °F) are dropped from labels.
- **Uncertainty**: per-bin (nearest-valid-neighbor distance × its |dz| × night/day) robust sd (1.4826·MAD) and 10th/90th residual percentiles from out-of-fold residuals (split-conformal style); bins with < 200 residuals fall back to the pooled night/day bin (TODO source). Separate tables for the model and for the IDW fallback.
- **sigma_s prior**: with < 168 ok residuals in the 60-day window (e.g. the first week of a season — the backfill starts Oct 1, so there is no September history) sigma_s falls back to the model's LOSO sd (TODO source).
- **Offset vs drift** (deviation): the prompt says "offset if the change-point is within 6 h". On synthetic data a clean 4 °F step was fitted as a 12–24 h ramp (hourly residuals carry diurnal structure), i.e. called drift. Fitted ramps ≤ 48 h are now also called offsets (`residual.ambiguous_ramp_hours`, TODO source); benchmark drifts ramp over ≥ 3 days.
- **Spikes inside a shifted period** are not reported if they are within 6 sigma of the shifted level (otherwise a 4 °F offset produced a stream of spike detections).
- **Clock shift** (deviation): cross-correlates the station against its *expected* series rather than the raw neighbor median; neighbors at other elevations peak at other hours, so the raw median produced false 1–2 h lags on synthetic data. Requires ≥ 48 of the 72 hours.
- **Offline**: a gap is only reported once a station has reported at least once (no alarms for the period before data starts). Closed gaps ≥ 36 h that ended inside the lookback window are reported with `end_ts` set.
- **System outage**: when it fires, the per-station ongoing `offline` detections it covers are suppressed (one detection, `station_id = 0`, `evidence.stations_offline` lists them).
- **Severities** other than offline are not in the prompt; set in `thresholds.yaml` (`severity:` block, TODO source). Drift/offset become critical above 6 sigma.
- **drift/offset/clock-shift readings → invalid** so repair replaces them (otherwise "repaired" chill hours would equal "unrepaired"); step → suspect (kept, not replaced).
- **Onset of drift** is refined as post-alarm data arrives, so `start_ts` of an ongoing drift can move by hours between daily runs (same data → same detections still holds). Consumers should match ongoing drift/offset detections by (station, type, overlap).
- **Learned offsets** are applied by `merge_asof` on `window_end` (an offset only affects hours after its window); an `accepted` offset takes precedence over a `learned` one. With an accepted offset, drift/offset re-alarm only when |residual + accepted| > 1.5·|accepted|.
- **Chill hours (evaluation only)**: 32 ≤ T ≤ 45 °F, hours from Nov 1 PST to season end, counted only over hours where the reference value exists so totals compare like with like.
- **Fault injection**: drift ramps over `days`, then holds for `hold_days` (bench: 3) and ends; offsets in the bench last 7–21 days; clock shifts 3–14 days. Faults on one station never overlap (3-day padding).
- **Bench false alarms**: counted twice — on stations with no injected fault (the prompt's definition), and as all detections not explained by an injected fault. On real data some are genuine station problems.
- **Expected-value precompute in replays**: expected values at hour t only use neighbor values at hour t, so the bench/demo precompute them once per season over the faulty data (source-QC validity), then recompute live only for stations next to a detector-flagged station.

## Constants added that are not in the prompt (all `TODO source` in config)

`neighbors.min_distance_km 0.5`, `step.confirm_sigma 3`, `stuck.resolution_f 0.01`, `residual.min_sigma_hours 168`,
`residual.ambiguous_ramp_hours 48`, `severity.*`, `model.huber_alpha 0.9`, `model.num_boost_round 2000`,
`model.early_stopping_rounds 50`, `model.valid_station_fraction 0.15`, `model.blind_max_train_rows 1.5M`,
`uncertainty.min_bin_count 200`, DIY daily max/min coverage 18 of 24 h, `region.recently_closed_since`,
`region.exclude_from_training`.
