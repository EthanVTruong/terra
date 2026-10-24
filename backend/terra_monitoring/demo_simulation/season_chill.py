"""Fetch each Station lab station's season chill hours up to the replay, once, into a pinned file.

Run explicitly with a private key file; the key is read, never written:
    python -m terra_monitoring.demo_simulation.season_chill --key-file C:/private/cimis.env

Chill hours follow the orchard method: hours from 32 to 45 F inclusive since Nov 1,
fixed PST hour-ending days. Only daily counts and provenance are saved, so the
replay can start from the real season-to-date total instead of zero.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

import pandas as pd

from chillwatch.sources.cimis import CimisSource

from .engine import DATA, ROOT, WARMUP

SEASON_START = date(2025, 11, 1)
OUTPUT = DATA / "season-chill-2025-26.json"
STATIONS = (6, 139, 212, 226, 235)
PST = timezone(timedelta(hours=-8))
UNUSABLE_QC = {"M", "N", "R", "S"}  # CIMIS missing, not collected, far out of range, sensor issue


def read_key(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip()
    for line in text.splitlines():
        name, _, value = line.partition("=")
        if value and name.strip() in {"CIMIS_APP_KEY", "CIMIS_API_KEY"}:
            return value.strip().strip('"').strip("'")
    if "=" not in text and text:
        return text
    raise ValueError("The key file needs CIMIS_APP_KEY=<key> or just the key")


def season_counts(frame: pd.DataFrame, cutoff: pd.Timestamp) -> dict:
    """Daily chill-hour counts before ``cutoff`` (the replay's first hour), PST hour-ending days."""
    ts = pd.to_datetime(frame["ts"], utc=True)
    keep = ts < cutoff
    frame, ts = frame[keep], ts[keep]
    qc = frame["qc_source"].fillna("").astype(str).str.strip().str.upper()
    usable = frame["temp_f"].notna() & ~qc.isin(UNUSABLE_QC)
    chill = usable & frame["temp_f"].between(32, 45)
    days = (ts - pd.Timedelta(hours=1)).dt.tz_convert(PST).dt.date.astype(str)
    daily = chill.groupby(days).sum().astype(int)
    known = usable.groupby(days).sum().astype(int)
    return {"daily_chill_hours": daily.to_dict(), "daily_usable_hours": known.to_dict(),
            "chill_hours": int(chill.sum()), "usable_hours": int(usable.sum()), "hours": int(len(frame))}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, default=ROOT / ".terra-local" / "cimis-cache")
    args = parser.parse_args(argv)
    cutoff = pd.Timestamp(WARMUP)
    end = cutoff.tz_convert(PST).date()
    source = CimisSource(app_key=read_key(args.key_file), cache_dir=args.cache_dir)
    stations = {}
    for station in STATIONS:
        frame = source.fetch_hourly(str(station), SEASON_START, end)
        stations[str(station)] = season_counts(frame, cutoff)
        print(f"station {station}: {stations[str(station)]['chill_hours']} chill hours "
              f"from {stations[str(station)]['usable_hours']} usable hours")
    result = {"schema_version": "terra-demo-season-chill-v1", "source": "CIMIS hourly air temperature",
              "api": source.api, "method": "hours 32-45 F inclusive since Nov 1, fixed PST hour-ending days",
              "season_start": SEASON_START.isoformat(), "through_exclusive_utc": cutoff.isoformat(),
              "excluded_qc_flags": sorted(UNUSABLE_QC), "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
              "stations": stations}
    OUTPUT.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
