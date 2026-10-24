# Terra web client

For the complete local app, run `python run_terra.py` from the repository root.
It builds this client and serves the UI/API together. See the [quickstart](../README.md).

For frontend iteration, use Node.js 24 LTS and pnpm 11.25.0:

```sh
pnpm --dir web install --frozen-lockfile
pnpm --dir web dev --host 127.0.0.1 --port 8510 --strictPort
```

Vite proxies `/api` to `http://127.0.0.1:8512`. Start the root launcher in another
terminal for those endpoints. `TERRA_API_TARGET` can select a different local API
port. The frontend alone cannot provide persistent monitoring or phone delivery.

Production client output is **`web/dist/client`**. The build also prepares existing
Sites-compatible worker metadata under `web/dist`; this is not Vercel API routing
or a hosted monitor. See the [hosting assessment](../docs/terra/setup-and-hosting.md#vercel).

The preview renders strict `terra-location-assessment-v1` packets from
`public/assessments/`. These are real historical inputs with controlled
injections, visibly labelled. **Open JSON** displays another generated packet.
Example selection is not a live provider query.

Python owns QC, interpolation, availability and all classifications. React
formats the supplied facts and exposes exact JSON copy/download. A measured
station's calibrated expectation and Nathan's location interpolation remain
separate. No local observation means no invented local discrepancy.

`pnpm build` and `pnpm test:sites` validate production packaging. Existing
Sites-compatible worker/build inputs are retained, but no site is published.
See [the integration guide](../docs/terra/README.md) and
[checkpoint evidence](../docs/terra/checkpoint.md).

