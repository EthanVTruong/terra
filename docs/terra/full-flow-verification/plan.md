# Final Terra user-flow verification

Status: bounded behavior verification complete at `37b417f`; evidence published
in [results.md](results.md). The subsequent goal remains active until the
designated consolidation owner finishes actual-upstream checks, exact-head CI
and main integration with the user's selected polished UI and all final
contributor revisions. Earlier standalone receipts do not replace those gates.

The grower saves a farm, chooses notifications, receives a meaningful monitor
note when new weather changes reliability, opens its exact evidence, and asks a
brief grounded follow-up. The operator can demonstrate the same detector and
estimator with controlled changes to held-out weather.

## Independent users

1. First-time grower: supply crops without invented varieties; enter or correct a
   farm manually; confirm its location; decline device location and notifications;
   interrupt and resume setup; verify saved profile and actual monitor readiness.
2. Returning grower: reopen without duplicate notes; ask about temperature,
   reliability, chill totals, freshness, a referenced alert and an ambiguous farm;
   handle thanks, acknowledgment, mixed requests and unrelated questions honestly.
3. Skeptical data operator: replay untouched held-out readings, a benign change,
   an isolated strong station departure, insufficient orchard support, repeated
   conditions, three distinct recovery hours and a restart.

## Acceptance evidence

| Case | Expected result |
| --- | --- |
| Original held-out weather / small change | No invented discrepancy or notification. |
| Strong isolated hot station | Actual QC evidence names the station, observed and expected values, departure and reason. Station excluded when appropriate; orchard alert only if its own eligibility changes. |
| Too few independent supporting stations | Persisted monitoring-limited event and one proactive grower note explaining lost reliability. |
| Same condition checked again | No duplicate incident, grower note or phone submission. |
| Recovery | Three distinct subsequent usable hours, then one recovery note; same-hour edits do not advance recovery. |
| Old note after later changes | Original note-time evidence remains immutable. |
| New grower onboarding | Actual supplied crops/location, explicit confirmation, persisted progress, truthful readiness and optional delivery. |
| Natural factual question | Answer from supplied evidence or one brief useful clarification; no thumbs-up replacing an in-domain question. |
| Thanks / acknowledgment / unrelated topic | Explicit silence or attached reaction as specified, with no unnecessary model call. |
| Notification | Distinguish in-app publication, simulated phone ping, provider acceptance and confirmed delivery. Never turn a placeholder receipt into a claim the actual orchard text was sent. |
| Unavailable source/model/transport | Useful retained state and honest fallback; no fabricated weather, monitoring or delivery. |

Real weather is outside the frozen reference's fitting and calibration windows.
Injected changes are labelled scenarios. A quiet control set proves those tested
cases stayed quiet; it cannot establish a universal zero false-positive rate or
confirm that every naturally flagged reading represents a real sensor failure.

Shared live service8512, auth preview8517 and user demo8520 stayed untouched by
this replay testing. Fresh state and ports were used for mutations. No credential
vault was copied. The integration owner subsequently verified one delivered
trial-template phone ping; this replay made no external send or SMS configuration
change. See the separate redacted delivery proof in the results.

The results identify tested revisions, data/reference evidence, test groups,
browser screenshots, repaired defects, delivery scope and remaining combined
release gates. The consolidation owner receives this single frozen source plus
metadata handoff before finishing the main release.
