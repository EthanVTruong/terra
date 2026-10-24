# Terra integration coordination

```json
{
  "kind": "task",
  "id": "terra-twilio-sms",
  "owner": "et",
  "status": "done",
  "intent": "User requests comprehensive Twilio setup and actual phone delivery when Terra flags a reading, with simulated flags for the demo. Add an explicitly enabled fixed-recipient transport, controlled detector demo, durable duplicate suppression and delivery evidence. Configure the account using computer control where permitted; keep authentication and regulated/paid submissions with the user. Preserve frozen QC, Nathan algorithms and current on-demand query semantics.",
  "branch": "codex/terra-twilio-sms",
  "base": "22d4594a768af930b655118959942ae06567b768",
  "domains": [
    "terra-coordination",
    "terra-qc-json"
  ],
  "paths": [
    "COORDINATION.md",
    "backend/terra_monitoring/**",
    "tests/test_terra_sms.py",
    "tests/test_terra_sms_server.py",
    "docs/terra/**",
    "web/**",
    ".env.example",
    ".github/workflows/terra.yml",
    "README.md"
  ],
  "depends_on": [],
  "contracts": [],
  "artifacts": [],
  "handoff": {
    "revision": "2ac2beb96eb2f324f3b2fc78fa8248072d664ce8",
    "summary": "SMS software handoff ready: fixed-recipient transport, explicit real-detector simulated demo, immutable farm-message delivery, durable attempt/retry/receipt journal and shared guarded server assembly. Ordinary queries remain pure. Integration owner owns combined farm worker/UI merge; no second worker or message queue added.",
    "evidence": [
      "145 transport/envelope tests and58 HTTP/composition tests pass. Full configured scoped check passes on the transport source against actual upstream22d4594; final assembly-hook cases separately pass. Production build and4packaging cases pass; browser disabled-send/setup/focus-return verified; preview8513 healthok and removed raw-send route404.",
      "Real frozen detector synthetic spike yields station6 extreme_departure,120F observed/40.475F expected. HTTP→MockTransport→receipt→pause→restart composition passes. Definite429-only bounded retry, unknown no-resend, category-aware expiry, batch receipt fairness and source-event correlation covered.",
      "Actual read-only Twilio account and owned-number APIs both HTTP200: accountactiveTrial, ownedSMSsenders0. Console shows100freeSMS, predefined templates and a trialsharedsender. Requested recipient absent from verified tester list. Recipient entered in Add a verified tester; Verify not pressed; zero OTPs, real SMS or paid actions.",
      "Existing account credentials plus opted-in recipient saved only in ignored private runtime configuration; shared CIMIS environment preserved. Redacted local readiness record available to integration owner. Original Terra staged checkout, Nathan implementation and frozen reference preserved."
    ],
    "uncertainty": "Actual custom Terra phone delivery remains unverified: requested tester needs phone verification; trial custom-body restriction and approved sender readiness remain. No spending approval, purchase or upgrade. One automatic browser accessibility snapshot included the existing token in this private tool trace; no source/public Git/handoff contains it, and owner credential rotation was not performed silently.",
    "next": "Root integration owner consumes this branch and shared private SMS env path, mounts optional farm router once, drains immutable outbox on startup/ticks and reconciles pending receipts. User completes Verify on prepared Twilio tester screen and reviews sender/custom-messaging upgrade requirements and budget before any real test. Keep SMS disabled until ready."
  }
}
```

This record covers ET's additive statistical QC/JSON work. Existing `backend/chillwatch`,
configuration, ingestion, repair and model code remain Nathan-owned; they are not
replaced by this checkpoint. Source facts live in the modules. This is a shared
Git record, not a live lock. Original prototype cleanup is coordinated separately.

```json
{
  "kind": "policy",
  "version": 1,
  "owners": {
    "et": "Ethan Truong; statistical QC and JSON/web integration",
    "nathan": "Nathan Ward; existing ChillWatch estimation and data/model layer"
  },
  "max_source_bytes": 10485760
}
```

```json
{
  "kind": "domain",
  "id": "terra-coordination",
  "owner": "et",
  "purpose": "Scoped integration records and portable checks",
  "paths": [
    "AGENTS.md",
    "CLAUDE.md",
    "COORDINATION.md",
    "terra.py",
    "tests/test_terra_harness.py",
    ".gitattributes"
  ],
  "depends_on": [],
  "checks": [
    "terra-harness"
  ]
}
```

