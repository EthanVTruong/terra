# Additional cached weather review

The independent operator reviewed the previously cached March 16–22 week, beyond
the March 9–15 station demo. Execution used production candidate
`ba37a820c988ff08172c2a56f10138f4b6ed6b35`; the QC, estimator, assessment and season
modules have no diff against merged main `cb58022`.

All 168 hourly assessments were causal: only observations at or before the hour
were supplied, with a preceding 24-hour warmup. The frozen reference and source
hashes are recorded in [the compact results](evidence/station-holdout-summary.json).
No model fitting, source acquisition, network request, SMS or live-state write
occurred.

| Tested condition at Mar 17, 08Z | Actual result |
| --- | --- |
| Original Davis 59.8°F | Five supporting stations, no finding or grower note. |
| Davis 60.8°F (+1) | Five supporting stations, no finding or grower note. |
| Davis 83.8°F (+24) | Strong Davis departure; excluded, four remaining peers; no orchard note. |
| Three stations offline | Two peers, insufficient orchard estimate; exactly one coverage note. |

The natural week contained 452 provider-warning rows and 388 source-usable rows.
There were 72 usable orchard estimates and 96 insufficient estimates, all reporting
`fewer_than_3_usable_peers`. One natural suspected Davis episode exceeded its
frozen extreme threshold: 57.9°F observed versus 49.625°F expected, a departure of
8.275°F against a 7.9684°F limit. That is detector evidence, not confirmation of a
sensor fault. Seven coverage openings, five six-hour escalations and seven
recoveries were persisted by the actual monitor.

A small change is not universally guaranteed to stay quiet: a separate boundary
probe found +1°F could extend an existing episode near its sustained threshold.
The explicit clean control above was selected independently of that probe and
both outcomes are retained. No thresholds were changed to quiet this holdout.

Historical station operation epochs were not independently verified. The existing
operation gate treated all 840 rows as eligible before source-quality filtering.
This cache was previously inspected and has no confirmed sensor-fault labels;
these results are not a universal false-positive estimate.

The original full 227KB report is retained in ignored local verification state;
its digest appears in the compact results. The portable
[reproduction script](reproduce_holdout_review.py) was factored from the executed
commands and syntax-checked. Run it with the Terra development Python and
`--repo-root <checkout> --source-dir <existing normalized cache>` to generate a
fresh report. It blocks networking and refuses to overwrite nonempty output.
