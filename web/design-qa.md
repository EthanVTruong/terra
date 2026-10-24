# UI fidelity QA — 2026-10-24

final result: passed

Scope: current assessment UI, approved wordmark, reference styling and connected
location /evidence /saved-assessment interactions. Monitoring inbox and SMS
integration are separate root-owned feature work.

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