```json
{
  "kind": "domain",
  "id": "terra-qc-json",
  "owner": "et",
  "purpose": "Imported frozen statistical QC and additive location-assessment JSON; existing ChillWatch implementation remains authoritative",
  "paths": [
    "backend/terra_qc/**",
    "backend/terra_monitoring/**",
    "config/terra/**",
    "docs/terra/**",
    "tests/test_terra_json.py",
    "tests/test_terra_qc_*.py",
    "tests/fixtures/terra/**",
    "requirements-terra.txt",
    "requirements-terra-dev.txt",
    "README.md",
    ".gitignore",
    "web/**",
    ".github/workflows/terra.yml",
    "spec.md",
    "tests/test_terra_live.py",
    "tests/test_terra_server.py",
    "tests/test_terra_sms.py",
    "tests/test_terra_sms_server.py",
    "tests/fixtures/terra_live/**",
    ".env.example",
    "tests/test_terra_pipeline.py",
    "scripts/terra-service/**",
    "tests/test_terra_runtime.py",
    "tests/test_farm_chill_ops.py",
    "tests/test_farm_chill_ops_api.py",
    "tests/test_farm_chill_ops*.py"
  ],
  "depends_on": [],
  "checks": [
    "terra-json-tests",
    "terra-sms-tests",
    "terra-farm-tests",
    "terra-runtime-tests",
    "terra-pipeline-tests"
  ]
}
```

```json
{
  "kind": "domain",
  "id": "terra-portability",
  "owner": "et",
  "purpose": "Narrow consolidation repairs: portable Windows grid label, declared plotting dependency, synthetic demo/test season bounds aligned to configured September start. Nathan retains numeric algorithms/configuration. Fast check validates coordination; mandatory complete backend CI validates grid and replay. Synthetic model fixture receives September training coverage for configured September replay, with unchanged seed/folds/December end/assertions; no real-data model refit.",
  "paths": [
    "backend/chillwatch/repair/grid.py",
    "pyproject.toml",
    "backend/chillwatch/demo/scenario.py",
    "tests/test_scenario.py",
    "tests/conftest.py"
  ],
  "depends_on": [],
  "checks": [
    "terra-harness"
  ]
}
```

```json
{
  "kind": "check",
  "id": "terra-harness",
  "argv": [
    "{python}",
    "-B",
    "-m",
    "unittest",
    "discover",
    "-s",
    "tests",
    "-p",
    "test_terra_harness.py",
    "-q"
  ],
  "timeout": 120
}
```

```json
{
  "kind": "check",
  "id": "terra-json-tests",
  "argv": [
    "{python}",
    "-B",
    "-m",
    "pytest",
    "--noconftest",
    "-o",
    "pythonpath=backend",
    "tests/test_terra_json.py",
    "tests/test_terra_qc_reference.py",
    "tests/test_estimate.py",
    "tests/test_terra_live.py",
    "tests/test_terra_server.py",
    "-q"
  ],
  "timeout": 120
}
```

