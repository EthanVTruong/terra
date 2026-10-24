# Terra full-flow verification

## Combined release checkpoint

The first combined release is merged as main
`8f028a8320661c4a59344f1e3067e8af2bb79a74` through
[PR 15](https://github.com/EthanVTruong/terra/pull/15). Its tree is identical to
the tested `1a1182f` head. All four [CI jobs](https://github.com/EthanVTruong/terra/actions/runs/40120705168)
passed: Windows and macOS scoped integration, full backend including PostgreSQL,
and both web builds/tests. Ancestry includes the exact `37b417f` behavior repairs,
Nathan's authoritative `098ebe0` layer and accepted `71627c1` Station lab slice.

The runtime owner completed an actual new-Python-process cutover at port 8512
and a fresh monitoring cycle. I independently inspected its retained-state proof
and the live browser: health is OK, served bundles match clean main, the cube
interface and ChatGPT-plan connection appear, and a retained coverage warning
opens its correct saved Details. Existing messages, events, evidence, profile
and old revisions were preserved. Hours were re-estimated under the new native
estimator contract while old payload revisions were retained. The SMS journal
is exactly unchanged; the confirmed placeholder remains delivered at one attempt.

This checkpoint is operational. A subsequent bounded follow-up restores the
exact quieter composer/menu arrangement from the user's screenshots and fixes
a saved-crop question alias and native CLI import. Those changes require their
own final head/checks/rollout receipt. The separately requested humidity, wind
and solar expansion is a new additive feature, with numerical/sim ownership
handed off; it is not covered by the temperature results below.

Verified behavior candidate: `37b417fab75c49c03f5f356eabc9090c7d5b75a7`.
The source is frozen. This report and its evidence are a metadata-only handoff to
the main consolidation owner. The combined release must separately pass its
actual-upstream checks and exact-head CI, including Nathan's newer model/data
layer and the user's selected polished UI. This standalone capture is not proof
that that later combined release has already shipped.

## User journeys

Independent agents exercised first-time and returning growers, a skeptical
station operator, natural questions and held-out weather. Actual browser passes
used fresh isolated journals and ports 8531–8533; shared services and the user's
port-8520 demonstration were preserved.

| Step | Health | Observed behavior and evidence |
| --- | --- | --- |
| 1. First visit and onboarding | Passed | No seeded demo farm. Mixed supplied crops retained without invented varieties. Invalid coordinates rejected, edits clear confirmation, interrupted setup resumes, explicit location confirmation and optional notifications persist. [Onboarding](evidence/06-final-onboarding-truth.png). |
| 2. Truthful readiness | Passed | Saved replay is labelled Replay and does not claim live monitoring. First welcome confirms the saved farm rather than inventing a connected worker. Device notification and SMS consent were not silently enabled. |
| 3. Quiet readings | Passed for tested controls | A small Davis change retained five peers without a finding or extra proactive note. Independently raising all five stations by 5°F or 15°F also stayed quiet. [Holdout review](held-out-review.md). |
| 4. Manual station spike | Passed | Actual keyboard slider movement to 125°F flagged Davis against about 46.5°F expected. Davis was excluded; four peers still supported the orchard, so no unnecessary orchard alert was published. [Actual slider](evidence/07-manual-slider-flags-station.png). |
| 5. Meaningful grower alert | Passed | Reducing support to two stations made the orchard estimate unavailable and published one actual proactive coverage note in the grower chat, without a manual refresh. Details named the two supporting stations. [Final alert and evidence](evidence/14-final-alert-and-evidence.png), [stage data](evidence/final-browser-stages.json). |
| 6. Repeat and recovery | Passed | Repeating the same condition did not duplicate the incident or note. Three distinct restored hours produced a recovery note. The older loss note still showed its original two-station evidence after recovery. [Saved context](evidence/10-recovery-retains-old-alert-context.png). |
| 7. Questions and quiet acknowledgments | Passed | Temperature, freshness, reliability, station findings, chill and referenced-alert questions route to grounded answers or a brief clarification. Thanks creates no assistant reply; acknowledgment attaches a reaction. An old-note freshness question retains its old observation time after recovery. |
| 8. Same-hour publication and follow-up | Passed after repair | New coverage appears after the older usable-support welcome even when both share the same historical reading clock. “What does that mean?” explains the newest coverage note. [DOM order](evidence/publication-dom-order.json), [publication proof](evidence/final-publication-proof.json), [final frame](evidence/11-final-same-hour-ordering.png). |

Screenshots 08–10 were captured before the final publication-order repair and
remain chronological evidence. Screenshots 11, 12 and 14 show the repaired
candidate. Source/observation clocks and saved assessment IDs were preserved;
new items use actual publication time for presentation and follow-up selection.

## What Terra flags

The station detector flags a reading that departs sufficiently from independent
nearby-station expectations under its frozen thresholds. A finding is evidence
of a suspicious reading, not a confirmed sensor fault. The orchard monitor
separately publishes a grower note when support becomes insufficient, persists
long enough to escalate, or recovers. A flagged station with enough remaining
support need not generate a grower alert.

The additional cached March 16–22 review assessed 840 rows over 168 causal hours,
plus warmup, with no future readings supplied. It produced 72 usable and 96
insufficient orchard estimates. One natural suspected Davis episode exceeded
its extreme limit. There are no confirmed fault labels, so these controls do
not establish a universal zero false-positive rate. Source/reference versions,
hashes, warnings and the boundary counterexample are in the
[held-out review](held-out-review.md) and its compact evidence.

## Delivery and account proof

The isolated historical replay made no real SMS, weather-provider, LLM, training
or shared live-state calls. Its phone card is simulated. Browser return routing
to the captured farm and exact note has controlled regression coverage; this
pass did not request actual OS notification permission or test closed-app push.

Separately, the integration owner's existing synthetic detector demo sent one
predefined trial-template SMS. Its saved receipt reports `delivered`, one
attempt, and confirmed handset receipt. I verified that receipt and current
transport readiness without sending another message or changing configuration.
[Redacted delivery proof](evidence/placeholder-delivery-redacted.json). This
proves the phone ping; it does not mean the historical orchard coverage text
itself was delivered by SMS. Farm SMS opt-ins remained off under that owner's
control. The actual grower information and evidence are in the app.

The connected ChatGPT-plan selection was inspected read-only. The integration
owner separately verified actual plan-written coverage notes. Replay uses the
native conversational writer's factual fallback; local factual/quiet intents
retain zero-call behavior and canonical station-health questions retain their
writer route.

## Repairs and verification

The repaired flow reuses native conversation routes and the real store,
assessment, estimator and monitor. Fixes cover seeded-farm isolation, source
readiness, crop preservation, ordinary question routing, weather versus chill
history, immutable old-note context, notification return routing, replay feed
updates and actual publication ordering. Scientific modules and reference
configuration have no diff against the reviewed `cb58022` base. No acquisition,
training, threshold tuning or numerical policy edits were made here.

All ten scoped check groups passed at `853336a` against `cb58022`. After the
final publication repair at `37b417f`, the affected native conversation and
farm suites plus full-flow tests passed with zero skips; SMS, runtime and
station-demo suites also passed with zero skips. All 28 UI tests and the final
production build passed. Both web packages installed with frozen lockfiles,
built successfully, and passed all eight packaging checks. Existing nonblocking
dependency deprecation and demo bundle-size advisories remain.

The browser used three independent fresh grower profiles and actual UI inputs.
The viewport override did not take effect in this browser, so this pass does
not claim a new 320px verification. The selected visual owner separately
verified 320px/390px containment in its accepted slice; the combined owner must
review the final selected UI and preserve that evidence. Global CI and the
newer upstream model-layer compatibility belong to the final combined head,
not to these earlier receipts.

## Handoff

Use the single `codex/terra-demo-simulation` head containing this report as the
evidence handoff. Do not merge individual worker branches, replace the user's
selected polished interface with this standalone pre-polish capture, restart
shared runtimes, or replay another external send. The consolidation owner owns
the final main integration and exact-head CI; the existing runtime owner owns
the one retained-data rollout.
