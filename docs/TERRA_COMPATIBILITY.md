# ChillWatch estimator vs Terra's pipeline: compatibility report

Checked 2026-10-24 against `terra-nathan-agent-handoff.md` (dated 2026-10-23). ChillWatch code at commit `14e1250` (same tree as the `daa451a` named in earlier copies).

**Where the numbers come from:**
- Validation figures: `bench/terra_compat/`, i.e. its scripts plus `eval.json`, `extra.json`, `stats.json` and `checks.json`.
- Model figures: `data/models/cimis-v1/metrics.json` and `data/models/physics_fit.json`.
- Durham coverage: `bench/results/durham_rescue_cimis.json`.
- `data/` and `bench/results/*.json` are git-ignored, so hashes are given below.

Cross-owner changes are **proposals only**. Nothing on ET's side was changed.

## Status after the follow-up work (later on 2026-10-24)

The ChillWatch-side changes proposed below are now built in `backend/chillwatch/estimate.py` (`estimate_location`).
Details and numbers are in `docs/DECISIONS.md` ("Terra integration, hourly offsets, year-round data").

| Gap below | Now |
|---|---|
| 1. Output fields | **Done.** Location id (`TEST-…` when no orchard is given), geometry source, hour, °F, method, a parameter-hash version, used and excluded station ids with reasons (held-out target, Terra finding, quality, source flag, missing, out of range), lapse stations, data-through, as-of and status. |
| 2. Terra findings | **Done.** Findings exclude readings in a single pass. A finding with an unknown visibility time still counts. |
| 3. `Y` flags | Terra's rule (blank flags only) is the default; `usable_flags` can keep `Y`. The decision is still ET's. |
| 4. Unapproved predictors | Unchanged: the Terra path uses air temperature only, and LightGBM stays out. |
| 5. Intervals | **Calibrated** on the physics fit's leave-one-station-out residuals (region-wide lapse): 79.9% of held-out 2023-25 hours at all region stations fall inside the nominal 80%. With `estimate_location`: 77–86% pooled on Terra's network, 83–93% at the ASOS sites, 54–98% at single sites. **Off by default** (`estimate.intervals`). When on, `method_version` includes a hash of the bin table. |
| 6. As-of / freshness | **Done.** `as_of` and `visible_at` are respected. A reading not yet visible is treated as absent. There is no forecast past `as_of`, and `stale` is returned past `max_age_hours`. |
| 7. October–March only | **Done.** April–September 2006–2026 is ingested and seasons are September–August years. On Terra's stations, each estimated from the other four: 1.78 °F Oct–Mar 2024-25 and 2.34 °F Apr–Aug 2025 (peer median 1.86 / 2.29 °F). In summer the Delta site (212) reads 3.3 °F cooler than the other four predict (estimate − truth = +3.3 °F). |

Other items:
- **Lapse correction:** off on Terra's network. It applies only when the stations within 80 km span 500 ft or more.
- **Peer minimum:** `insufficient_data` below 3 usable peers.
- **Known-station offsets:** now by hour of day.
- **March 2026:** nothing in the follow-up work was fitted on it. But the proposals it led to are now built: the lapse span gate, Terra's flag rule as the default, and hour-of-day offsets. `bench/terra_compat/intervals.json` also reports on it. So, as the windows table says, it is not a fresh holdout for the new configuration.

Superseded in the report below (it describes commit `14e1250`):
- **Known-station model:** `cimis-v2` is active. It was trained year-round on Sep–Aug seasons 2005-06..2022-23, through 2023-09-01. Its hashes:
  - `model.txt`: `b474d05e3a7fd177b7a2e5d86cf6b289a3c950f14b70790eed270c8c6e2822b8`
  - `uncertainty.json`: `c531dedef6aec4baf1c8ad278d950b6eb8c286ae62377f900b2d7f04562a9180`
  - `features.json`: unchanged
- **`data/models/physics_fit.json`** now holds the year-round fit:
  - Settings: lapse ×0.75, 600 ft weighting, with the p10/p90 bins.
  - Fitted on Sep–Aug 2018-19..2022-23 and checked on 2023-24 + 2024-25: 2.333 °F, vs 2.384 °F for the spec physics.
  - SHA-256 `37633cc95ce00dc5a645c84d244050160d60292fd8593ff768de37552cccda1a`.
  - The Oct–Mar fit quoted below (×0.5, 2.14 °F, SHA-256 `be0ba06f…`) is kept as `physics_fit_octmar_2026-10-24.json`. It has no bins.