```json
{
  "kind": "task",
  "id": "terra-qc-json",
  "owner": "et",
  "status": "done",
  "intent": "ET requested a cleaned tested statistical checkpoint in the shared GitHub repository, then a first additive JSON/UI integration with Nathan estimate_location. ET selected local estimates plus relevant station flags at unsensored locations, and measured comparisons where sensors exist. Preserve existing ingestion/model/repair and all source evidence; no refit, extra-variable adoption, provider calls or external messaging. Review the first coherent JSON contract with ET before expanding the product.",
  "branch": "codex/terra-qc-json",
  "base": "fc47bf301b87bed1d6f328b01adfac904dc229e3",
  "domains": [
    "terra-coordination",
    "terra-qc-json"
  ],
  "paths": [
    "AGENTS.md",
    "CLAUDE.md",
    "COORDINATION.md",
    "terra.py",
    "tests/test_terra_harness.py",
    "backend/terra_qc/**",
    "backend/terra_monitoring/**",
    "config/terra/**",
    "docs/terra/**",
    "tests/test_terra_json.py",
    "tests/test_terra_qc_*.py",
    "tests/fixtures/terra/**",
    "requirements-terra.txt",
    "requirements-terra-dev.txt",
    "README.md",
    ".gitignore",
    "web/**",
    ".github/workflows/terra.yml",
    "spec.md",
    ".gitattributes"
  ],
  "depends_on": [],
  "contracts": [],
  "artifacts": [],
  "handoff": {
    "revision": "bc9b24eab76556f225367bcf0d4b3f9daecd552f",
    "summary": "First additive QC-to-location-JSON/UI slice implemented and verified in a separate checkout. Final cleaned statistical source/reference identities match; existing Nathan code/config/pipeline preserved.",
    "evidence": [
      "53 pure estimator/adapter/reference cases plus31harness cases pass; scoped check against actual origin/main passes.",
      "Full original cleanup reports200tests and identical25case/2400prefix snapshot; all imported files independently hash-reconciled against final handoff.",
      "Frontend build+4packaging tests pass; desktop/mobile unavailable/stale/file-error/focus behavior verified. Downloaded JSON equals generated packet; warning/error log empty.",
      "Actual61.0F/37.3F/+23.7F calibrated example and37.0F uninstrumented estimate retain separate meanings; peak time labelled separately."
    ],
    "uncertainty": "First slice renders saved JSON examples or files. Live location API, continuous monitoring, subscriptions, external SMS and new chill metrics are not implemented. Unknown measured sites have no calibrated fault threshold. External clipboard round-trip unverified. Natural fault labels absent.",
    "next": "Review the concrete JSON/UI in draft PR3 with ET, then select the live location-query input/behavior before expanding. No merge or deployment in this checkpoint.",
    "pull_request": "https://github.com/EthanVTruong/terra/pull/3"
  }
}
```

```json
{
  "kind": "task",
  "id": "terra-main-consolidation",
  "owner": "et",
  "status": "done",
  "intent": "User explicitly authorizes verification of the combined technical layer and branch consolidation into main. Audit PR3/worker branch inclusion, run full existing backend tests plus pure statistical JSON/UI checks, resolve verified integration defects within ownership, then promote/merge exact reviewed head if all checks support it. Preserve original Terra staged checkout, Nathan algorithms/config and recovery branches; no new monitoring/SMS features or provider ingestion.",
  "branch": "codex/terra-qc-json",
  "base": "fc47bf301b87bed1d6f328b01adfac904dc229e3",
  "domains": [
    "terra-coordination",
    "terra-qc-json",
    "terra-portability"
  ],
  "paths": [
    "AGENTS.md",
    "CLAUDE.md",
    "COORDINATION.md",
    "terra.py",
    "tests/test_terra_harness.py",
    "backend/terra_qc/**",
    "backend/terra_monitoring/**",
    "config/terra/**",
    "docs/terra/**",
    "tests/test_terra_json.py",
    "tests/test_terra_qc_*.py",
    "tests/fixtures/terra/**",
    "requirements-terra.txt",
    "requirements-terra-dev.txt",
    "README.md",
    ".gitignore",
    "web/**",
    ".github/workflows/terra.yml",
    "spec.md",
    ".gitattributes",
    "backend/chillwatch/repair/grid.py",
    "pyproject.toml",
    "backend/chillwatch/demo/scenario.py",
    "tests/test_scenario.py",
    "tests/conftest.py"
  ],
  "depends_on": [
    "terra-qc-json"
  ],
  "contracts": [],
  "artifacts": [],
  "handoff": {
    "revision": "d8ced9a8173e96150cf6ef474b2f38b8b152698e",
    "summary": "PR #3 merged into main at 1d5caf7 after all four CI jobs passed on reviewed head 5ffec23. Shared main was fast-forwarded and matched the tested PR tree exactly. All intended backend, statistical QC, JSON and web code is consolidated. The source snapshots and original staged prototype are preserved. Follow-up changes only record completion and ignore generated Vite cache.",
    "evidence": [
      "Final CI run 40073189344: 173 Python tests and eight subtests passed in 219.33 seconds. PostgreSQL 16 migrations and both database tests executed, with zero skipped tests; pip check passed.",
      "Windows and macOS minimal-runtime jobs each passed 64 targeted cases plus 31 harness cases. Production web build and four packaging cases passed. Independent review verified 15 focused regressions and all six source-import hashes plus the frozen reference SHA.",
      "Browser and CLI examples agree: 61.0 F observed, 37.3 F expected, 35.1–41.5 F typical range, +23.7 F departure. Missing/stale/unknown inputs, malformed JSON, desktop/mobile focus and exact downloads verified.",
      "Two grid and eight synthetic model/scenario cases passed, including all four faults with original assertions, seeds, folds and thresholds. Calendar/fixture coverage needed repair; no numerical algorithm or real-data model refit changed.",
      "GitHub confirmed merged=true at 1d5caf7, containing reviewed head 5ffec23 and prior Nathan main 229106c. The merged tree matched tested source exactly. Original Terra still has the same nine staged entries and numstat counts.",
      "Completion follow-up check passed against actual merged origin/main 1d5caf7 (31 harness + 64 targeted cases). Only .gitignore and this coordination record changed afterward; application source remains identical to tested 5ffec23."
    ],
    "uncertainty": "Software and controlled-fault validation only. Natural-fault accuracy and live credentials remain unverified. Live API, continuous worker, subscriptions and SMS remain future scope. External clipboard fidelity is unverified. Standalone estimator lapse operation-date limitation remains documented; the assessment adapter enforces the gate. Worker snapshot refs remain for recovery.",
    "next": "Use shared main for the next assigned task. Review live-query and always-on product scope separately before implementation. No remaining source integration is needed from the reviewed worker snapshots.",
    "pull_request": "https://github.com/EthanVTruong/terra/pull/3",
    "verification_run": "https://github.com/EthanVTruong/terra/actions/runs/40073189344"
  }
}
```

