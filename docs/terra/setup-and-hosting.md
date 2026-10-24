# Setup and hosting assessment

Audited on 2026-10-24 against main `cb580227851d6194dc6a001bbe51e3b615f4dba2`, including the local ChatGPT connection. This change improves local setup; it does not turn the runtime into a hosted multi-user service.

**Selected direction:** Terra remains local-first and open source. Complete the
local quickstart and reviewed interface; defer hosted migration. The Vercel
assessment below records what would be needed if that scope changes.

## Local setup

Install Python 3.12 (CI baseline), Node.js 24 LTS and pnpm 11.25.0. Minimum Node is 22.13, matching the pinned pnpm requirement. If needed, install pnpm with `npm install --global pnpm@11.25.0`. Linux distributions that package Python's `venv`/pip separately need those packages too.

Run `python run_terra.py` from a clone (`python3` where appropriate), then visit the printed URL. The launcher:

1. Checks prerequisites and the selected port before installing.
2. Creates or reuses `.venv`; it never replaces an existing environment automatically.
3. Installs exact versions from `requirements-terra.txt`, without native training or test dependencies.
4. Installs the frozen pnpm lockfile and builds the client. Matching Python dependencies and an unchanged web build are reused. Changes to the client, public examples, package metadata or build inputs invalidate the build cache.
5. Runs the canonical UI/API process with absolute paths and internal `PYTHONPATH`; shell activation is unnecessary.

The default is a saved-example preview: historical/controlled packets, no source credentials, worker, ChatGPT or SMS. Keys inherited from the shell or the legacy root `.env` cannot activate those preview capabilities. Its state is separate in `.terra-local/preview`.

`python run_terra.py --live --env-file /absolute/path/to/cimis.env` enables normal operator configuration, ChatGPT controls and the five-minute monitor. Supply `CIMIS_APP_KEY` in that file or the process environment. `--sms-env-file` preserves explicit SMS configuration rules. `--no-worker` gives on-demand live queries; `--no-chatgpt` disables plan controls. `--chatgpt-state-dir` selects a stable protected local connection directory. These commands cannot grant provider consent or verify a recipient automatically.

Live state defaults to `.terra-local/runtime`; use `--state-dir /absolute/private/path` to keep it outside a checkout. Back up farm, source, ChatGPT and SMS records privately. Explicit existing `TERRA_SMS_DB_PATH` still takes precedence in live mode. Startup keeps the existing fail-closed journal migration rules; never delete delivery history or a saved pause to bypass a startup problem.

## Troubleshooting

```sh
python run_terra.py --doctor
python run_terra.py --setup-only
python run_terra.py --port 8520
```

Doctor does not install packages, read private configuration contents or contact weather/AI/SMS providers. It returns a nonzero exit code when prerequisites, dependencies, a current build or the selected port need attention. Installation readiness is separate from weather coverage, model permission or phone delivery.

| Symptom | Action |
| --- | --- |
| Python missing/old | Use the installed Python 3.12 command, often `python3` or `py -3.12`. |
| `venv`/pip unavailable | Install your Python distribution's venv/pip support. An incomplete `.venv` is retained for explicit repair. |
| pnpm missing/wrong version | Install pnpm 11.25.0; the frontend package now declares the manager and minimum Node. |
| Download/build fails | Fix the reported network/tool error and rerun; partial setup is retryable and journals are retained. |
| Port busy | Use the existing process or choose `--port 8520`; the launcher never stops another monitor. |
| Saved examples work, current weather does not | Start `--live` with a CIMIS key and check source state. Preview has no live source. |
| Empty inbox | Confirm and persist a location, then check worker/source state. Startup creates no orchard or subscription. |
| AI/SMS needs setup | Follow its connection screen/runbook. Installation cannot grant consent, verify a phone or supply a sender. |

## Friction found and changes made

| Finding | Result |
| --- | --- |
| README led with ML dependencies, manual `PYTHONPATH` and separate install/build/server steps | Product quickstart first; launcher assembles the existing pieces. |
| Product instructions installed test packages; native editable install adds training dependencies | Launcher uses the minimal pinned product runtime. Scientific workflow stays separate. |
| Frontend package omitted its toolchain contract | pnpm 11.25.0 and minimum Node 22.13 are declared. |
| Credentials appeared necessary to try Terra | Saved-example preview needs none; live weather, AI prose and SMS are explicit additions. |
| Windows service assumes an environment and built client | `--setup-only` prepares both before the optional service helper. |
| Process health could be mistaken for product readiness | Doctor reports setup separately; source coverage, connection consent and delivery require their own evidence. |

## Release alignment

