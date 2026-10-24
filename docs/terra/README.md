# Frozen QC + location assessments

This milestone adds the actual selected Terra statistical detector to the shared
repository and calls Nathan's existing `chillwatch.estimate.estimate_location`.
It produces JSON data and a compact web preview. It does not replace ChillWatch's
ingestion, storage, LightGBM model, detection, repair or configuration.

ET selected two result types: estimated conditions plus relevant source-station
flags at uninstrumented locations, and independent measured comparisons where
a sensor exists. Only the frozen five stations have calibrated e1 decisions.
Other measured sites receive numerical comparisons without invented thresholds.

The target product and subsequent service/delivery requirements are in
[the product spec](../../spec.md) and
[the monitoring contract](always-on-monitoring.md).
[checkpoint.md](checkpoint.md) records hashes, provenance and measured checks.

## Run the pure JSON path

The lightweight path needs NumPy, pandas and PyYAML. It does not load or train
LightGBM, scikit-learn or the separate ChillWatch model pipeline.

```powershell
python -m pip install -r requirements-terra-dev.txt
$env:PYTHONPATH = "backend"
python -m terra_monitoring docs/terra/examples/measured.request.json --output assessment.json
python -m pytest --noconftest -o pythonpath=backend tests/test_terra_json.py tests/test_terra_qc_reference.py tests/test_estimate.py -q
python terra.py check --base origin/main --task terra-qc-json
```

On macOS/Linux use `export PYTHONPATH=backend` for the module command.
The examples are retained real historical weather with explicitly controlled
fault injection. They are not current weather or documented natural faults.
The request includes original dataset identity, its own observation-snapshot
identity, exact query clock and scenario provenance.

`--noconftest` isolates this pure API's tests from the existing global test
fixture that imports ChillWatch's separate ML context. It does not skip
individual estimator/adapter tests. The original model/detection/repair suite
retains its original dependencies and is not claimed by these checks.

## JSON meanings

- `location_estimate`: Nathan's temperature/geometry interpolation, supporting
  stations and exclusions. Respect `ok`, `stale` and `insufficient_data`.
- `target_comparison`: measured value and its comparison, if a target exists.
  For a calibrated station this uses Terra's frozen pair-adjusted expectation;
  it is distinct from the location interpolation value. `typical_low_f` and
  `typical_high_f` add the station's calibrated historical residual bounds to
  that same-hour expectation. They describe a typical historical range, not a
  future forecast or a guaranteed confidence interval. They remain null for
  an unavailable comparison or an uncalibrated sensor. A stale comparison may
  retain these historical values with an explicit stale status.
- `station_findings`: original causal e1 findings relevant to this query/hour.
- `estimator_qc_exclusions`: separately labelled QC exclusions used for the
  estimator. A measured calibrated target is held out of the peer-QC pass too,
  preventing its own reading from influencing its interpolation indirectly.
- `data_health`: source/availability exclusions; source warnings are not
  confirmed sensor faults.
- `provenance`: frozen reference, estimator method/version, caller's snapshot,
  publication visibility and exclusion basis.

The adapter requires aware timestamps, exact hour-ending observation times,
finite coordinates, trusted timestamp metadata and the selected reference.
Findings become visible at detection time and at any later required input
publication time, not at an earlier retrospective onset. Missing publication
history remains explicitly snapshot-only. Future readings and revisions are
not used before their visibility clock.

The adapter separately applies caller-supplied station connect/disconnect dates
before QC and interpolation, using Nathan's fixed-PST hour-ending calendar day.
Both dates are inclusive; midnight belongs to the preceding operating day.
Missing dates retain permissive eligibility. Out-of-operation observations are
unavailable, not sensor faults, with `station_not_operating` evidence. Filtering
the interpolation geometry also keeps those stations out of the lapse fit.
Provenance names this adapter gate and its query-day interpretation separately
from Nathan's estimator method version. The standalone preexisting estimator is
unchanged and does not itself apply operating dates to every lapse contributor;
consumers needing this gate should use the assessment adapter.

A request can name any coordinate. A usable estimate still requires at least
three eligible peers in Nathan's configured region/radius and validated months.
Shared storage is year-round, while the current pure estimator remains gated
to October–March. Unsupported queries return unavailable; they do not imply
normal conditions. Changing QC source-warning eligibility, calibration or
geographic coverage is a separate scientific decision.

## Web preview

```powershell
pnpm --dir web install --frozen-lockfile
pnpm --dir web dev --host 127.0.0.1 --port 8510 --strictPort
```

The UI reads committed small JSON examples from `web/public/assessments/`,
renders their facts and offers exact JSON copy/download. Open any newly
generated assessment file with **Open JSON**. Example selection and file opening
do not query a provider or calculate classifications in JavaScript.
This first slice is a saved-data integration preview; a live query API and
always-on worker follow the next review with ET.

## Scientific and product limits

The combined path has controlled/software verification, not adjudicated natural
fault accuracy. Keep raw measurements, statistical findings, location estimates
and reported FNRIC chill distinct. Nathan's repaired/estimated chill windows
are not interchangeable with FNRIC's reported September-start totals.

An extreme station departure retains the detector's `strong` label; this
adapter does not rename it a confirmed spike, sensor failure or crop emergency.
An uninstrumented coordinate has no measured local discrepancy. An unknown
sensor's numerical difference is `comparison_only` until a policy is validated.

There is no SMS, hosted background monitor, subscription service or automatic
delivery in this checkpoint. The UI deliberately states the data mode and time.

