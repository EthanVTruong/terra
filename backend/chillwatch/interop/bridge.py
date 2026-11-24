"""Serial bridge: stamp each line from the Uno with the host's UTC clock and append it to JSONL.

The Uno has no real-time clock, so the computer it is plugged into supplies the timestamp.
The raw line is stored untouched; decoding and validation happen later, so a bad line is
never lost and the capture can be re-validated with new rules.

    chillwatch logger bridge --port /dev/ttyACM0 --out data/logger/orchard-1.jsonl

Needs ``pyserial`` (``pip install pyserial``) only for live capture; everything else in the
package works without it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path


def stamp_lines(lines: Iterable[str], clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> Iterator[dict]:
    for line in lines:
        text = line.strip()
        if text:
            yield {"received_at": clock().isoformat(), "line": text}


def append_jsonl(entries: Iterable[dict], out: str | Path) -> int:
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("a", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")
            fh.flush()
            n += 1
    return n


def serial_lines(port: str, baud: int = 9600) -> Iterator[str]:  # pragma: no cover - needs hardware
    try:
        import serial  # type: ignore[import-not-found]
    except ImportError as e:
        raise SystemExit("live capture needs pyserial: pip install pyserial") from e
    with serial.Serial(port, baud, timeout=5) as ser:
        while True:
            raw = ser.readline()
            if raw:
                yield raw.decode("ascii", errors="replace")


def run_bridge(port: str, out: str | Path, baud: int = 9600) -> int:  # pragma: no cover - needs hardware
    return append_jsonl(stamp_lines(serial_lines(port, baud)), out)
