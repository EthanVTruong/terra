# Integrated pipeline readiness

This record separates verified functionality from remaining integration and
external account dependencies. Current integration owner: `terra-full-pipeline`.
The [shared contract](pipeline-contract.md) defines accepted product policies.

| Slice | Current evidence | Remaining acceptance |
| --- | --- | --- |
| Live source → QC → location JSON | PR #4 merged at `37948ed`; all four CI jobs passed, including PostgreSQL with zero skips. Combined runtime reads real CIMIS; the demo evidence showed 60.6°F from six usable stations | Final combined CI |
| Grower UI | Accepted incoming interface, persisted orchard feed, guarded setup/consent, exact evidence and delivery labels integrated from `4ff79ce` plus `dc9a113` focus repair; desktop/390px/320px unified browser checks, production build, four packaging and three semantic UI tests pass | Final combined CI |
| Farm monitor and event store | Farm `73bee6b` and runtime `ea78c7d` integrated. 64 farm/API and 34 lifecycle cases pass. Actual installed service restarted with two identical note IDs, fresh worker and zero SMS attempts | Final combined CI |
| Message → SMS | 203 transport/HTTP cases and nine cross-layer acceptance cases pass. Mocked provider receipts reconcile with the UI closed, survive restart, retain backoff and avoid blind resend | Real sender/account readiness and phone receipt |
| Twilio account | Existing active trial credentials are privately configured. Read-only account probes succeeded; owned senders: zero. Requested tester still requires verification; trial template restriction prevents arbitrary Terra text | Tester verification, approved custom-message sender/capability, charge authorization if needed, token rotation, then a real delivery test |
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

## Evidence boundaries

The merged local suites contain **499 Python cases**: 189 baseline/live/HTTP,
203 SMS/HTTP, 64 farm/API, 34 runtime and nine cross-layer acceptance cases.
The cross-layer tests use actual saved/raw weather adapters, frozen QC, Nathan's
estimator, persisted farm state and transport journals with controlled weather
and `httpx.MockTransport`. They establish software behavior, not natural sensor
fault accuracy or real phone delivery. No actual SMS was accepted or delivered
during this integration.

The Windows launcher was exercised with an isolated state directory and port:
health and empty-orchard responses succeeded; stop retained state and terminated
the matching process. The actual combined runtime then started on loopback8512
with one five-minute worker, explicit absolute private configuration and shared
state. The existing demo profile remains opted out of SMS.
The subsequent real restart changed the worker PID while retaining both note IDs
exactly, with no duplicate notes or SMS attempts. The live new-hour missing-data
transition produced a monitoring-limited note; it did not create a temperature
fault conclusion. Final browser checks also verified exact API/record JSON,
mobile focus containment/opener return and the disabled phone state.

The intended recipient and existing credentials remain only in ignored private
configuration. One browser accessibility observation included the existing Auth
Token in a private tool trace. It was not placed in tracked files or Git history;
rotation is required before activation, and both SMS enable flags remain false.
The secret-exclusion scan found no actual configured credential/contact values
in tracked files or newly added history. Original staged bootstrap entries remain
unchanged.

No matching five-season orchard archive is automatically populated. Historical
pace stays unavailable until a matching, sufficiently complete archive is
explicitly imported. The 400-hour cultivar value remains a configured reference,
not a validated biological threshold. Optional LLM writing is not required for
the deterministic inbox and was not enabled or billed.

Use [the local-runtime runbook](local-runtime.md) for one-process operation and
[the Twilio runbook](twilio-sms.md) for account gates and receipt semantics.
