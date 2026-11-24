"""One call from raw logger input to a validated, scored hourly series and a chill summary.

    samples ->  rules (sample checks, stream events)
            ->  hourly (canonical hour-ending rows, coverage, gaps)
            ->  fusion (physics + ML expected value, weighted by uncertainty; flags; gap estimates)
            ->  chill (chill hours + Dynamic Model with observed / estimated / range)

Every stage is a pure function on data frames; this module only wires them together and
collects the report packet. With no reference stations in the context the fusion step is
skipped and the report says so: rules and coverage still apply, but nothing is estimated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .contract import Node
from .fusion import Expectation, calibration_summary, reference_expectation, score
from .hourly import gaps, samples_to_hourly
from .rules import StreamEvents, validate_samples

PACKET_VERSION = "chill-monitor-v1"


@dataclass
class Result:
    node: Node
    samples: pd.DataFrame        # validated samples (status, issues)
    rejects: pd.DataFrame        # lines that never decoded
    events: StreamEvents
    hourly: pd.DataFrame         # scored hourly rows (canonical + fusion columns)
    expectation: Expectation | None
    chill: dict[str, Any]

    def packet(self) -> dict[str, Any]:
        h = self.hourly
        counts = h["qc_source"].value_counts().to_dict() if len(h) else {}
        sample_issues = _count(self.samples.get("issues", []))
        for i in self.rejects.get("issue", []):
            sample_issues[i] = sample_issues.get(i, 0) + 1
        hour_issues = _count(h.get("issues", []))
        exp = self.expectation
        return {
            "packet": PACKET_VERSION,
            "node": {"node_id": self.node.node_id, "name": self.node.name, "lat": self.node.lat,
                     "lon": self.node.lon, "elevation_ft": self.node.elevation_ft, "sensor": self.node.sensor,
                     "shield": self.node.shield, "calibration_offset_c": self.node.calibration_offset_c},
            "samples": {"received": int(len(self.samples) + len(self.rejects)), "decoded": int(len(self.samples)),
                        "rejected": int(len(self.rejects)),
                        "by_status": self.samples["status"].value_counts().to_dict() if len(self.samples) else {}},
            "stream": self.events.to_dict(),
            "hours": {"total": int(len(h)), "by_status": {k: int(v) for k, v in counts.items()},
                      "gaps": gaps(h) if len(h) else []},
            # samples and hours are counted separately: an hour carries the issues of its samples
            "issues": {"samples": dict(sorted(sample_issues.items())), "hours": dict(sorted(hour_issues.items()))},
            "reference": None if exp is None else {
                "ml_available": exp.ml_available,
                "model_version": exp.model_version,
                "method": "physics+ml fused (minimum variance)" if exp.ml_available else "physics only",
                "agreement": calibration_summary(h),
            },
            "chill": self.chill,
        }


def _count(values: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        for i in filter(None, str(v).split(",")):
            out[i] = out.get(i, 0) + 1
    return out


def run(samples: pd.DataFrame, rejects: pd.DataFrame, node: Node, ctx: Any | None, start: Any, end: Any,
        as_of: Any = None, target_portions: float | None = None, target_hours: float | None = None) -> Result:
    from ..chill.season import summarize  # chill.season reads interop outputs; import here, not at load

    if node.calibration_offset_c and len(samples):
        samples = samples.assign(temp_c=samples["temp_c"] - float(node.calibration_offset_c))
    validated, events = validate_samples(samples, node)
    hourly = samples_to_hourly(validated, node, start, end)
    exp = None
    if ctx is not None and len(ctx.region_stations()):
        exp = reference_expectation(ctx, node, hourly["ts"].min(), hourly["ts"].max())
        scored = score(hourly, exp, node)
    else:
        scored = hourly.copy()
        ok = scored["qc_source"] == "ok"
        scored["used_temp_f"] = np.where(ok, scored["temp_f"], np.nan)
        scored["is_estimated"] = False
        scored["est_sd"] = np.where(ok, node.sensor_sd_f, np.nan)
    chill = summarize(scored, hourly["ts"].min(), hourly["ts"].max(), as_of=as_of,
                      target_portions=target_portions, target_hours=target_hours)
    return Result(node=node, samples=validated, rejects=rejects, events=events, hourly=scored,
                  expectation=exp, chill=chill)
