"""Wire format v1: the text line the Arduino logger prints for every sample.

    $CM1,<node_id>,<seq>,<uptime_ms>,<temp_c>,<rh_pct>,<status>,<chill_h>*<CS>

* ``$`` starts a record; anything before it on the line (boot messages, noise) is ignored.
* ``temp_c`` / ``rh_pct`` have one decimal and are empty when the sensor read failed.
* ``status`` is ``OK`` or an error code (``ERR_READ`` when the DHT22 did not answer).
* ``chill_h`` is the device's own running chill-hour count shown on the LCD. It is advisory;
  the backend recomputes chill from validated hourly data.
* ``CS`` is two upper-case hex digits: XOR of every character between ``$`` and ``*``
  (the NMEA 0183 scheme, cheap to compute on the Uno).

Example: ``$CM1,orchard-1,42,2520000,4.6,81.2,OK,12.0*7D``
"""

from __future__ import annotations

from .contract import WIRE_VERSION, DeviceRecord, Issue


class WireError(ValueError):
    def __init__(self, issue: Issue, message: str) -> None:
        super().__init__(message)
        self.issue = issue


def checksum(body: str) -> str:
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return f"{cs:02X}"


def _num(text: str) -> float:
    return float(text) if text.strip() != "" else float("nan")


def encode(rec: DeviceRecord) -> str:
    """The line a device would send (used by tests, simulators and the replay tool)."""
    def fmt(x: float) -> str:
        return "" if x != x else f"{x:.1f}"  # NaN -> empty field

    chill = "" if rec.device_chill_hours is None else f"{rec.device_chill_hours:.1f}"
    body = ",".join([WIRE_VERSION, rec.node_id, str(rec.seq), str(rec.uptime_ms), fmt(rec.temp_c),
                     fmt(rec.rh_pct), rec.device_status, chill])
    return f"${body}*{checksum(body)}"


def decode(line: str) -> DeviceRecord:
    """Parse one line; raises :class:`WireError` with the reason code on any problem."""
    start = line.find("$")
    if start < 0 or "*" not in line[start:]:
        raise WireError(Issue.BAD_FORMAT, "no $...* record on the line")
    body, _, tail = line[start + 1:].partition("*")
    sent = tail.strip()[:2].upper()
    if len(sent) != 2 or sent != checksum(body):
        raise WireError(Issue.BAD_CHECKSUM, f"checksum {sent!r} != {checksum(body)!r}")
    parts = body.split(",")
    if parts[0] != WIRE_VERSION:
        raise WireError(Issue.WRONG_VERSION, f"wire version {parts[0]!r}, expected {WIRE_VERSION}")
    if len(parts) != 8:
        raise WireError(Issue.BAD_FORMAT, f"expected 8 fields, got {len(parts)}")
    _, node, seq, uptime, temp, rh, status, chill = parts
    try:
        return DeviceRecord(
            node_id=node.strip(),
            seq=int(seq),
            uptime_ms=int(uptime),
            temp_c=_num(temp),
            rh_pct=_num(rh),
            device_status=status.strip() or "OK",
            device_chill_hours=float(chill) if chill.strip() else None,
        )
    except ValueError as e:
        raise WireError(Issue.BAD_FORMAT, f"unparseable field: {e}") from e
