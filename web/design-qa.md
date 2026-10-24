# UI fidelity QA — 2026-10-24

final result: passed

The independent visual slice and the combined monitoring/phone UI pass against
the actual unified API on8512. This covers the UI and the disabled-phone state;
real SMS delivery remains separately gated by account setup.

Scope: source fidelity, approved wordmark, assessment/query paths, actual orchard
setup and incoming persisted notes, exact evidence, truthful delivery labels,
keyboard/mobile behavior and the owner Phone Alerts controls.

## Compared artifacts

- Source: exact Grok Bot article, plus Fliptexts public message/landing states.
- Combined comparison: `../.terra-local/ui-fidelity/comparison-final.png`.
- Rendered UI: `http://127.0.0.1:8504/` in the in-app browser.
- Same UI scale: Grok's 372 ×384 article crop enlarged 2×; Terra native 744 ×768.
  Source scaling softens its screenshot, so DOM values ground precise measurements.
  Data, brand and workspace contents intentionally differ by product.

## Resolved findings

| Priority | Finding | Fix and post-fix evidence |
| --- | --- | --- |
| P2 | Initial frame retained wider prototype dimensions. | Matches native 744 ×768 and 320px evidence; combined comparison. |
| P2 | Default JSON displaced the accepted temperature context. | Pinned temperature with exact JSON tab; desktop capture and keyboard tab navigation. |
| P2 | Selecting a station comparison discarded matching hourly evidence. | Retains the matching station/time; note opens its own evidence, attachment selects JSON. |
| P2 | Note and chart used different time zones. | Shared Pacific formatter; source timestamps remain byte-for-byte JSON. |
| P2 | Mobile evidence hid injected mode/date with the covered conversation. | Mode and date visible in full-screen evidence; minimum-width evidence capture. |
| P2 | Unavailable comparison discarded its available observed value. | 61.0°F retained, Expected/Departure unavailable, quiet unavailable status visible. |
| P2 | Disabling the submit button could drop focus on query completion. | Captures the submission origin and returns focus without stealing later user focus. Actual current query verified. |
| P3 | One large chart bundle delayed the primary UI. | Chart split from main entry (about 259KB /78KB gzip initial JS); reserved chart loading area. |

## Verification

- Fonts/typography: exact inspected source font, 14/20 prose; approved native wordmark.
- Spacing/layout: source-backed shell, header, padding, corners and maximum bubble width.
- Colors: canvas/bubble/ink match source. Secondary copy has readable neutral contrast.
- Images/icons: final approved 1080px source hashes match manifest; consistent Phosphor icons.
- Copy/content: three readings, short labels, collapsed explanation/source details;
  meaningful missing/stale/source warnings preserved.
- Desktop: 1280 ×900; source popover, location form, panel close/reopen, tab keys,
  explicit note/JSON navigation, transient copy feedback and clean console.
- Mobile: 390 ×844 and 320 ×568; no horizontal overflow, full-screen evidence,
  background hidden/inert, Shift-Tab wrap, Escape and opener restoration.
- Real source: actual current CIMIS queries returned approximately 60.1°F custom
  estimate and Davis 60.4°F observed /60.0°F expected. No natural fault accuracy claim.
- Exact download: downloaded packet equals rendered JSON (trimmed newline only).
- Controlled failure service: delayed 503 and cancellation retain prior 37.0°F
  historical result; retry remains available. Separate from real provider evidence.
- Disconnected source: current query disabled with retry explanation; saved evidence works.
- Production build and all four packaging tests pass. Scoped coordination and Python
  checks pass using the shared configured environment; numerical algorithms unchanged.

## Limits and expected differences

Native Grok window behavior is not observable from its static demo; Terra motion
timings are proposals, not measured parity claims. No authenticated Flip dashboard
or physical phone/touch behavior was tested. Reduced-motion rules were inspected,
not toggled through the OS. External clipboard paste fidelity is unverified.
The existing local-file loader was preserved; its native picker was not repeated
during the team's shared-browser mutex. The monitor/SMS integration needs its own
API/browser verification before a complete pipeline readiness claim.

No unresolved P0/P1/P2 issue remains in this presentation scope.