- **Windows:**
  - Training and the physics fit now use data before 2023-09-01, not 2023-04-01.
  - The holdout seasons 2023-24 and 2024-25 now include April–September. They cover all of Terra's offset-fit window (2023-09-01 → 2025-09-01), not only its Oct–Mar parts.
  - The 2025-26 season starts 2025-09-01. It is still excluded from training.
- **Season coverage:** year-round (gap 7 above). The Oct–Mar-only statements below describe the earlier state.

## Verdict

**The estimator can be adapted to Terra. It is not compatible yet.**

What already lines up:
- **Same inputs and conventions.** Same CIMIS source, station geometry identical to the handoff table, canonical UTC hour-ending time, °F.
- **No leakage.** For each of Terra's five stations, the station's own readings and flags were replaced with noise over three days. All 21 estimator outputs stayed identical (`checks.json`).
- **Matches ET's published check point.** The Terra-style comparator, rebuilt from the handoff text on our rows, gives Davis an expected 37.65 °F at 2026-01-11T09:00Z (peer median 36.5 °F + Jan/hour-1 offset 1.15 °F). That is ET's value. ET's band and residual were not reproduced.

Seven contract gaps:
1. Output fields.
2. No input path for Terra findings.
3. CIMIS `Y`-flag eligibility differs.
4. The LightGBM path needs predictors Terra has not approved.
5. p10/p90 intervals are uncalibrated.
6. No as-of/freshness handling.
7. Data, model and validation cover October–March only.

Scope overlap:
- The ChillWatch repo also contains ingestion, Postgres storage, station QC/fault detection, gap repair, chill, replay and a map.
- These duplicate ET-owned parts. Proposal: integrate only the estimator.

Estimating at places with no station:
- **Test sites:** six ASOS airport sensors near Davis. They are not orchards and not on-orchard ground truth.
- **Baseline:** an unweighted five-station median that Nathan built. Terra produces no estimate at unobserved locations.
- **Result:** using only Terra's five stations, our inverse-distance estimate errs by 1.87 °F MAE vs 2.06 °F for the median (March 2026). In Terra's inspected winter it is 1.27 vs 1.42 °F.
- **Lapse correction:** adds nothing on Terra's low-elevation network, where it sits at the ±10 °F/kft clip about half the time. Proposal: switch it off there.

## Artifacts: present and verified

| Artifact | Status |
|---|---|
| `terra_app/`, `terra.py`, `AGENTS.md`, `COORDINATION.md`, `docs/data-contract-v3.json`, `docs/source-alignment.json` | **Absent.** Checked EthanVTruong/terra at `main` 8c953f6b5597404d5ccf6dbd6d8134cd76fb70d5, branch `chillwatch-data-ml-layer` and PR #1 head 6f2ff9102db90c8f50378c5dbc6cd6dfbe252434, plus the local checkout. No other Terra repository is accessible from this session. |
| QC reference `terra-qc-v3-9365da1e44e26886` (`…-c120e2336695109a.reference.json`, SHA-256 `c120e2336695109a8b5e7be586d194862ed8351296bc25ec4ef31dedf7cf8d31`) + metadata JSON | **Absent, not verified.** |
| Dataset `cimis-hourly-4d152b359ac00bc3` (alias `cimis-hourly-residual-v3-4d152b359ac00bc3`), SHA-256 `4d152b359ac00bc343137315eccbe77737d3a3324a363b5b7cae659e7f1cd741` | **Absent, not verified.** |
| SQLite `model:cimis` pointer | **Absent.** |
| `data/models/cimis-v1/model.txt` | Present, `08e845f8d2d09b3500b908ed66caa2d8893e088df8488fd87569667692d4124b` |
| `data/models/cimis-v1/features.json` | Present, `59d59a967a2685b4b28feb8402f6dc39d47a15da55e7e7dcd9d119730273cadf` |
| `data/models/cimis-v1/uncertainty.json` | Present, `33da171cb48451278f2ffb0b37fb77e55764192d45f6a8d97a3a9822dc609921` |
| `data/models/physics_fit.json` | Present, `be0ba06f083b77c01c3d4d6edad4d87f84712746cbdcd42400bfe983f5dae31a` |
| `config/thresholds.yaml` / `config/region.yaml` | Present, `fbafce4dbdcd8ac801b84fca0d0b2e9b12cef18eb9166ed6b6918a5a49de0228` / `48856a0f188c434d4e8168b8b9b4bc9e1430bb2e3ede5fc8f7b71c68cc548e41` |

