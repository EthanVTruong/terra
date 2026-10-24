"""Assess caller-supplied data; no provider calls, training or message delivery."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .assessment import assess_location


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path, help="terra-location-request-v1 JSON")
    parser.add_argument("--output", type=Path, help="Write the exact JSON assessment to this file")
    args = parser.parse_args()
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        if request.get("schema_version") != "terra-location-request-v1":
            raise ValueError("Expected a terra-location-request-v1 request")
        query = dict(request["query"])
        packet = assess_location(
            **query,
            observations=pd.DataFrame(request["observations"]),
            stations=pd.DataFrame(request["stations"]),
            snapshot_id=request["snapshot_id"],
            data_mode=request["data_mode"],
        )
        body = json.dumps(packet, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(body, encoding="utf-8", newline="\n")
        else:
            print(body, end="")
    except (KeyError, TypeError, ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
