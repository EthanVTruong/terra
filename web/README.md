# Terra location-assessment preview

Run `pnpm install --frozen-lockfile`, then
`pnpm dev --host 127.0.0.1 --port 8510 --strictPort` from this directory.
The authoritative lockfile is `pnpm-lock.yaml`.

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

