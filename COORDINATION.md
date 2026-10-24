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
  "purpose": "Narrow consolidation verification repair: Windows grid-export date label and declared plotting dependency; Nathan retains the grid/interpolation/model algorithms; configured fast check validates scoped coordination, while mandatory full backend CI tests actual export with PostgreSQL and plotting installed",
  "paths": [
    "backend/chillwatch/repair/grid.py",
    "pyproject.toml"
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
  "status": "active",
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
    "pyproject.toml"
  ],
  "depends_on": [
    "terra-qc-json"
  ],
  "contracts": [],
  "artifacts": [],
  "handoff": {
    "revision": "38a7ea6e4e271275731a4e6cce1d2f5df3eeff16",
    "summary": "Root owns verification, repairs and guarded merge. Independent review confirms worker content is integrated. Restore lost calibrated range and numeric unavailable JSON, apply operation-date eligibility at adapter boundary, and repair existing Windows grid label/missing plotting dependency; Nathan calculation algorithms and frozen QC remain unchanged.",
    "evidence": [
      "53 original pure checks and scoped harness pass; updated adapter repair passes58 cases. Browser downloads match CLI and calibrated range is visible; source missing/stale/unknown states remain explicit.",
      "Full fresh backend environment exposed existing Windows %-d formatting and missing matplotlib packaging defects. These narrow portability repairs are assigned to the isolated backend worker; full Ubuntu/PostgreSQL CI has been added."
    ],
    "uncertainty": "PriorCI intentionally ran53puretests/31harness anddidnotprove fulloriginalbackend. LiveAPI/always-on/SMSremain documentedfuturefeatures. Actualremotehead andfreshCI mustberechecked beforemerge.",
    "next": "Complete fullbackend+UI+independent review, recordresults andminimalrepairs, run requiredcheckagainstactualorigin/main, mergewith expected-head guard andverify main."
  }
}
```
