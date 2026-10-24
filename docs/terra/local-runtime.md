# Local monitor runtime

One process serves the built interface and API on loopback port 8512 and runs
the existing farm monitor every five minutes. Close the browser to leave it
working; keep the computer awake. This is a local service, not a hosted service.

Install `requirements-terra.txt` into `.venv`, then build the client:

```powershell
pnpm --dir web install --frozen-lockfile
pnpm --dir web build
```

Launch from the repository on Windows, supplying private configuration files
explicitly. Those files are ignored by Git. Existing process variables take
precedence over the CIMIS file; the SMS loader accepts only its known keys.

```powershell
./scripts/terra-service/start.ps1 -EnvFile C:/private/cimis.env `
  -SmsEnvFile C:/private/twilio.env
```

The helper starts a hidden process, checks `/api/health`, and records its PID and
logs in `.terra-local/runtime/`. Optional `-PythonExe`, `-StateDir`, `-Port`,
`-Interval` and `-NoWorker` parameters support an existing environment or a
separate smoke-test instance. A port already in use is an error: avoid duplicate
monitors. An occupied port or failed health check does not delete state.

For a foreground process on any supported platform:

```powershell
$env:PYTHONPATH = 'backend'
./.venv/Scripts/python.exe -m terra_monitoring.runtime --env-file C:/private/cimis.env `
  --sms-env-file C:/private/twilio.env --state-dir C:/private/terra-state
```

Use the environment's Python executable and absolute state paths for your OS.
The CLI defaults to loopback only. `--no-worker` serves the UI/API without
background polling. Importing the module or using its app factory does not poll.

The state directory contains independent farm, transport and source journals.
Retain it across restarts and branch changes. Startup creates neither an orchard
nor a subscription. Explicitly create a location in the interface; the initial
West Orchard location is a clearly labelled demonstration pin, not a known farm.
SMS remains disabled unless its private configuration and orchard consent both
permit it. Opening the interface or querying a location never sends an SMS.

`GET /api/monitor/status` exposes the persisted worker heartbeat. Messages are
published in the local inbox before transport; provider acceptance and delivered
receipts are distinct. The worker drains eligible pending messages and polls
accepted-message receipts even while the interface is closed. Source outages
withhold numerical conclusions and retain their evidence.

For foreground shutdown use Ctrl+C, which lets the worker finish an in-flight
operation within its bounded timeout. On Windows the helper below stops only the
recorded matching runtime and retains its journals:

```powershell
./scripts/terra-service/stop.ps1
```

This helper performs process termination. An interrupted provider operation may
remain uncertain; the durable ledger prevents a blind resend. Check delivery
Details after restart rather than assuming that an interrupted request failed.

Real custom-message delivery additionally needs a verified intended recipient,
an eligible sender/account, authorized charges where applicable and valid private
credentials. A mock receipt or labelled demo is not evidence of phone delivery.
See [Twilio setup](twilio-sms.md) and [readiness](readiness.md).
