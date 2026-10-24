# Terra integration coordination

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
    "tests/fixtures/terra_live/**",
    ".env.example",
    "tests/test_terra_pipeline.py",
    "scripts/terra-service/**"
  ],
  "depends_on": [],
  "checks": [
    "terra-json-tests"
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
      "Browser and CLI examples agree: 61.0 F observed, 37.3 F expected, 35.1\u201341.5 F typical range, +23.7 F departure. Missing/stale/unknown inputs, malformed JSON, desktop/mobile focus and exact downloads verified.",
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
  "status": "active",
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
    "revision": "0b1554930038d981881742d3a84b29e99dff595b",
    "summary": "Live CIMIS source, strict raw normalization, loopback HTTP boundary and web query controls implemented on corrected main. Minimal integration checks pass. Browser live/custom/measured, mobile, connection failure, cancellation, supersession and malformed-file checks pass. Draft PR4 first CI green; final per-cohort cache-provenance repair is verified locally.",
    "evidence": [
      "Scoped terra.py check passes against origin/main 0b155493: 31 harness and 189 pure JSON/reference/estimator/source/HTTP cases. New source/API coverage includes 125 cases, synthetic full HTTP composition, N5/extreme exclusions, malformed/future/unit/QC inputs, cache age/failure and restart visibility.",
      "Bounded actual CIMIS batch succeeded: custom 38.6,-121.84,70ft estimated 61.04305453536202F from six stations at valid 2026-10-24T07:00Z; first retrieval 07:53:42Z. A measured Winters query returned 65.2F observed /61.725F expected/no_alert. Outside coverage returns null/insufficient_data.",
      "Real hour transition also verified: at 08:01Z CIMIS reported the 08:00Z hour with missing M temperatures. UI withholds estimate and fault conclusion rather than selecting an older good hour. No natural fault truth is claimed.",
      "Browser custom query displayed current provenance and exactly matching JSON download. Production web build and four packaging tests pass. Cold import brings in no SQLAlchemy, psycopg, scipy, sklearn or LightGBM; original algorithms/calibration unchanged."
    ],
    "uncertainty": "Local on-demand service only. Initial PR4 CI passed all four jobs including native/PostgreSQL with zero skips. The final cache repair still needs its exact-revision CI run. Natural-fault accuracy is unverified; automatic numerical fault calibration remains limited to five stations. Always-on worker, SMS and hosting are future work.",
    "next": "Verify final cache-repair CI on draft PR4 and keep the loopback preview available. Review working live path with ET before the continuous monitoring/delivery slice."
  }
}
```

```json
{
  "kind": "task",
  "id": "terra-full-pipeline",
  "owner": "et",
  "status": "active",
  "intent": "ET explicitly requested a /goal to autonomously guide existing Terra chats, finish and coordinate UI/livebackend/persistentfarmchill monitoring/Twilio data\u2192flag\u2192message pipeline, verify readiness and merge/push working integration while ET sleeps. Root is final integration owner, answers routine cross-chat questions using accepted decisions, preserves domain ownership/human attribution/original staged files, and distinguishes real delivery from labelled controls and account blockers. Twilio account/password/verification/paid steps require real access and any applicable user confirmations; no fabricated delivery or silent cost authority. GitHub contributor cache refresh only through supported safe controls.",
  "branch": "codex/terra-end-to-end",
  "base": "0b1554930038d981881742d3a84b29e99dff595b",
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
    ".github/workflows/terra.yml"
  ],
  "depends_on": [
    "terra-authorship"
  ],
  "contracts": [],
  "artifacts": [],
  "handoff": {
    "revision": "cfe3d5541b0496c2d4abd4685e64de945c4602b4",
    "summary": "Root orchestration worktree isolated at Terra-orchestrator. Live API owner finalizes PR4; persistent farm-chill ops owner implements monitoring/event state; Twilio owner handles console/credentials/transport and private opted-in phone; UI owner ports accepted Grok-like prototype onto real live app; logo owner provides only approved assets. Root owns contract reconciliation, cross-layer acceptance, merges/readiness and final handoff.",
    "evidence": [
      "User explicitly authorizes guiding and answering all other chats. Accepted decisions:32\u201345F fromNov1 for orchard/current/historical, FNRIC separate, two completed days below20th before pacewarning, updatesanytime/no-repeat, incomingonlythreevalues/minimalcopy/evidenceaside, frozenfive-stationQC/Nathanmath unchanged.",
      "PR4headcfe3d55:189pure+31harness and webbuild/4tests green; final fullPostgreSQL CI pending. Twilio account remains sign-up/no sender/credentials/real send. Private recipient already provided in Twilio chat. Root async spending-limit question pending; no paid changes authorized yet."
    ],
    "uncertainty": "Current Twilio trial/customSMS restrictions and sender approvals may block real delivery; actual account must establish readiness. New external keys, identity/phone checks and legal/payment steps cannot be fabricated. Full integration/source/receipt/workerrestart tests still required. Githubsidebar cache remains server-controlled although APIalready2humans.",
    "next": "Agree durable event/transport/UI contracts, finish owners isolatedwork, mergePR4whenexactCIgreen, integrate worker/SMS/UI oncehandedoff, run end-to-end+fullCI/browser/demo delivery, publish verifiedmain and report any unavoidable account dependency."
  }
}
```