## Final unified UI verification

Actual API: http://127.0.0.1:8512; UI: http://127.0.0.1:8504.
Exactly one labelled Davis demo was created through the protected setup form:
orchard-e8cea869-3bf6-4274-a20b-a4c54c1f9225. Its SMS opt-in stayed false.

| Check | Actual result /evidence |
| --- | --- |
| Setup +guard | POST through the original loopback Host/Origin succeeded; focus returned to the query control. No sender/config/token was placed in exported evidence. |
| Current feed | Persisted welcome note and later real new-hour data-health note appeared. Both remain local inbox /not requested for SMS. |
| Exact evidence | Welcome opens its frozen60.6°F estimate, six supporting stations and explicit1AM PDT evidence time. Rendered details JSON equals the exact API object without omitted/changed fields; record-equality.txt. |
| Missing data/history | New-hour unavailable readings remain unavailable. Latest health warning is visible, prior evidence retains its own date. Chill stays unavailable outside the season; historical pace is not shown as zero. |
| Ordinary query | Check location still uses pure /api/assess. Actual current9UTC publication gap rendered Unavailable with a current-source label. No SMS action was performed. |
| Phone controls | Actual unconfigured/disabled account state displayed; demo and opt-in stayed disabled/unchecked. Escape restored the bell trigger. No send/enable/opt-in was attempted. |
| Focus repair | Details remains focusable during its fetch, with a duplicate-request guard. Closed nested disclosures are excluded from the mobile trap. Shift-Tab wraps to the visible Chill and monitoring summary; Escape returns to the actual Details opener. |
| Responsive |390×844 and320×568 feed/evidence remain usable without horizontal overflow. Background is hidden/inert under the mobile evidence dialog. |
| Fresh console | A new tab loaded the unified app with zero captured warnings/errors, avoiding historical Vite edit-time messages. |
| Build/checks | Production build,4 packaging cases,3 semantic delivery-state cases and scoped coordination/configured checks passed. Root separately verifies the merged complete backend/runtime and CI. |

Combined source/implementation comparison:
../.terra-local/ui-fidelity/comparison-unified.png.
Actual desktop/mobile/evidence/phone captures use the actual-* files in that
directory. The three fidelity/focus corrections were re-captured after repair.

Remaining P3/limits: the immutable welcome repeats Demo orchard because that was
the explicitly labelled example name; text is retained intact. Physical touch,
OS reduced-motion switching and external clipboard paste are unverified. No
second orchard was created merely to stress multi-location races; those guards
were reviewed. Source/native window timings remain unknown where previously
noted. Actual phone account activation/delivery is a separate readiness gate.
No unresolved P0/P1/P2 issue remains in this UI scope.

## Quiet metadata refinement — 2026-10-24

User direction: keep the accepted tight proportions and three readings; remove
repeated context labels, use the approved square character, and group field notes
by day with small times below each bubble.

The header and favicon now use the existing approved cube asset. Normal local
publication captions and repeated orchard/monitoring headings are omitted from
the conversation; location, crop, demo context and transport remain in Details or
Sources. Exact stored message text and JSON are unchanged. Actual delivery states,
worker failures, unavailable weather and non-current data indicators remain visible.

The exact Grok article shows a centered 12px/16px clock above its demonstration
transcript. Terra adapts that restraint into one Pacific calendar-day divider and
clock-only note footers. This multi-day behavior is Terra's implementation, not a
claim about Grok's unobserved native app. The evidence heading is one row: Estimate
(or the station name) and its reading time. Different-day readings keep a compact
date, and historical/injected evidence retains its mode and date on mobile. Full
timestamps are available through time labels/tooltips and the exact record.

Verified at 1280×900, 390×844 and 320×568: no horizontal overflow, one Today divider
for three same-day notes, clocks below bubbles, mobile Escape/opener restoration,
focus wrap, compact estimate heading, historical mode/date and untouched raw JSON.
Evidence: `../.terra-local/ui-quiet/desktop.png`, `mobile.png`,
`mobile-evidence.png`. Calendar tests cover Pacific midnight, both daylight-saving
transitions, different-day/year evidence and invalid timestamps.

