import math

import pytest

from chillwatch.interop import DeviceRecord, Issue, WireError, decode, encode
from chillwatch.interop.bridge import append_jsonl, stamp_lines
from chillwatch.interop.readers import decode_capture, read_capture
from chillwatch.interop.wire import checksum

REC = DeviceRecord("orchard-1", 42, 2520000, 4.6, 81.2, "OK", 12.0)


def test_round_trip_and_documented_example():
    line = encode(REC)
    assert line == "$CM1,orchard-1,42,2520000,4.6,81.2,OK,12.0*7D"   # the example in wire.py
    assert decode(line) == REC
    assert decode("boot noise " + line + "\r\n") == REC


def test_failed_read_has_empty_fields():
    rec = DeviceRecord("orchard-1", 7, 1000, math.nan, math.nan, "ERR_READ", None)
    line = encode(rec)
    assert ",,,ERR_READ," in line
    out = decode(line)
    assert math.isnan(out.temp_c) and out.device_status == "ERR_READ" and out.device_chill_hours is None


@pytest.mark.parametrize("bad, issue", [
    ("$CM1,orchard-1,42,2520000,4.6,81.2,0K,12.0*7D", Issue.BAD_CHECKSUM),
    ("$CM1,orchard-1,42,2520000,4.6,81.2,OK,12.0", Issue.BAD_FORMAT),
    ("$CM1,orchard-1,42,x,4.6,81.2,OK,12.0*" + checksum("CM1,orchard-1,42,x,4.6,81.2,OK,12.0"), Issue.BAD_FORMAT),
    ("$CM2,orchard-1,42,2520000,4.6,81.2,OK,12.0*" + checksum("CM2,orchard-1,42,2520000,4.6,81.2,OK,12.0"),
     Issue.WRONG_VERSION),
])
def test_rejects_carry_an_issue(bad, issue):
    with pytest.raises(WireError) as e:
        decode(bad)
    assert e.value.issue == issue


def test_bridge_capture_round_trip(tmp_path):
    from datetime import UTC, datetime, timedelta

    t0 = datetime(2026, 12, 1, 8, tzinfo=UTC)
    ticks = iter(t0 + timedelta(minutes=i) for i in range(10))
    lines = ["# chill_logger wire CM1 orchard-1", encode(REC), encode(REC).replace(",OK,", ",0K,")]
    out = tmp_path / "cap.jsonl"
    assert append_jsonl(stamp_lines(lines, clock=lambda: next(ticks)), out) == 3
    samples, rejects = read_capture(out)
    assert len(samples) == 1 and samples["temp_c"].iloc[0] == 4.6
    assert str(samples["received_at"].dt.tz) == "UTC"
    assert list(rejects["issue"]) == [Issue.BAD_CHECKSUM]


def test_decode_capture_skips_comment_lines():
    s, r = decode_capture([{"received_at": "2026-12-01T08:00:00+00:00", "line": "# hello"}])
    assert s.empty and r.empty
