"""Orchard estimates only: one explicit chill method and comparable seasons."""
from __future__ import annotations

from calendar import monthrange
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
import math

from .store import utc

PST = timezone(timedelta(hours=-8))
METHOD = "orchard-hours-32-45-nov1-mar31-fixed-pst-v1"
MIN_COVERAGE = 0.95
MIN_REFERENCES = 5


def season_bounds(now):
    local = utc(now).astimezone(PST)
    year = local.year if local.month >= 11 else local.year - 1
    return (datetime(year, 11, 1, tzinfo=PST).astimezone(timezone.utc),
            datetime(year + 1, 4, 1, tzinfo=PST).astimezone(timezone.utc))


def eligible(row):
    value = row.get("temperature_f")
    return row.get("usable") is True and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _count(rows, start, cutoff, geometry_key, method_version):
    expected = max(0, int((cutoff - start).total_seconds() / 3600))
    available, count, comparable, comparison_available, through = 0, 0, 0, 0, None
    comparison_expected = expected
    for year in range(start.year, cutoff.year + 1):
        if monthrange(year, 2)[1] == 29:
            leap_start = datetime(year, 2, 29, tzinfo=PST).astimezone(timezone.utc)
            comparison_expected -= max(0, int((min(cutoff, leap_start + timedelta(days=1)) - max(start, leap_start)).total_seconds() / 3600))
    stamps, values = rows
    for cursor, row in values[bisect_right(stamps, start):bisect_right(stamps, cutoff)]:
        day = (cursor.astimezone(PST) - timedelta(seconds=1)).date()
        leap = day.month == 2 and day.day == 29
        valid = bool(row and eligible(row) and row.get("geometry_key") == geometry_key
                     and row.get("method_version") == method_version)
        if valid:
            available += 1
            chill = 32 <= row["temperature_f"] <= 45
            count += chill
            through = cursor.isoformat()
            if not leap:
                comparable += chill
                comparison_available += 1
    return {"hours": float(count) if available else None, "comparison_hours": float(comparable) if available else None,
            "coverage": available / expected if expected else 0.0, "known_hours": available,
            "expected_hours": expected, "comparison_coverage": comparison_available / comparison_expected if comparison_expected else 0.0,
            "data_through": through}


def quantile(values, fraction):
    values = sorted(values)
    position = (len(values) - 1) * fraction
    lo, hi = math.floor(position), math.ceil(position)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)


def summarize(rows, now, geometry_key, method_version):
    now = utc(now)
    start, end = season_bounds(now)
    cutoff = min(now.replace(minute=0, second=0, microsecond=0), end)
    by_time = {utc(r["valid_utc"]): r for r in rows if utc(r["valid_utc"]) <= now}
    stamps = sorted(by_time)
    index = (stamps, [(stamp, by_time[stamp]) for stamp in stamps])
    current = _count(index, start, cutoff, geometry_key, method_version)
    references = []
    local_end = cutoff.astimezone(PST)
    for year in range(start.year - 10, start.year):
        ref_start = datetime(year, 11, 1, tzinfo=PST).astimezone(timezone.utc)
        ref_year = year + (local_end.month < 11)
        if local_end.month == 2 and local_end.day == 29 and monthrange(ref_year, 2)[1] == 28:
            # Feb29's chill is excluded from every comparison. A non-leap reference
            # must still include all of Feb28, including at midnight into Feb29.
            ref_cutoff = datetime(ref_year, 3, 1, tzinfo=PST).astimezone(timezone.utc)
        else:
            ref_cutoff = local_end.replace(year=ref_year, day=min(local_end.day, monthrange(ref_year, local_end.month)[1])).astimezone(timezone.utc)
        counted = _count(index, ref_start, ref_cutoff, geometry_key, method_version)
        if counted["coverage"] >= MIN_COVERAGE and counted["comparison_coverage"] >= MIN_COVERAGE:
            references.append({"season_start": ref_start.astimezone(PST).date().isoformat(), **counted})
    complete = current["coverage"] >= MIN_COVERAGE and current["comparison_coverage"] >= MIN_COVERAGE
    comparable = complete and len(references) >= MIN_REFERENCES
    p20 = quantile([r["comparison_hours"] for r in references], 0.2) if comparable else None
    median = quantile([r["comparison_hours"] for r in references], 0.5) if comparable else None
    status = "below20" if comparable and current["comparison_hours"] < p20 else "typical" if comparable else "history_insufficient" if complete else "incomplete"
    active = start <= now < end
    return {"method": METHOD, "season_start": start.astimezone(PST).date().isoformat(),
            "cutoff": cutoff.isoformat(), "active_season": active, **current,
            "status": status if active else "offseason", "reference_count": len(references),
            "percentile20_hours": p20, "historical_median_hours": median,
            "references": references, "complete": complete,
            "warning": None if comparable else "Historical pace is unavailable without matching estimates, 95% coverage and at least five prior seasons.",
            "basis": "estimated orchard temperature; FNRIC station totals are separate",
            "estimator_method_version": method_version}


def completed_day_summaries(rows, now, geometry_key, method_version, count=3):
    local = utc(now).astimezone(PST)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    results = []
    for days in reversed(range(count)):
        cutoff = (midnight - timedelta(days=days)).astimezone(timezone.utc)
        result = summarize(rows, cutoff, geometry_key, method_version)
        result["completed_day"] = (cutoff.astimezone(PST).date() - timedelta(days=1)).isoformat()
        results.append(result)
    return results
