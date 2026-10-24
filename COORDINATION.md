# Terra integration coordination

```json
{
  "kind": "task",
  "id": "terra-ui-fidelity",
  "owner": "et",
  "status": "active",
  "intent": "User requests a further design pass matching the exact Grok Bot / Fliptexts references, approved lowercase terra wordmark, interaction design and animation polish. Preserve accepted compact conversation, three-value notes, pinned temperature context and the current live assessment contract. Isolated checkout based on the live-query commit; no backend edits, provider ingestion, training, delivery, deployment or changes to another chat's files.",
  "branch": "codex/terra-ui-fidelity",
  "base": "7f37e2670103fc2efa83c272e1cab285e3616620",
  "domains": [
    "terra-coordination",
    "terra-qc-json"
  ],
  "paths": [
    "COORDINATION.md",
    "web/**",
    "docs/terra/ui-fidelity.md"
  ],
  "depends_on": [],
  "contracts": [],
  "artifacts": [],
  "handoff": {
    "revision": "7f37e2670103fc2efa83c272e1cab285e3616620",
    "summary": "Independent visual slice complete on the committed live API: exact source-backed shell/type/bubbles, approved wordmark-only header, compact incoming three-value notes, pinned linked temperature evidence/JSON, anchored source/location controls, mobile focus and restrained motion. Root owns final pipeline integration; the next additive slice can connect its frozen farm feed/SMS components.",
    "evidence": [
      "Actual in-app source inspection plus combined screenshot at .terra-local/ui-fidelity/comparison-final.png. Native target 744x768, approximately 424px chat /320px evidence; 44px header; source font and 14/20,8/12,16/16/16/6 bubble values.",
      "Approved 1080px logo pair copied byte-identically to web/public/brand; wordmark SHA 48aa13f9b307fc37b9abcc0ab4b064aa358cbea7b97fef530cc7b5544fb13955; manifest/README retained.",
      "Desktop, 390px and 320px browser checks: exact download=renderedJSON, linked tab/evidence selection, close/reopen, popover/Escape focus, mobile Shift-Tab wrap and opener restoration, current-query keyboard return, unavailable/expected-null rendering and clean final console.",
      "Actual CIMIS custom estimate 60.1°F and Davis 60.4°F observed /60.0°F expected verified. Separate delayed 503/cancel/disconnected probes retain previous 37.0°F replay; no SMS sent.",
      "Production build and 4 packaging cases pass. Scoped terra.py check against 7f37e26 passes in shared configured .venv; original minimal .venv lacks pytest. Algorithms/data/contracts unchanged.",
      "web/design-qa.md passes this presentation scope; native source control behavior, physical touch, OS reduced-motion toggle and external clipboard paste remain explicitly unverified."
    ],
    "uncertainty": "Static Grok shell does not expose native interaction timings; Terra 220/180ms durations are proposals using the source 350ms widget curve. Original Terra staged bootstrap untouched. Monitoring/SMS routes and delivery readiness are separate root-owned integration, with actual farm response fixture now supplied; provider acceptance must stay distinct from delivery.",
    "next": "Root can cherry-pick the independent visual commit on merged live main 37948ed. Connect the canonical /api/orchards feed and owner SmsControls in a separate additive UI commit after the unified server is available; preserve pure /api/assess and incoming-only behavior."
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
    "tests/fixtures/terra_live/**",
    ".env.example"
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
    "summary": "Live CIMIS source, strict raw normalization, loopback HTTP boundary and web query controls implemented on corrected main. Minimal integration checks pass. Completing browser failure/mobile verification and GitHub CI before review.",
    "evidence": [
      "Scoped terra.py check passes against origin/main 0b155493: 31 harness and 187 pure JSON/reference/estimator/source/HTTP cases. New source/API coverage includes 123 cases, synthetic full HTTP composition, N5/extreme exclusions, malformed/future/unit/QC inputs, cache age/failure and restart visibility.",
      "Bounded actual CIMIS batch succeeded: custom 38.6,-121.84,70ft estimated 61.04305453536202F from six stations at valid 2026-10-24T07:00Z; first retrieval 07:53:42Z. A measured Winters query returned 65.2F observed /61.725F expected/no_alert. Outside coverage returns null/insufficient_data.",
      "Real hour transition also verified: at 08:01Z CIMIS reported the 08:00Z hour with missing M temperatures. UI withholds estimate and fault conclusion rather than selecting an older good hour. No natural fault truth is claimed.",
      "Browser custom query displayed current provenance and exactly matching JSON download. Production web build and four packaging tests pass. Cold import brings in no SQLAlchemy, psycopg, scipy, sklearn or LightGBM; original algorithms/calibration unchanged."
    ],
    "uncertainty": "Local on-demand service only. Full native/PostgreSQL and portable CI still to run on this branch. Natural-fault accuracy is unverified; automatic numerical fault calibration remains limited to five stations. Always-on worker, SMS and hosting are future work.",
    "next": "Finish mobile/failure/race browser checks, publish draft PR and inspect full native/PostgreSQL plus portable CI. Review working live path with ET before the continuous monitoring/delivery slice."
  }
}
```
