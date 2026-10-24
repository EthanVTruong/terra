# Terra compatibility evaluation

Scripts and outputs behind `docs/TERRA_COMPATIBILITY.md`. They read the local ChillWatch databases and write nothing to them.

```bash
export CHILLWATCH_DB_URL=postgresql+psycopg://chillwatch:chillwatch@localhost:5432/chillwatch
export CHILLWATCH_ASOS_DB_URL=postgresql+psycopg://chillwatch:chillwatch@localhost:5432/chillwatch_asos
python bench/terra_compat/terra_compat_eval.py  hourly.parquet > eval.json         # Terra stations held out + replay
python bench/terra_compat/terra_compat_extra.py extra.parquet  > extra.json        # orchard path, Terra eligibility, full-network spike
python bench/terra_compat/terra_compat_analyze.py hourly.parquet > common_rows.json
python bench/terra_compat/terra_compat_stats.py hourly.parquet extra.parquet > stats.json   # bootstrap CIs, chill, eligibility
python bench/terra_compat/terra_compat_checks.py > checks.json                     # scramble (leakage) test, Y-flag check
python bench/terra_compat/terra_compat_estimate_check.py extra.parquet > estimate_check.json   # estimate_location vs the evaluated method
python bench/terra_compat/terra_compat_intervals.py > intervals.json               # p10-p90 coverage, summer accuracy
```

Each run takes a few minutes.
- `eval`, `extra`, `common_rows`, `stats` and `checks` were produced at commit `daa451a` with model `cimis-v1` and the Oct–Mar physics fit.
- `estimate_check.json` was produced at `edf361d`. Its estimate runs with the lapse off, so the physics fit does not enter.
- `intervals.json` was produced at `b0e8e91`, with the year-round physics fit and its p10/p90 bins (`data/models/physics_fit.json`, SHA-256 `37633cc9…`).
