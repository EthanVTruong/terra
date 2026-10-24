"""One season of `chillwatch bench` with a chosen model (same faults and seeds as the two-season run).

usage: CHILLWATCH_CONFIG_DIR=<config dir> python bench/experiments/bench_season.py SEASON INDEX MODEL OUT_DIR

INDEX is the season's position in the two-season run (2023-24 -> 0, 2024-25 -> 1, 2025-26 -> 2); the
fault seed is 42 + INDEX and 50 faults are injected, which reproduces that run's faults per season.
Per-season runs keep each process under the 2-hour limit of the sessions these were run in.
"""

import json
import sys
from pathlib import Path

from chillwatch import config
from chillwatch.bench.run import run_bench
from chillwatch.context import Context, get_context
from chillwatch.qc.model import ModelBundle

season, si, model, out = sys.argv[1], int(sys.argv[2]), sys.argv[3], Path(sys.argv[4])
base = get_context()
ctx = Context(base.store, ModelBundle.load(config.data_dir() / "models" / model))
out.mkdir(parents=True, exist_ok=True)
r = run_bench(50, 42 + si, ctx=ctx, seasons=[season], gapfill_blocks=100, out_dir=out)
print(json.dumps({"paths": r["paths"]}))