What the validation used instead of ET's artifacts:
- **Data:** ChillWatch's own Postgres rows for the five stations, from the same CIMIS source. That is 13,127 hourly rows per station in [2023-10-01T08Z, 2026-04-01T08Z), Oct–Mar only, compared with ET's 109,555-row full-year dataset.
- **Comparators** labelled *Terra-style* are re-implementations from the handoff text, run on those rows.
  - They cover Oct–Mar month bins only and have no fallback offset.
  - No new acquisition or training run was made.

## What the engine predicts

| | Known station (QC "expected") | Place with no station (orchard, map cell, virtual station) |
|---|---|---|
| Output | Hourly expected air temperature, °F, UTC hour-ending | Same |
| Method | LightGBM `cimis-v1` (34 features) + per-station night/day median offset | 1/d² IDW over the 6 nearest active stations within 80 km. Hourly region Theil–Sen lapse × 0.5. Weight × exp(−(dz/600 ft)²). |
| Inputs | Neighbor air temp, **neighbor RH, dew point, solar, wind**, geometry, time of day/year | Neighbor air temp + geometry only |
| Stations | 132 CIMIS stations within 150 km of Davis, Manteca, Modesto, Parlier, Shafter (+ Durham). Terra's five are a subset. | Same |
| Fit / tune | Trained 2005-06..2022-23, Oct 1–Mar 31, on all region stations (Terra's five included); early stopping on held-out stations | Two scalars, leave-one-station-out over 2018-19..2022-23 (92–100 targets per season, Terra's five included) |
| Tested | Holdout 2023-24 + 2024-25, 84 stations: 1.95 °F MAE with offset | Same seasons, ~89 stations leave-one-out: 2.14 °F |
| Insufficient data | Model runs with ≥ 1 valid neighbor | `method = none` below 2 valid neighbors |
| Contract fit | Needs predictors Terra has not approved; dew point is temperature-derived | Air temperature only: fits Terra's approved dataset |

**Time:**
- CIMIS Date + Hour is fixed PST and converted as UTC = PST + 8 h, with 2400 → next-day 00:00.
- Timestamps are stored tz-aware.
- **Season limit:** ChillWatch holds and models Oct 1–Mar 31 only. Nothing is validated for April–September or for the Sep 1 chill start that FNRIC uses.

**Units and geometry:**
- Temperatures °F; elevations in feet.
- Elevation sources: stations use CIMIS metadata, ASOS test sites use IEM metadata (metres → feet), map cells use the Terrarium DEM (zoom 9, ~300 m).

**Not used:** ChillWatch reads no FNRIC data.

## Windows and overlap

Windows below are the handoff's fixed-PST intervals written in UTC. They start and end at 08:00Z and are half-open on hour-ending stamps.