Final verification: production build, seven UI tests, four packaging tests, and
all configured scoped checks pass against origin/main at 78e835d. The live API
remains on 8512; this refinement changes no backend, data or notification behavior.

## Character placement refinement — 2026-10-24

User found the cube crowded into the top-left corner. The header now has a shared
52px height, a 28px character, 24px left inset (20px below 370px), and approximately
12px clearance above/below the character. The evidence header shares its baseline;
the Phone Alerts popover retains a 6px gap below the taller header. This deliberate
spacing refinement supersedes the earlier 44px source-height target.

Production build and diff whitespace check pass. Actual browser checks at 1280x900,
390x844 and 320x568 confirm aligned header borders, approximately 12px logo
clearance, a 6px popover gap and no horizontal overflow. Screenshots are retained
in `.terra-local/ui-logo-spacing/`. Corrected the earlier task artifact field to
use declared artifact IDs only; the QA path remains an evidence pointer.

## Lean copy and single evidence entry — 2026-10-24

User requests removing repeated As of dates from bubbles, reducing Phone Alerts
subtext, and avoiding Details on every page. Display formatting now strips only
the generated trailing Pacific timestamp (including the template's truncated
Details suffix). Meaningful dates within prose, stored messages, transport text
and exact JSON remain intact.

Notes open their readings directly; the duplicate per-note Details button is
removed. Repeated Details, source/exclusion and message/delivery disclosures are
removed from the conversation and sidebar. The evidence pane's JSON tab remains
the central full record, including provenance, exclusions and delivery receipts.
Unavailable data, stale findings, replay identity and actual receipt states retain
visible treatment. Historical chill unavailability keeps its explanatory tooltip.

Phone Alerts shows connection status, the orchard preference and Refresh when
disconnected. Configured connections reveal the simulated test and delivery
controls. The test stays explicitly simulated; cost copy appears when sending is
available, while queued/sent results still say delivery is unconfirmed. Existing
configuration gates, transport actions and duplicate-send guards are unchanged.

Production build, nine UI tests and four packaging tests pass. Actual unified API
and browser checks cover note tap-to-read, mobile Escape/focus return, original
message timestamps in JSON, historical mode/date and complete assessment provenance
in JSON after removing the redundant disclosures. The actual disconnected Phone
Alerts view was checked; configured branches were reviewed without enabling SMS
or sending a message. Screenshots: `.terra-local/ui-lean-copy/`.

## Fixed verified phone interaction — 2026-10-24

User selected the existing verified phone for the demo. Phone setup now uses an
explicit action and readiness states. Its binary orchard subscription switch
appears when useful after configuration. Phone readiness, subscription and message
receipt are separate. The visible ready label is Ready rather than an implication
of continuous handset presence. Trial-template mode states that the SMS is a
notification and the actual field note remains in Terra.

Read-only connection checks resolve to a bounded pending, setup-required, ready,
paused or unavailable state. The real server's setup-required response was
verified; ready and a controlled 1.4-second pending response were checked against
an isolated HTTP fixture running the actual frontend. Preference on/off changes
were fixture-only. Busy controls preserve focus, and receipt checks remain
available for an existing event after sending pauses. No real SMS action was
performed. Build, 14 UI tests, four packaging tests and scope validation pass.
Design rationale and logistics: `phone-connection-design.md`. Review captures:
`.terra-local/phone-connection/`; the selected interactive state preview is
thread-scoped `terra-verified-phone.html`.

Integration note: origin/main advanced during this pass with backend cleanup.
Scope/record validation passes against the declared task base 78e835d; the UI
branch preserves that base. The integration owner must reconcile current main
and run its configured integration checks before merging this branch.


## Local-first reconciliation

User authorized integrating the approved quiet UI with main after PR10. The combined build retains ChatGPT controls, replay-only source warnings and trial-placeholder delivery labels, alongside the cube logo, Pacific day dividers, clock-only footers and explicit phone readiness. All22 UI checks and4 packaging cases run together. Isolated18520 browser verified the two-day offline feed, old support-loss evidence, connection controls, 390/320 containment and Escape focus return; no provider, SMS or actual subscription change. Cloud migration is deferred in favor of the local open-source quickstart.