```json
{
  "kind": "task",
  "id": "terra-authorship",
  "owner": "et",
  "status": "done",
  "intent": "User explicitly requests existing Codex commits attributed to Ethan and existing Claude commits to Nathan so main has only those two human contributors. Correct exact assistant author/committer identities and assistant co-author trailers, preserve original commit trees/dates/topology and initial signature, retain recovery bundle/map, configure future project identity, and publish only main plus the corresponding existing ChillWatch branch with atomic exact-head leases. This specifically requested metadata rewrite is a bounded exception to the general no-force-push policy; do not overwrite new work or rewrite unrelated repositories.",
  "branch": "codex/terra-authorship",
  "base": "ddad65f9148f88ea40b03de15e86ae51722aef17",
  "domains": [
    "terra-coordination",
    "terra-qc-json"
  ],
  "paths": [
    "AGENTS.md",
    "COORDINATION.md",
    "docs/terra/**"
  ],
  "depends_on": [
    "terra-main-consolidation"
  ],
  "contracts": [],
  "artifacts": [],
  "handoff": {
    "revision": "b843f1836ad47e0f6fe133677de36b411004b0aa",
    "summary": "Requested attribution correction published atomically with exact-head leases. Codex author/committer identities map to Ethan; Claude identities map to Nathan; 37 assistant co-author trailers removed. Both published branches corrected, local worktree refs mapped without changing their files, recovery bundle retained. Future local identity and repository instructions use human attribution.",
    "evidence": [
      "Independent raw-object audit: 62 mapped trees unchanged, 64 ordered parent edges preserved, 124 author/committer timestamps and timezones preserved; every message byte preserved except 37 assistant co-author lines.",
      "Exactly 40 invalidated signatures removed; original signed root 4000c04 remains byte-identical. Original application/test content unchanged.",
      "Published rewrite heads: main 0226b51fa8d63d7a3d9e536bfda9110350bea3e4; ChillWatch c00b212ff10e4f15dac3b23acb2df09e76eb2ffd. Main at rewrite: 16 Ethan / 39 Nathan authors; subsequent documentation commits are Ethan.",
      "Verified recovery bundle SHA-256 398c12cc6086444a3b36b69c7917ab039deb24ba6c627d172891922775a4b764; public old/new mapping docs/terra/authorship-map.json.",
      "Repository-local author defaults are Ethan Truong / 41927541+EthanVTruong@users.noreply.github.com. Original Terra staged file contents preserved.",
      "Completion scoped check passed against published rewritten origin/main: 31 harness and 64 pure JSON/reference/estimator cases. GitHub independently resolves rewritten Ethan commits to EthanVTruong and Nathan commits to Nathan-W123. All local reachable commit authors are these two humans."
    ],
    "uncertainty": "Old PR/CI links still reference original commits and contributor displays may be cached. Signed descendants lose their old verification badges when metadata changes. Other clones must resync or re-clone rather than merge old history.",
    "next": "Use rewritten main. Apply the published mapping for historical SHA references; preserve uncommitted work when resyncing other clones. Live-query implementation remains owned by its separate chat."
  }
}
```


