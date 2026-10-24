# Terra compatibility evaluation

Scripts and outputs behind `docs/TERRA_COMPATIBILITY.md`. They read the local ChillWatch databases and write nothing to them.

```bash
export CHILLWATCH_DB_URL=postgresql+psycopg://chillwatch:chillwatch@localhost:5432/chillwatch
export CHILLWATCH_ASOS_DB_URL=postgresql+psycopg://chillwatch:chillwatch@localhost:5432/chillwatch_asos
python bench/terra_compat/terra_compat_eval.py  hourly.parquet > eval.json         # Terra stations held out + replay
python bench/terra_compat/terra_compat_extra.py extra.parquet  > extra.json        # orchard path, Terra eligibility, full-network spike
python bench/terra_compat/terra_compat_analyze.py hourly.parquet > common_rows.json
python bench/terra_compat/terra_compat_stats.py hourly.parquet extra.parquet > stats.json   # bootstrap CIs, chill, eligibility
```

Each run takes a few minutes. Outputs here were produced at commit `daa451a` with model `cimis-v1`.
