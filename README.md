# terra
Everything you need to know about your chill data in iMessage

## ChillWatch data / ML layer

Station ingestion (CIMIS, grower DIY uploads, PRISM), physics and LightGBM neighbor model,
station error detection, gap filling and a fault-injection benchmark. See
[docs/DATA_ML_LAYER.md](docs/DATA_ML_LAYER.md) and [docs/DECISIONS.md](docs/DECISIONS.md).

## Terra statistical QC + location JSON

The additive [Terra integration](docs/terra/README.md) joins the frozen statistical
station detector to Nathan's existing temperature/geometry estimator. It returns
location estimates, relevant station flags and measured comparisons as strict
JSON, with a web preview for saved examples and on-demand live queries. The existing ChillWatch pipeline remains
unchanged. See [spec.md](spec.md) for the target product and next review boundary.


## Live location queries

The [local live-query service](docs/terra/live-queries.md) fetches a bounded recent
CIMIS snapshot, applies the same frozen QC and Nathan estimator, and returns
JSON to the web UI. It needs a CIMIS key in the server process environment.
The browser sends coordinates and optional station identity; it cannot supply
observations, clocks, data mode or credentials.

```powershell
python -m pip install -r requirements-terra-dev.txt
$env:PYTHONPATH = "backend"
$env:CIMIS_APP_KEY = "<your CIMIS AppKey>"
python -m terra_monitoring.server --host 127.0.0.1 --port 8512
```

After building the web preview, open <http://127.0.0.1:8512>. For frontend
iteration, Vite proxies `/api` to that same local service. Follow the linked
runbook for the build, development commands, cache/freshness behavior and tests.

Queries are on demand, with a 120-second response-cache window. This is not a
continuous monitoring worker or deployed service. Real current
weather, retained cached data and controlled examples remain explicitly labelled.

## Phone alerts with Twilio

The optional [Twilio integration](docs/terra/twilio-sms.md) delivers selected,
persisted orchard notifications to one configured phone through a shared transport. The
**Phone alerts** panel can also run a controlled temperature spike through the
frozen detector and send its clearly labelled demo text. Saved examples and
uploaded JSON and ordinary location queries never send messages. SMS stays disabled until explicitly configured.
Delivery records survive restarts; uncertain sends are withheld from automatic
resending. Real phone delivery requires a working Twilio account and approved sender.