```json
{
  "kind": "task",
  "id": "terra-live-location-queries",
  "owner": "et",
  "status": "done",
  "intent": "After user-requested authorship correction, refresh corrected shared main and connect on-demand LIVE provider observations through the existing frozen QC/location-assessment JSON into the web UI. User explicitly asks full interoperable-location and flagging verification. Reuse Nathan transport/estimator unchanged; preserve strict raw units/QC/hour-ending/operating-date and causal visibility. Fetch only bounded recent context/candidate stations; no historical ingestion, model fitting/tuning, live alert dispatch, continuous schedule or deployment. Keep examples/injections distinct from real current data and review the first working live slice with ET.",
  "branch": "codex/terra-live-location-queries",
  "base": "0b1554930038d981881742d3a84b29e99dff595b",
  "domains": [
    "terra-coordination",
    "terra-qc-json"
  ],
  "paths": [
    "COORDINATION.md",
    "backend/terra_monitoring/**",
    "tests/test_terra_live.py",
    "tests/test_terra_server.py",
    "tests/fixtures/terra_live/**",
    "requirements-terra.txt",
    "requirements-terra-dev.txt",
    ".env.example",
    "docs/terra/**",
    "spec.md",
    "README.md",
    "web/**",
    ".github/workflows/terra.yml"
  ],
  "depends_on": [
    "terra-authorship"
  ],
  "contracts": [],
  "artifacts": [],
  "handoff": {
    "revision": "cfe3d5541b0496c2d4abd4685e64de945c4602b4",
    "summary": "Live-source/API/UI checkpoint complete and pushed as PR4. Exact source revision cfe3d55 passed all four CI jobs, including full native/PostgreSQL and portable pure runtime checks. Integration owner retains final main merge; no product-review approval is needed to consume this accepted interface.",
    "evidence": [
      "Scoped terra.py check passes against origin/main 0b155493: 31 harness and 189 pure JSON/reference/estimator/source/HTTP cases. New source/API coverage includes 125 cases, synthetic full HTTP composition, N5/extreme exclusions, malformed/future/unit/QC inputs, cache age/failure and restart visibility.",
      "Bounded actual CIMIS batch succeeded: custom 38.6,-121.84,70ft estimated 61.04305453536202F from six stations at valid 2026-10-24T07:00Z; first retrieval 07:53:42Z. A measured Winters query returned 65.2F observed /61.725F expected/no_alert. Outside coverage returns null/insufficient_data.",
      "Real hour transition also verified: at 08:01Z CIMIS reported the 08:00Z hour with missing M temperatures. UI withholds estimate and fault conclusion rather than selecting an older good hour. No natural fault truth is claimed.",
      "Browser custom query displayed current provenance and exactly matching JSON download. Production web build and four packaging tests pass. Cold import brings in no SQLAlchemy, psycopg, scipy, sklearn or LightGBM; original algorithms/calibration unchanged.",
      "All four jobs passed for exact source cfe3d5541b0496c2d4abd4685e64de945c4602b4: PostgreSQL16 migration/full native suite with zero skipped tests; Windows and macOS189 pure/runtime +31 harness; production web build +4 packaging cases. Final follow-up changes only this coordination record.",
      "Live matrix at08:15UTC reached all five calibrated presets, uncalibrated station243, arbitrary and outside coordinates with valid200 packets. Target observations stay independent/held out; unavailable current-hour M readings never create a fault conclusion. Earlier usable custom/measured cases are retained in local evidence.",
      "Browser1280/390/320 checks passed: exact downloaded JSON equality, measured/custom association, source time separation, lost connection retention/retry, delayed cancellation, superseding a pending live result with an injected example, malformed exclusions rejected without losing the last packet, and mobile JSON focus/escape/no horizontal page overflow.",
      "Native chillwatch algorithms/config and frozen reference unchanged; SHA256809c78b69d80d93d73f5b4b75ca6e74b0759ddcebf1e17fa2450386b44fcd90f. All new commits use Ethan human attribution. Original Terra staged bootstrap and unrelated worktrees preserved."
    ],
    "uncertainty": "Software and controlled-fault validation only; natural-fault accuracy remains unverified. Latest reported hourly placeholders may be missing while the provider publishes: the current-hour result is withheld and data_health retains M/source_missing evidence. Fault calibration remains limited to five stations. Continuous monitoring, SMS and hosting belong to the separate integration work.",
    "next": "Integration owner merges PR4 after its checks and consumes LiveQueryService.assess/query JSON. Live-source scope has no remaining implementation work. Three isolated worker checkpoints are preserved on codex/terra-live-api b69a76a, codex/terra-live-ui 64b88b0 and codex/terra-live-tests 9685d46; their clean worktrees were removed.",
    "pull_request": "https://github.com/EthanVTruong/terra/pull/4",
    "verification_run": "https://github.com/EthanVTruong/terra/actions/runs/40077329673"
  }
}
```