| Window | Terra use | ChillWatch use |
|---|---|---|
| before 2023-04-01 | — | Model training, physics fit |
| 2023-09-01 → 2025-09-01 | Offset fit | Holdout seasons 2023-24, 2024-25 (only their Oct–Mar parts exist in our DB); physics check; Durham and bench evaluation |
| 2025-09-01 → 2025-12-01 | Residual calibration | 2025-26 season from 2025-10-01 (excluded from training) |
| 2025-12-01 → 2026-03-01 | Inspected winter | 2025-26 season: demo, estimated chill map |
| 2026-01-10 → 2026-01-14 | Saved replay | — |
| 2026-03-01 → 2026-04-01 | None (after Terra's dataset) | Not fit on. Before this report it was used only to draw the estimated chill map. This report has now inspected it and drew proposals from it (lapse off, `Y` handling, hour-of-day offsets), so it is no longer a fresh holdout for the proposed configuration. |

**Implications for future claims:**
- A new generalization claim needs data after 2026-04-01T08:00Z, such as the 2026-27 season.
- ChillWatch's holdout seasons sit inside Terra's offset-fit window, so they are not a clean holdout for any joint claim that uses Terra offsets.

## Contract checklist

| Contract item | ChillWatch today | Smallest change (owner) |
|---|---|---|
| Shared inputs | Reads CIMIS itself. No `source`, `timestamp_verified`, `timestamp_basis` or geometry provenance. | A proposed estimator consumes Terra's normalized observations (Nathan). Terra exposes a read interface (ET, proposal). |
| Time alignment | Same CIMIS convention as Terra. | When consuming Terra observations, take Terra's aligned UTC as given; never re-apply +8 h or FNRIC +1 h (Nathan). |
| Season coverage | Oct–Mar only. | Return `insufficient_data` outside Oct–Mar until Apr–Sep is ingested and validated (Nathan). |
| Eligibility | Keeps CIMIS `Y` (and `H`, `A`, `P`, `T`) as usable. Terra drops every non-blank flag. | See finding D. Decide `Y` handling (ET). Never use `T`/`A` rows as neighbors or truth; none are present today (Nathan). |
| Terra findings | No input path. ChillWatch's own two-pass detection re-flags neighbors, which is the "remove neighbors until the network agrees" pattern. | The estimator takes an explicit exclusion list (station, hour, reason) from Terra findings in a single pass. Our detection is not used in integration (Nathan). The effect of excluding real Terra findings is unmeasured until Terra's artifacts are shared. |
| Freshness / as-of | None. The map's "season to date" stops at the last hour with any data. | `as_of` argument: use only measurements with ts ≤ as_of, report data-through per used station, and return `stale` past an agreed maximum age (Nathan; age limit with ET). Causality in Terra's as-of clock holds only if Terra's findings are as-of aware (ET, proposal). |
| Causality | The estimator is per-hour, so a prefix run matches a full run by construction. The production path's detection can re-flag past hours later. | Eligibility comes from Terra findings only (Nathan). |
| Output | `ts, expected_f, est_sd, p10, p90, n_neighbors, method` | Add the fields in "Proposed output" (Nathan). |
| Insufficient data | IDW: below 2 valid neighbors. Model: below 1. | `status = insufficient_data` below 3 usable peers, matching Terra's QC rule (Nathan). |
| Uncertainty | p10–p90 (nominal 80 %) covered 84 % (March) / 93 % (winter) at held-out stations; Durham 73–91 % over 20 seasons. The bins were fit on a different estimator than the one they wrap. | Withhold intervals from contract output until recalibrated per method on a pre-test window (Nathan). |
| Chill | Counts hourly 32–45 °F (inclusive) from Nov 1 on any hourly series, including gap-filled estimated hours in station series. FNRIC sums daily `hours_32_45` from Sep 1. | Never label our counts as station chill or replace FNRIC totals. Orchard chill from estimates is named and versioned, and carries `is_estimated` plus missing hours (Nathan). |
| Lapse correction | Applied × 0.5 everywhere. | Off on Terra's network, or gated on elevation spread (Nathan). Evidence in A. |
| Geometry | Identical to the handoff for all five stations. No FNRIC ID or provenance stored. | Join Terra geometry by station ID with its provenance; test locations carry their elevation source (Nathan). |
| Scope | Duplicates ingestion, storage, QC, repair, chill, replay, map. | Integrate only a proposed `estimate_location`. The rest stays outside Terra (Nathan; confirm with ET). |

## Validation case (run)

**Design:**
- Every target is excluded from neighbors, the lapse fit and features. The scramble test confirms this.
- Methods are compared on the hours where all of them produce an estimate.
- 95 % intervals come from a day-block bootstrap (PST days) of the paired MAE difference.
- Truth is *Terra-style eligible* readings: ChillWatch `qc_base` ok and a blank CIMIS flag. Scoring against ChillWatch-eligible truth gives the same ordering.
- Windows: March 2026 (not fit on, now inspected) and Terra's inspected winter.

### A. Places with no station (MAE, °F)

| | ASOS ×6, Mar 2026 | ASOS ×6, winter | CIMIS Delta ×4, Mar 2026 | CIMIS Delta ×4, winter |
|---|---|---|---|---|
| Unweighted 5-station median (≥3), baseline | 2.06 | 1.42 | 2.44 | 1.73 |
| Ours, Terra's 5 stations, lapse off | 1.86 | 1.27 | 2.14 | 1.48 |
| Ours, Terra's 5 stations, lapse ×0.5 (current) | 1.87 | 1.27 | 2.15 | 1.49 |
| Ours, full network, Terra-style eligibility | 1.85 | 1.19 | 1.67 | 0.92 |
| Ours, full network, `Y` kept | 1.79 | 1.18 | 1.66 | 0.92 |

**Test sites:**
- ASOS EDU, DWA, SMF, SAC, VCB, SUU.
  - They were not used to fit the CIMIS estimator or the physics parameters tested here. They were in ChillWatch's separate ASOS model.
  - ASOS truth is a single :53 report per hour. CIMIS values are hourly averages.
- CIMIS Delta stations 140, 242, 243, 247 were in model training and the physics fit (time-separated).

**Ours (5 stations, current) − median:**
- −0.19 °F [−0.26, −0.14] in March.
- −0.15 [−0.20, −0.11] in winter (inspected).

**Availability** over March ASOS truth hours:
- median 78 %;
- ours on 5 stations 86 % under the current 2-neighbor rule, and 78 % under the proposed 3-peer rule;
- full network 87 % (Terra-style eligibility) / 100 % (`Y` kept).

**Full network, March ASOS sites:** with Terra-style eligibility the gain over 5 stations is not significant (−0.02 [−0.05, +0.01]). It reaches −0.14 [−0.24, −0.05] only when `Y` readings are kept.

**Lapse:**
- On the 5-station field it is finite in 61 % (March) / 87 % (winter) of hours.
- It is clipped at ±10 °F/kft in 44 % / 51 % of hours.
- Turning it on changes MAE by +0.009 °F [+0.003, +0.014] (March) and −0.001 [−0.004, +0.002] (winter).

### B. Terra stations held out, each estimated from the others (MAE, °F)

| | Mar 2026 (2,483 h) | Winter (10,050 h) |
|---|---|---|
| Terra-style 4-peer median | 2.07 | 1.49 |
| Ours, same 4 peers (IDW; lapse undefined with 4 stations) | 1.97 | 1.41 |
| Ours, full network | 1.78 | 1.27 |

**Same 4 peers:**
- −0.09 [−0.14, −0.05] in March and −0.08 [−0.10, −0.06] in winter.
- Worse at Hastings Tract East (212) and Verona (235) in March, by 0.12 and 0.15 °F.

**Full network:**
- −0.29 [−0.36, −0.22] and −0.22 [−0.27, −0.17].
- The gain comes from stations Terra does not have: Delta stations 140, 242, 243 and 247 cut 212's error by 0.5–1.0 °F.
- The full network makes Verona worse in March (+0.22 [+0.04, +0.45]).
- The two physics parameters were fit in 2018–2023 with these stations among the leave-one-out targets. They are time-separated, not strictly spatially held out.

### C. Known station, target history allowed (MAE, °F)

| | Mar 2026 | Winter |
|---|---|---|
| Terra-style: 4-peer median + month×hour offset (Oct–Mar bins of Terra's fit window) | 1.75 | 1.37 |
| Ours: model + night/day offset | 1.49 | 1.12 |
| Ours: model + offset, Terra-style eligibility throughout | 1.50 | 1.13 |

**Model − Terra-style:** −0.26 [−0.36, −0.18] in March; −0.25 [−0.29, −0.20] in winter.

**Not a like-for-like comparison:**
- The model trained on these stations' 2005–2023 history and uses the unapproved predictors.
- The Terra-style reference uses two seasons.

**Chill-range hours.** Dec–Feb counts of 32–45 °F hours recomputed from CIMIS hourly readings, about 2,000 common hours per station (observed 733–904); these are not FNRIC `hours_32_45`.
- Net error per station: 19 h (Terra-style) vs 21 h (ours).
- Gross misclassified hours: 130 vs 99.
- Month×hour bins track the daily bias cycle better for net counts, so hour-of-day offsets are a candidate change on our side.

### D. Eligibility: CIMIS `Y` flags

**How common Y flags are:**
- In March 2026, 786 of 3,720 rows at the five stations carry `Y` (21 %), averaging 72.5 °F vs 60.8 °F.
- About 72 % fall on Mar 15–24.

**Checked against each station's nearest ASOS sensor** (`checks.json`), `Y` hours vs blank hours, March:

| CIMIS station | ASOS | Distance | MAE blank / `Y` | Mean diff blank / `Y` |
|---|---|---|---|---|
| Davis (6) | EDU | 1.0 km | 1.24 / 1.47 °F | +0.55 / +0.71 |
| Woodland (226) | DWA | 11.1 km | 2.01 / 2.69 | +1.00 / +1.56 |
| Winters (139) | DWA | 13.7 km | 2.52 / **6.22** | +0.68 / **+5.79** |
| Hastings Tract East (212) | SUU | 16.4 km | 2.00 / 1.98 | −0.71 / −0.33 |
| Verona (235) | SMF | 11.5 km | 2.15 / 1.50 | −1.57 / −0.27 |

**What it shows:**
- At four stations, `Y` hours agree with an independent sensor about as well as blank hours, which fits a regional warm spell.
- At Winters the 77 `Y` hours run 5.8 °F warmer than the nearest ASOS, so some of those may be real faults.
- Neither blanket rule is right.

**Effect of Terra's rule:**
- The Terra-style 4-peer median is unavailable in 28 % of March hours overall, and in 14 % of hours where the target is usable.
- **Accuracy on usable hours:** pooled change is near zero (−0.04 [−0.10, +0.01] in March; ~0 in winter). Individual stations shift by up to ±0.4 °F in March.
- **On the 786 `Y` target hours:** estimates from blank-flag neighbors run 3.8 °F (IDW, n = 452) to 5.7 °F (model, n = 691) below the flagged reading. That comparison uses the flagged reading as truth, so only the ASOS check above is independent evidence.

**Proposal (ET):** confirm `Y` readings against peers, e.g. with Terra's residual reference, instead of excluding or accepting all of them.

### E. Flagged neighbor (replay hour)

**Setup:** 61 °F injected into Davis at 2026-01-11T09:00Z. The real reading was 37.0 °F.

**Results:**
- Left unflagged, the spike moves estimates at locations that use Davis by up to +11.9 °F on the 5-station network, and +10.9 °F (IDW) / +9.7 °F (model) on the full network.
- Excluded through a Terra-style flag, the estimates equal the case where Davis's real reading is simply dropped, exactly.

**Caveats:**
- This is one synthetic hour.
- The Terra-style flag was simulated.

### Not tested

- **Estimator behaviour under neighbor faults:** missing, stale, drifting or stuck neighbors. These belong in Nathan's part of the joint case below.
- **Operational measures:** delays, failures and source revisions are not measured. Historical publication timing cannot be reconstructed from provider snapshots.

## Proposed output (per location and hour)

| Field | Content |
|---|---|
| `location_id` | `TEST-<name>` when no orchard coordinate is supplied |
| `lat`, `lon`, `elevation_ft` | Location |
| `geometry_source` | Where the elevation comes from |
| `valid_utc` | Hour |
| `temperature`, `unit` | Estimate, `unit = degF` |
| `method`, `method_version` | `idw` / `idw_lapse`; config + parameter hash |
| `used_station_ids` | Stations used |
| `excluded` | Station and reason: Terra finding / missing / out of range |
| `data_through_utc` | Per used station |
| `as_of_utc` | Clock the estimate was made against |
| `status` | `ok` / `insufficient_data` / `stale` |

No interval until calibrated.

## Proposed changes

**ChillWatch (Nathan), after coordination:**
1. A lean `estimate_location(lat, lon, elevation_ft, hours, as_of, observations, findings)` that returns the fields above. It does not exist yet. No adapter or plugin layer.
2. Neighbor eligibility comes only from Terra quality + Terra findings.
3. Lapse off on the Terra network. `insufficient_data` below 3 usable peers and outside Oct–Mar.
4. Withhold intervals. Recalibrate later on a pre-test window.
5. Keep LightGBM out of the integration unless Terra approves its extra predictors.
6. Keep ChillWatch ingestion, QC, repair, chill and map out of Terra.

**Terra (ET), proposals:**
1. A read interface for normalized observations, as-of-aware findings and geometry provenance.
2. A decision on `Y`-flag eligibility (evidence in D).
3. Optionally more stations. Four nearby Delta CIMIS stations cut error at Hastings Tract East by 0.5–1.0 °F; Verona got worse in March.
4. Confirm FNRIC conventions before any orchard chill is shown next to reported chill.

## Joint validation case (when Terra's artifacts are available)

**Setup:**
- Run Terra's saved replay 2026-01-10..14 with the frozen v3 reference and its findings.
- Estimate held-out Davis and a labelled test location, e.g. `TEST-EDU` at the University Airport ASOS coordinates.

**Check:**
- `used_station_ids` and `excluded` reasons.
- The injected spike hour is excluded through Terra's finding, not ChillWatch QC.
- Estimates made as of each hour equal the final ones.

**Nathan's part:** rerun the estimator with Terra's real findings and with neighbors removed, made stale, drifted or stuck. Report error, availability, delay and failures for each case.

**Generalization:** needs data after 2026-04-01T08:00Z, which neither side has seen.

## Open

- Nathan's approved scope is unconfirmed. `AGENTS.md`, `COORDINATION.md` and `terra.py brief` were not available.
- ChillWatch's detector benchmark is still running. It does not affect this integration, because station QC is Terra's.
