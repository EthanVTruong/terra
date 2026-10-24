# terra
Local-first orchard chill and weather updates in a compact inbox, with optional ChatGPT message writing and SMS.

## Run locally

Install **Python 3.12**, **Node.js 24 LTS** and **pnpm 11.25.0**. Python 3.12 is the CI baseline; the launcher accepts newer Python and Node.js 22.13 or newer. If pnpm is missing, run `npm install --global pnpm@11.25.0` after installing Node.js.

```sh
git clone https://github.com/EthanVTruong/terra.git
cd terra
python run_terra.py
```

Use `python3` if that is your platform's Python command. Visit **http://127.0.0.1:8512** once the server starts. Stop with Ctrl+C.

The launcher creates `.venv`, installs the pinned product runtime, builds the web client and serves the UI and API together. No shell activation, database server or API key is needed for this preview. The first setup needs internet access to download dependencies; later runs reuse matching dependencies and an unchanged build.

The preview shows **saved historical assessments and controlled examples**, not current weather. It disables weather credentials, ChatGPT and SMS, skips the background worker, and uses separate state in `.terra-local/preview`. These examples exercise the frozen QC reference and existing temperature estimator; launching Terra does not train a model.

Terra opens with Tara's grower conversation: enter your crops, explicitly confirm your farm location and elevation, then choose browser notifications or in-chat updates. Farm questions use saved facts and retained note evidence; ChatGPT writing is optional. Reopening the conversation preserves its history and stays quiet. See [the conversation guide](docs/terra/tara-conversation.md).

```sh
python run_terra.py --doctor
python run_terra.py --setup-only
```

`--doctor` reports missing tools, dependencies, stale builds and occupied ports without installing or checking provider credentials. If port 8512 is busy, use `--port 8520`.

## Connect current weather

Create a private environment file outside the repository containing `CIMIS_APP_KEY=<your key>`, then run:

```sh
python run_terra.py --live --env-file /absolute/path/to/cimis.env
```

On Windows, a path such as `C:/private/cimis.env` works. Existing process variables take precedence. Omit `--env-file` if the key is already in the process environment. Confirm your actual location in Terra; saved station reference locations are examples to confirm or adjust.

Live mode enables the five-minute monitor and retains journals in `.terra-local/runtime`. Keep that directory across restarts. Leave the process and computer running to continue monitoring with the browser closed. Add `--no-worker` for on-demand queries only. A running server alone does not prove weather coverage or phone readiness.

- **Message writing:** explicitly sign in to an eligible ChatGPT plan and choose a returned model. Without a usable connection, notes use a factual template. See [ChatGPT connection](docs/terra/chatgpt-plan.md).
- **Phone alerts:** add `--sms-env-file /absolute/path/to/twilio.env` in live mode, complete sender/verified-recipient setup and opt in for the orchard. SMS stays disabled until explicitly configured. See [Twilio setup](docs/terra/twilio-sms.md).
- **Operator controls:** the [local runtime guide](docs/terra/local-runtime.md) covers private state paths, Windows background service helpers and shutdown.

## Local-first by design

Terra is an **open-source, local single-user application**. The interface, API and monitor run on your computer; private account connections and retained journals stay in your own state directory. A Terra cloud account or Vercel deployment is unnecessary. Keep the process running and computer awake for background monitoring.

The current product direction is to make this local experience complete and easy to run. Hosted migration is deferred; the [setup assessment](docs/terra/setup-and-hosting.md) retains the findings for future reference. The launcher creates no cloud resources or public deployment.

## Development

For frontend iteration, see [web/README.md](web/README.md). The launcher uses `requirements-terra.txt`; test tooling is in `requirements-terra-dev.txt`. The broader native scientific workflow below has separate heavier dependencies in `pyproject.toml` and is not required for the product preview.

## Controlled station and grower demonstration

The [station lab and grower demo](docs/terra/demo-simulation.md) replay real March 2026 observations from after detector fitting and calibration. Manual controls change a copy of the readings; the frozen detector, existing estimator and orchard monitor calculate the outcome. A suspect station can be excluded while enough peers still support the orchard; insufficient support produces the grower note. Advancing through three subsequent usable hours demonstrates recovery.

The [production-interface replay guide](docs/terra/full-flow-verification/replay.md) runs the actual Tara onboarding, inbox, Details and follow-up flow against the same controlled weather in a separate local process. Both demonstration entry points are included in this repository. Their phone cards are simulated; configured Twilio delivery and its persisted receipt remain separate.

## ChillWatch data / ML layer

Station ingestion (CIMIS, grower DIY uploads, PRISM), physics and LightGBM neighbor model,
station error detection, gap filling and a fault-injection benchmark. See
[docs/DATA_ML_LAYER.md](docs/DATA_ML_LAYER.md) and [docs/DECISIONS.md](docs/DECISIONS.md).

## Terra statistical QC + location JSON

The additive [Terra integration](docs/terra/README.md) joins the frozen statistical
station detector to Nathan's existing temperature/geometry estimator. It returns
location estimates, relevant station flags and measured comparisons as strict
JSON, with a web preview for saved examples and on-demand live queries. The existing
ChillWatch numerical pipeline remains unchanged; audited input guards are described
in [backend cleanup](docs/terra/backend-cleanup.md). See [spec.md](spec.md) for the
target product and next review boundary.


## Live location queries

The [local live-query service](docs/terra/live-queries.md) fetches a bounded recent
CIMIS snapshot, applies the same frozen QC and Nathan estimator, and returns
JSON to the web UI. It needs a CIMIS key in the server process environment.
The browser sends coordinates and optional station identity; it cannot supply
observations, clocks, data mode or credentials.

Queries are on demand, with a 120-second response-cache window. Use the combined
runtime below for persistent monitoring. Real current weather, retained cached
data and controlled examples remain explicitly labelled.

## Persistent orchard inbox

The [combined local runtime](docs/terra/local-runtime.md) serves the interface and
API and runs one durable orchard monitor. It stores observations, meaningful
condition changes and concise grower notes; optionally it dispatches eligible
notes through Twilio and reconciles receipts with the browser closed. Confirm a
farm profile explicitly and retain the state directory across restarts. Local inbox publication, provider
acceptance and phone delivery remain separate states.

The optional [ChatGPT plan connection](docs/terra/chatgpt-plan.md) writes notes
from trusted event JSON using your eligible plan. Open **Message writing**, sign
in and choose an available model. Coverage updates use a brief texting voice;
measurements and technical recovery counts stay in Details. Failed or unavailable
generation uses a factual template, and published notes are never rewritten.

## Phone alerts with Twilio

The optional [Twilio integration](docs/terra/twilio-sms.md) delivers selected,
persisted orchard notifications to one configured phone through a shared transport. The
**Phone alerts** panel can also run a controlled temperature spike through the
frozen detector and send its clearly labelled demo text. Saved examples and
uploaded JSON and ordinary location queries never send messages. SMS stays disabled until explicitly configured.
Delivery records survive restarts; uncertain sends are withheld from automatic
resending. Real phone delivery requires a working Twilio account and approved sender.