```json
{
  "kind": "task",
  "id": "terra-full-pipeline",
  "owner": "et",
  "status": "active",
  "intent": "ET explicitly requested a /goal to autonomously guide existing Terra chats, finish and coordinate UI/livebackend/persistentfarmchill monitoring/Twilio data→flag→message pipeline, verify readiness and merge/push working integration while ET sleeps. Root is final integration owner, answers routine cross-chat questions using accepted decisions, preserves domain ownership/human attribution/original staged files, and distinguishes real delivery from labelled controls and account blockers. Twilio account/password/verification/paid steps require real access and any applicable user confirmations; no fabricated delivery or silent cost authority. GitHub contributor cache refresh only through supported safe controls.",
  "branch": "codex/terra-end-to-end",
  "base": "37948ed4f76cbbfbef7b4570d8ed06b84c6e8723",
  "domains": [
    "terra-coordination",
    "terra-qc-json"
  ],
  "paths": [
    "AGENTS.md",
    "COORDINATION.md",
    "backend/terra_monitoring/**",
    "tests/test_terra*.py",
    "tests/fixtures/terra_live/**",
    "tests/fixtures/terra/**",
    "scripts/terra-service/**",
    "requirements-terra.txt",
    "requirements-terra-dev.txt",
    ".env.example",
    "docs/terra/**",
    "spec.md",
    "README.md",
    "web/**",
    ".github/workflows/terra.yml",
    "tests/test_farm_chill_ops.py",
    "tests/test_farm_chill_ops_api.py"
  ],
  "depends_on": [
    "terra-authorship"
  ],
  "contracts": [],
  "artifacts": [],
  "handoff": {
    "revision": "3117d2b50da8c510adb38febe89726071bfb422f",
    "summary": "PR4 merged after all exact-head checks passed. Frozen SMS transport and unified runtime integrated; farm owner finalizes receipt monotonicity and UI owner connects accepted incoming design to actual persisted feed. Root owns final whole-layer tests, scope reconciliation, publication and local service handoff.",
    "evidence": [
      "Live PR4 22d4594 all four CI jobs green; merged37948ed includes PostgreSQL with zero skips. SMSf855 software handoff203 cases green; runtimeea78 overlay34cases green, merged-tree rerun pending.",
      "Cross-layer acceptance worker uses actual classes and mock transport, no real sends. Baseline+SMS fastcheck exceeded120s after scope growth; one focused harness repair separates independently bounded groups without reducing coverage."
    ],
    "uncertainty": "Real Twilio custom SMS blocked: requested tester not yet verified, no owned approved sender, trial custom-body restrictions, no spending answer. Existing Auth Token appeared only in a private tool trace; credential rotation required before activation. Both SMS flags remain false. No real messages accepted/delivered. Githubsidebar cache server-controlled.",
    "next": "Merge farm and combined UI owner sources; run all groups, real CIMIS smoke, worker restart/delivery mock acceptance and browser checks; publish verified main. Keep account gates explicit and preserve original staged bootstrap."
  }
}
```


