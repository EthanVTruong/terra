# Clean statistical checkpoint and first JSON integration

The subsequent [main consolidation verification](main-consolidation.md) records
full-backend CI, restored typical ranges and integration/portability repairs.
The checks below describe the original first-slice checkpoint.

Prepared against shared `main`:
`229106c251cfa41993a747cde2a40f29e53ed60f`.
The original prototype cleanup chat released ownership after its final checks.
This additive integration preserves Nathan's existing implementation and calls
its pure `estimate_location` interface.

## Frozen identities

| Item | Identity / SHA-256 |
| --- | --- |
| Selected statistical reference | `terra-qc-v4-e1e59ac0ea395d80` |
| Reference bytes, 177,729 bytes | `809c78b69d80d93d73f5b4b75ca6e74b0759ddcebf1e17fa2450386b44fcd90f` |
| Original normalized source snapshot | `4d152b359ac00bc343137315eccbe77737d3a3324a363b5b7cae659e7f1cd741` |
| Final original QC module | `6b94e17eac85fd0e8367c65e30988528af96b3cce0904c515fdb867268690789` |
| Original complete replay export, preserved locally | `23a9a02e60a4b1cc5ae24b043c04978f24b247a21ddc52cd0562b4080b3d9aee` |

[The import manifest](source-import.json) records original and shared hashes
for all six copied statistical/geometry files. The only Python source change
in that copy is the import namespace `terra_app` to `terra_qc`. Every file
was rechecked against the final released cleanup source. The selected reference
is copied byte-for-byte; its loader rejects changed bytes or identity.

The repository includes the frozen reference and three small, complete replay
requests plus their assessment JSON. It does not include credentials, the
original SQLite cache, full training dataset or retired executable ML archive.
Each request records its source dataset, explicit controlled injection and
observation-snapshot identity. No provider request, fitting or recalibration
was performed for this integration.

## Cleanup evidence versus new integration checks

The original [cleanup report](history/ml-cleanup.md) and
[structured results](history/ml-cleanup-results.json) record 200 passing Python
tests after removal of project-local ML packages, 20-case/12,000-row numerical
parity and a byte-identical 25-case/2,400-prefix export. These are original
prototype checks, not a claim that the entire separate ChillWatch suite ran.

The new shared checkout passes:

- 53 targeted Python cases: 28 composition tests, seven frozen-reference/parity
  cases and Nathan's 18 existing pure estimator tests.
- 31 portable coordination-harness tests and the required
  `terra.py check --base origin/main --task terra-qc-json`.
- Production frontend build and four existing packaging tests.
- A cold process that forbids LightGBM, scikit-learn, SciPy, joblib and
  threadpoolctl imports while running the complete assessment.
- Actual replay parity against golden values produced by the final cleaned
  original detector. The measured example is 61.0°F observed, 37.3°F expected,
  +23.7°F departure. Initial detection is 08:00 UTC; the displayed peak evidence
  is 09:00 UTC, explicitly labelled separately.
- Causal visibility, future/revision exclusion, fifth-reading persistence,
  unavailable/stale data, unknown-sensor comparison-only output and target
  scrambling that cannot change the held-out location expectation.

The existing global ChillWatch pytest fixture imports its ML context even for
pure tests. These scoped checks use `--noconftest`; individual estimator and
adapter tests still execute. The original ChillWatch model/detection/repair
suite remains outside this minimal environment and is not claimed as run.

## Browser verification

The browser shows actual generated JSON, rather than simulated client-side
classification. Verified at normal desktop width and 390/320px:

- Location without a sensor: 37.0°F interpolated from four usable stations;
  Davis's controlled anomaly is retained as a source flag and excluded.
- Measured Davis: the calibrated three-value comparison leads; Nathan's
  interpolation remains a distinct secondary value. The target is not repeated
  as a second identical message, while its exact JSON finding is preserved.
- Two missing peers: expected/departure are null and unavailable, with no
  numerical anomaly conclusion.
- A locally opened stale packet retains historical values with a visible stale
  warning and no current classification.
- Invalid local JSON is rejected while preserving the current packet and
  selection. Mobile JSON traps focus, closes with Escape and returns focus.
- Downloaded JSON exactly equals the generated packet. Copy reports success;
  external clipboard round-trip is unverified because the browser clipboard
  inspection API returned an empty string.
- Captured warning/error log is empty; viewport override reset afterward.

Local captures are in `.terra-local/review/` and are not shipped as scientific
evidence. The preview is a saved-data integration: it selects examples or opens
an assessment JSON file. It does not yet submit arbitrary coordinates to a
live backend, poll providers or send messages.

## Boundaries for the next review

The frozen statistical model covers five stations. Query coordinates can be
arbitrary, but estimates require supported peers and Nathan's validated months.
Unknown measured sites receive numerical differences without an invented fault
policy. Existing source-warning exclusions remain unchanged. The combined path
has software/controlled validation, not independently adjudicated natural faults.

Original e1 station findings and held-out-target estimator exclusions remain
separate. The target-held-out pass is fixed and does not recursively remove
neighbors until agreement. It preserves the selected calibration.

FNRIC reported September-start chill, repaired station temperatures and estimated
location chill are distinct products. No chill metric or scientific window was
silently merged. SMS, background operation and live location querying follow the
next product review in [spec.md](../../spec.md).

