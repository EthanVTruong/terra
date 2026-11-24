"""Readers: every input format becomes the common sample frame (``contract.SAMPLE_COLUMNS``).

* ``*.jsonl`` - captures written by the serial bridge: ``{"received_at": ISO-8601, "line": "$CM1,..."}``.
* ``*.csv`` / ``*.json`` - grower DIY uploads (``timestamp, temp, unit, rh``), via the existing
  :func:`chillwatch.sources.diy.parse_upload` so both paths share timestamp/unit handling.

Lines that fail to decode are not dropped silently: they come back in a ``rejects`` frame with
their reason code, and the validation report counts them.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

import pandas as pd

from ..sources.diy import parse_upload, read_upload
from .contract import Issue, coerce_samples, empty_samples, f_to_c
from .wire import WireError, decode

REJECT_COLUMNS = ["received_at", "raw", "issue", "detail"]


def _rejects(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=REJECT_COLUMNS)
    df["received_at"] = pd.to_datetime(df["received_at"], utc=True, errors="coerce")
    return df


def decode_capture(entries: Iterable[dict], source: str = "logger") -> tuple[pd.DataFrame, pd.DataFrame]:
    """``[{"received_at": ..., "line": ...}, ...]`` -> (samples, rejects)."""
    rows, rejected = [], []
    for e in entries:
        line, at = str(e.get("line", "")), e.get("received_at")
        if "$" not in line:  # boot banners and blank lines are not records
            continue
        try:
            rec = decode(line)
        except WireError as err:
            rejected.append({"received_at": at, "raw": line, "issue": str(err.issue), "detail": str(err)})
            continue
        rows.append({
            "source": source, "node_id": rec.node_id, "received_at": at, "seq": rec.seq,
            "uptime_ms": rec.uptime_ms, "temp_c": rec.temp_c, "rh_pct": rec.rh_pct,
            "device_status": rec.device_status, "raw": line,
        })
    samples = coerce_samples(pd.DataFrame(rows)) if rows else empty_samples()
    bad_time = samples["received_at"].isna()
    if bad_time.any():
        for _, r in samples[bad_time].iterrows():
            rejected.append({"received_at": None, "raw": r["raw"], "issue": str(Issue.BAD_FORMAT),
                             "detail": "missing or unparseable received_at"})
        samples = samples[~bad_time].reset_index(drop=True)
    return samples, _rejects(rejected)


def read_capture(path: str | Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    entries = []
    for n, text in enumerate(Path(path).read_text().splitlines(), start=1):
        text = text.strip()
        if not text:
            continue
        try:
            entries.append(json.loads(text))
        except json.JSONDecodeError:
            entries.append({"received_at": None, "line": f"$?line{n}:{text}"})
    return decode_capture(entries)


def read_diy(path: str | Path, node_id: str, tz: str | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """A DIY CSV/JSON log (already timestamped by its logger) as samples."""
    raw = read_upload(Path(path))
    rows, n_rejected = parse_upload(raw, node_id, tz, str(path))
    samples = coerce_samples(pd.DataFrame({
        "source": "diy",
        "node_id": node_id,
        "received_at": rows["ts"],
        "seq": pd.NA,
        "uptime_ms": pd.NA,
        "temp_c": f_to_c(rows["temp_f"].astype("float64")),
        "rh_pct": rows["rh_pct"],
        "device_status": "OK",
        "raw": str(path),
    }))
    rejects = _rejects([{"received_at": None, "raw": str(path), "issue": str(Issue.BAD_FORMAT),
                         "detail": f"{n_rejected} rows without a usable timestamp"}] if n_rejected else [])
    return samples, rejects


def read_any(path: str | Path, node_id: str, tz: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    p = Path(path)
    if p.suffix.lower() == ".jsonl":
        samples, rejects = read_capture(p)
        return samples[samples["node_id"] == str(node_id)].reset_index(drop=True), rejects
    if p.suffix.lower() in (".csv", ".json"):
        return read_diy(p, node_id, tz)
    raise ValueError(f"unsupported input {p.name}: expected .jsonl (serial capture) or .csv/.json (DIY log)")