The audit found that the reviewed UI at port 8504 came from `codex/terra-ui-quiet`
(`4464760`), while main contained the newer ChatGPT connection. The subsequent
local-first integration combines the cube logo, leaner copy and phone-readiness
states with the ChatGPT control, trial receipt semantics and replay-only warning.
Use the same committed build for fresh clones and the demonstration. The
separate simulation/conversation feature branch still has its own verification
and integration boundary.

## Vercel

Vercel supports React and Python/FastAPI. Its Services beta combines frontends/backends in one project. Terra has no Vercel configuration or exported root ASGI `app` entrypoint yet. Vite outputs **`web/dist/client`**, not `web/dist`; the dev proxy to `127.0.0.1:8512` is not production routing. See [FastAPI support](https://vercel.com/docs/frameworks/backend/fastapi) and [Services](https://vercel.com/docs/services).

### Saved-example showcase

A frontend-only deployment would use root `web`, install `pnpm install --frozen-lockfile`, build `pnpm build` and publish `dist/client`. The build includes saved assessments. There would be no live API, persisted inbox, plan connection, worker or SMS. The existing UI still exposes controls for those unavailable APIs; a public showcase mode should make that boundary clear. These settings were checked against the local build, not a public deployment.

### Full live product: remaining work

| Boundary | Current implementation | Required adaptation |
| --- | --- | --- |
| Durable state | Farm/outbox, SMS no-resend ledger, source evidence and provider/auth state use SQLite/local files | Shared durable storage adapter and migrations preserving transactions, immutable evidence, leases and deduplication. The native ML PostgreSQL configuration does not migrate product journals automatically. |
| User authorization | Operator controls require loopback Host/Origin and local session token; no hosted tenant ownership layer | Authenticated hosted sessions and ownership checks on reads and writes. Keep local guards for local installs. CORS or accepting a production Host alone is insufficient. |
| Monitoring | Five-minute thread inside a long-lived local ASGI process | Bounded idempotent monitor tick or separate worker with durable shared leases; no dependency on a Function lifespan thread. |
| Provider identity | One protected local ChatGPT connection and one configured SMS recipient/sender | Hosted account/recipient ownership, consent, callbacks, secret protection and connection lifecycle. Do not upload the local vault as a shared hosted identity. |
| Build/API | App factory/CLI; product requirements differ from the root ML pyproject | Hosted ASGI entrypoint, explicit product dependencies, `/api` routing and correct static output. Secrets remain outside browser build variables. |
| Operations | Local heartbeat and retained journals | Deployed readiness, protected scheduling, receipt processing, backoff visibility and restart/concurrency checks. |

Local SQLite cannot provide durable shared writes across Function instances. Moving journals to `/tmp` loses Terra's guarantees. See Vercel's [SQLite explanation](https://vercel.com/kb/guide/is-sqlite-supported-in-vercel).

Vercel **Daemons** are now public beta on all plans and support Python container workers. They can host a continuous loop, but currently have ephemeral storage, manual instance counts and production-only deployments. Durable journals and distributed coordination remain necessary. See [Daemons](https://vercel.com/docs/daemons).

An authenticated scheduled Function could instead run a bounded monitor tick. Hobby Cron runs only daily and cannot replace the five-minute monitor. Pro/Enterprise Cron supports minute intervals. A paid schedule, external scheduler or suitably configured Daemon needs an explicit choice. See [Cron limits](https://vercel.com/docs/cron-jobs/usage-and-pricing).

### Suggested order

1. Verify the local quickstart with labelled examples; configure the real demo's source, location and phone consent separately.
2. Add hosted user ownership and a durable product-store interface, retaining SQLite locally. Prove outbox/receipt behavior across restarts and concurrent workers.
3. Add Vercel static/API configuration and either a scheduled tick or Daemon. Verify preview/production isolation, account connections and redeployment without duplicate notes/texts.

A single always-on host with a persistent volume is closer to the existing full demo runtime. Public access would still require HTTPS and hosted authorization. A Vercel config alone does not supply those application changes.

## Open-source distribution follow-ups

Actual `.env*`, `.terra-local`, SQLite, environments and build/dependency directories are ignored; `.env.example` is only a tracked template. No private configuration is needed for a fresh preview.

Two provenance decisions remain: root `LICENSE` is MIT while native `pyproject.toml` declares BSD-3-Clause; `web/public/fonts/README.md` identifies the exact Grok reference font without a recorded redistribution grant. Confirm component attribution and font redistribution terms, or choose an appropriately licensed replacement. This audit changes neither native metadata nor approved reference typography.

## Verification boundary

The local setup path is checked with a fresh Python environment, frozen frontend install/build, saved-example UI/API, setup diagnostics, occupied-port handling and retained state. Real provider calls, inference, texts, cloud provisioning and deployment are outside this setup test. Cross-platform CI and public deployment verification remain separate evidence from the Windows smoke test.
