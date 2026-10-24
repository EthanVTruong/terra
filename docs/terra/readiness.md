# Integrated pipeline readiness

This record separates verified functionality from remaining integration and
external account dependencies. Current integration owner: `terra-full-pipeline`.
The [shared contract](pipeline-contract.md) defines accepted product policies.

| Slice | Current evidence | Remaining acceptance |
| --- | --- | --- |
| Live source → QC → location JSON | PR #4 merged at `37948ed`; all four exact-head CI jobs passed, including 298 backend cases and PostgreSQL with zero skips | Retest through the combined runtime |
| Grower UI | Accepted tight incoming layout, three-value comparisons and evidence sidebar; isolated UI owner verifies live/failure/mobile states | Integrate persistent feed and actual transport states |
| Farm monitor and event store | Separate owner implements durable observations, matching-method chill, event/message identity and consent | Whole-path persistence, recovery, clock/freshness and worker tests |
| Message → SMS | Transport owner implements fixed-recipient gates, attempt ledger, explicit demo, safe retry and receipt reconciliation | Whole-path acceptance and real sender/account readiness |
| Twilio account | Existing trial account is signed in and API credentials are privately configured; read-only probe shows no sender yet | Approved sender/custom-message capability and actual phone delivery |
| GitHub contributors | Published histories and contributor API contain only Ethan and Nathan; supported background rebuild requested | Sidebar refresh is controlled by GitHub |

Required combined acceptance:

- A controlled eligible fault traverses actual QC, persisted message selection,
  transport reservation and receipt handling. Real delivery evidence stays
  separate from simulated weather and mock transport.
- The worker continues with the UI closed, resumes after restart and avoids
  repeated openings or duplicate external sends.
- A healthy orchard with an excluded suspect station keeps station evidence in
  Details rather than sending a conflicting raw-station alert.
- Missing/stale/source-failure states remain distinct from numerical anomalies;
  fresh data-health and daily chill messages use the correct evidence age.
- Two completed low-pace days, recovery, incomplete history, opt-out and expiry
  preserve the selected policies.
- Definitive retryable rejection persists backoff; uncertain acceptance is
  reconciled without blind resend. Accepted messages acquire receipts with the
  UI closed. Acceptance is never labelled delivered.
- Desktop/mobile, keyboard/focus, cancellation, unavailable source and exact
  evidence download work against the assembled service.
- Full PostgreSQL/native tests, minimal-runtime platform checks, production web
  build and packaging tests pass for the final reviewed source.

Credentials, full phone numbers and private account data stay in ignored local
configuration. Missing credentials, sender verification, account approval or
spending authorization remain explicit. A simulated receipt does not establish
real SMS readiness.
