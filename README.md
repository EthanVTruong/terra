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
JSON, with a saved-data web preview. The existing ChillWatch pipeline remains
unchanged. See [spec.md](spec.md) for the target product and next review boundary.
