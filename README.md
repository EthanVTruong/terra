# Terra

**A local-first assistant for orchard weather, winter chill, and the data behind them.**

Tell Terra what you grow and where. It checks nearby weather stations, estimates temperature at your orchard, and keeps a quiet inbox of meaningful updates. Ask follow-up questions, inspect the evidence, or receive selected notes by SMS.

## What it does

- **Checks station data:** compares temperatures with nearby peers and excludes suspect readings from the orchard estimate. Humidity, wind and solar checks keep numerical discrepancies separate from missing or source-warned data. [Weather rules](docs/terra/weather-contract.md).
- **Tracks winter chill:** separates estimated orchard chill from provider-reported totals. Missing hours stay unknown; historical comparisons need sufficient comparable data.
- **Explains meaningful changes:** saves monitoring and recovery notes with their original evidence. A station flag does not automatically become an orchard alert.
- **Answers in plain language:** uses saved farm and weather facts, with optional ChatGPT writing and factual local fallback.

**CIMIS observations → station QC → location estimate → chill and coverage monitoring → saved notes → optional notifications.** Python computes the facts; the language model explains them.

## Run locally

Requires **Python 3.12**, **Node.js 24 LTS**, and **pnpm 11.25.0** (`npm install --global pnpm@11.25.0`).

```sh
git clone https://github.com/EthanVTruong/terra.git
cd terra
python run_terra.py
```

Open **http://127.0.0.1:8512**. Use `python3` if needed. Stop with Ctrl+C.

The launcher creates `.venv`, installs pinned dependencies, builds the interface, and serves the UI and API. First setup needs internet; no API key, database server, or model training is needed.

**Default preview:** saved historical assessments and controlled examples, with live weather, the worker, ChatGPT, and SMS disabled. State stays in `.terra-local/preview`.

Enter your crops, select **Use my location**, and approve the browser request. Terra looks up ground elevation and selects nearby eligible stations. Choose notifications separately.

Troubleshoot with `python run_terra.py --doctor`; choose another port with `--port 8520`.

## Connect live weather

Put `CIMIS_APP_KEY=<your key>` in a private environment file outside the repository:

```sh
python run_terra.py --live --env-file /absolute/path/to/cimis.env
```

Windows paths such as `C:/private/cimis.env` also work. Set up your crops and farm location in the app. Live monitoring checks every five minutes. Preserve `.terra-local/runtime` across restarts and keep the process and computer running.

- **ChatGPT:** connect your eligible plan and select a model in the app. [Connection guide](docs/terra/chatgpt-plan.md).
- **Notifications:** browser alerts work while Terra is open. SMS requires Twilio setup and orchard opt-in; add `--sms-env-file /absolute/path/to/twilio.env`. Trial-template mode sends a generic placeholder. [Phone setup](docs/terra/twilio-sms.md).
- **Operations:** [state, background operation, and shutdown](docs/terra/local-runtime.md). Add `--no-worker` in live mode for on-demand queries only.

## Demo

The [station lab and grower demo](docs/terra/demo-simulation.md) replay real **March 2026 CIMIS observations**, after the detector's 2025 fitting and calibration periods. Controls change copies of readings; the actual detector, estimator, and monitor calculate the outcome.

Try **normal readings → station spike → loss of station support → recovery after three subsequent usable hours**. The [production-interface replay](docs/terra/full-flow-verification/replay.md) includes actual onboarding, chat, and retained Details. Both demos use isolated state and simulated phone notifications.

## Developers and agents

Read [AGENTS.md](AGENTS.md), run `python terra.py brief`, and record your owner/scope in [COORDINATION.md](COORDINATION.md) before editing on an isolated task branch. [CLAUDE.md](CLAUDE.md) imports the same rules.

Activate the launcher's `.venv`, then:

```sh
python -m pip install -r requirements-terra-dev.txt
python terra.py check --base origin/main --task <task-id>
pnpm --dir web test:ui
pnpm --dir web test:sites
```

| Area | Entry point |
| --- | --- |
| Interface | `web/` · [frontend development](web/README.md) |
| Chat, monitoring, storage, delivery | `backend/terra_monitoring/` · [grower workflow](docs/terra/tara-conversation.md) · [chill rules](docs/terra/farm-chill-ops.md) |
| Frozen station QC | `backend/terra_qc/`, `config/terra/` · [reference provenance](docs/terra/checkpoint.md) |
| Ingestion, estimation, scientific tools | `backend/chillwatch/` · [ChillWatch guide](docs/DATA_ML_LAYER.md) |

The separate native training/repair tools need additional dependencies. [CI](.github/workflows/terra.yml) covers the full backend, PostgreSQL, portable product checks, and web builds.

Terra is currently local and single-user. Orchard temperatures are estimates; statistical flags are not confirmed sensor failures or crop-risk diagnoses. Controlled demos do not establish natural-fault accuracy.