```json
{
  "kind": "check",
  "id": "terra-sms-tests",
  "argv": [
    "{python}",
    "-B",
    "-m",
    "pytest",
    "--noconftest",
    "-o",
    "pythonpath=backend",
    "tests/test_terra_sms.py",
    "tests/test_terra_sms_server.py",
    "-q"
  ],
  "timeout": 120
}
```


```json
{
  "kind": "check",
  "id": "terra-farm-tests",
  "argv": [
    "{python}",
    "-B",
    "-m",
    "pytest",
    "--noconftest",
    "-o",
    "pythonpath=backend",
    "tests/test_farm_chill_ops.py",
    "tests/test_farm_chill_ops_api.py",
    "-q"
  ],
  "timeout": 120
}
```


```json
{
  "kind": "check",
  "id": "terra-runtime-tests",
  "argv": [
    "{python}",
    "-B",
    "-m",
    "pytest",
    "--noconftest",
    "-o",
    "pythonpath=backend",
    "tests/test_terra_runtime.py",
    "-q"
  ],
  "timeout": 120
}
```


```json
{
  "kind": "check",
  "id": "terra-pipeline-tests",
  "argv": [
    "{python}",
    "-B",
    "-m",
    "pytest",
    "--noconftest",
    "-o",
    "pythonpath=backend",
    "tests/test_terra_pipeline.py",
    "-q"
  ],
  "timeout": 120
}
```


```json
{
  "kind": "check",
  "id": "farm-chill-ops-tests",
  "argv": [
    "{python}",
    "-B",
    "-m",
    "pytest",
    "--noconftest",
    "-o",
    "pythonpath=backend",
    "-p",
    "no:cacheprovider",
    "tests/test_farm_chill_ops.py",
    "tests/test_farm_chill_ops_api.py",
    "-q"
  ],
  "timeout": 120
}
```


```json
{
  "kind": "task",
  "id": "farm-chill-ops",
  "owner": "et",
  "status": "active",
  "intent": "User explicitly requested a goal to finish the compact LLM contract/system prompt and tidy persistent farm-chill-ops backend after the product interview. Build the scoped seasonal monitor, orchard storage, selective event lifecycle, local inbox, factual Q&A, optional LLM/fallback, CLI/HTTP and verification. No new model fitting, bulk provider backfill, GDD/forecast/SMS/hosting/auth scope. Preserve Nathan and active live-query work.",
  "branch": "codex/farm-chill-ops",
  "base": "7f37e2670103fc2efa83c272e1cab285e3616620",
  "domains": [
    "terra-qc-json",
    "terra-coordination"
  ],
  "paths": [
    "COORDINATION.md",
    "backend/terra_monitoring/farm_ops/**",
    "tests/test_farm_chill_ops*.py",
    "docs/terra/farm-chill-ops.md",
    "docs/terra/farm-chill-ops/**"
  ],
  "depends_on": [
    "terra-main-consolidation"
  ],
  "contracts": [],
  "artifacts": [],
  "handoff": {
    "revision": "7f37e2670103fc2efa83c272e1cab285e3616620",
    "summary": "Isolated worktree based on committed live-query interface. Product choices locked: Nonpareil/Davis; estimated 32-45F chill from Nov1; separate FNRIC details; two completed days below20 before pace warning; one opening/escalation/recovery; recovery3valid readings; combine urgent-first; LLM message-only; important changes only, anytime; source faults quiet while orchard estimate usable.",
    "evidence": [
      "Goal explicitly created in figure out terra spec chat. Prior 64 estimator/adapter checks passed; live-query owner independently verifies the evolving transport."
    ],
    "uncertainty": "Live-query owner has uncommitted fixes that are excluded from this isolated base. No key/provider call, external delivery or deployment is authorized by this task. Real same-orchard 5-season history may be unavailable; withhold comparison.400h cultivar page does not establish matched chill definition, so default target is a configured reference only, not a biological requirement.",
    "next": "Implement persistent monitor and compact writer contract, exercise restart/failure/causal replay paths, run scoped checks and leave a reviewable branch."
  }
}
```
