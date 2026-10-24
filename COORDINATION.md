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
    "spec.md"
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
  "base": "229106c251cfa41993a747cde2a40f29e53ed60f",
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
    "revision": "a67837b1b3c5456c510f02361cdefb7cf5ffde3e",
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
  "base": "229106c251cfa41993a747cde2a40f29e53ed60f",
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
    "revision": "094e4e6f953ab0ebdd451f705f229f2f462d88e8",
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
