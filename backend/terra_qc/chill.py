"""Causal seasonal summaries of FNRIC daily chill *increments*.

Dates use a conservative fixed UTC-08:00 pending source timestamp verification.
Only days ending before ``as_of`` are eligible. ``total`` retains all reported
chill; ``comparison_total`` and reference totals omit February 29 so percentile
comparisons give every season the same calendar-day opportunity. ``coverage``
uses all completed calendar dates, including February 29. ``comparison_coverage``
uses non-leap dates only. Both treat missing or invalid daily records as 0 hours.
Latest input occurrence wins for corrections, including invalid corrections.
Source flags and Terra suspect dates warn without deleting reported totals.
"""

from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from math import isfinite

PST = timezone(timedelta(hours=-8))
COVERAGE_MIN = 0.95
REFERENCE_MIN = 5


def _dates(start, end):
    while start <= end:
        yield start
        start += timedelta(days=1)


def _valid(record):
    value, hours = record.get("hours_32_45"), record.get("n_hours")
    return (
        isinstance(value, (int, float)) and not isinstance(value, bool)
        and isfinite(value) and value >= 0
        and isinstance(hours, int) and not isinstance(hours, bool)
        and 0 <= hours <= 24 and value <= hours
    )


def _percentile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _sum_season(index, start, cutoff):
    days = list(_dates(start, cutoff))
    valid = [index[day] for day in days if day in index and _valid(index[day])]
    comparison_days = [day for day in days if (day.month, day.day) != (2, 29)]
    comparison_valid = [index[day] for day in comparison_days if day in index and _valid(index[day])]
    expected_hours = len(days) * 24
    reported_hours = sum(record["n_hours"] for record in valid)
    comparison_expected_hours = len(comparison_days) * 24
    comparison_reported_hours = sum(record["n_hours"] for record in comparison_valid)
    return {
        "total": sum(record["hours_32_45"] for record in comparison_valid),
        "coverage": reported_hours / expected_hours if expected_hours else 0.0,
        "expected_hours": expected_hours,
        "reported_hours": reported_hours,
        "comparison_coverage": comparison_reported_hours / comparison_expected_hours if comparison_expected_hours else 0.0,
        "comparison_expected_hours": comparison_expected_hours,
        "comparison_reported_hours": comparison_reported_hours,
    }


def summarize_chill(records: list[dict], station_id: int, as_of: datetime,
                    unreliable_dates: set[str] | None = None) -> dict:
    """Return totals and a withheld/20th/80th percentile seasonal conclusion.

    ``coverage`` is 0..1 for all completed days; ``comparison_coverage`` omits
    February 29. All-calendar-day coverage controls eligibility. ``status`` is one of
    insufficient, below20, typical, above80. Percentiles require five of the last
    ten completed seasons and >=95% coverage in each reference/current season.
    Invalid values are rejected from sums and coverage, never replaced with 0
    observations. ``invalid_records`` counts rejected selected-date corrections.
    ``unreliable_dates`` are Terra model suspect days, separate from optional
    ``source_flag``/``source_flags`` record metadata. Neither removes chill.
    """
    if not isinstance(as_of, datetime) or as_of.utcoffset() != timedelta(0):
        raise ValueError("as_of must be a timezone-aware UTC datetime")
    today = as_of.astimezone(PST).date()
    year = today.year if today.month >= 9 else today.year - 1
    start, cutoff = date(year, 9, 1), today - timedelta(days=1)
    earliest = date(year - 10, 9, 1)
    index = {}
    for record in records:
        if record.get("station_id") != station_id or record.get("source", "fnric") != "fnric":
            continue
        try:
            day = date.fromisoformat(record["date"])
            if day.isoformat() != record["date"]:
                continue
        except (ValueError, KeyError, TypeError):
            continue
        if earliest <= day <= cutoff:
            index[day] = record
    current = _sum_season(index, start, cutoff)
    reported = [record for day, record in index.items() if start <= day <= cutoff and _valid(record)]
    total = float(sum(record["hours_32_45"] for record in reported))
    references = []
    for previous_year in range(year - 10, year):
        # No current-season day has completed at the start of September 1.
        if cutoff < start:
            break
        end_year = previous_year if cutoff.month >= 9 else previous_year + 1
        end_day = min(cutoff.day, monthrange(end_year, cutoff.month)[1])
        reference = _sum_season(index, date(previous_year, 9, 1), date(end_year, cutoff.month, end_day))
        if reference["coverage"] >= COVERAGE_MIN:
            references.append({"season_start": f"{previous_year}-09-01", **reference})
    usable = current["coverage"] >= COVERAGE_MIN and len(references) >= REFERENCE_MIN
    p20 = _percentile([item["total"] for item in references], 0.2) if usable else None
    p80 = _percentile([item["total"] for item in references], 0.8) if usable else None
    status = "insufficient"
    if usable:
        status = "below20" if current["total"] < p20 else "above80" if current["total"] > p80 else "typical"
    qc_dates = sorted(day for day in (unreliable_dates or set()) if start.isoformat() <= day <= cutoff.isoformat())
    source_dates = sorted(day.isoformat() for day, record in index.items()
                          if start <= day <= cutoff and (record.get("source_flag") or record.get("source_flags")))
    warnings = []
    if qc_dates:
        warnings.append("Reported chill includes dates with suspected Terra temperature anomalies: " + ", ".join(qc_dates) + ". Totals are retained.")
    if source_dates:
        warnings.append("FNRIC source flags are present on: " + ", ".join(source_dates) + ". Totals are retained.")
    invalid_count = sum(not _valid(record) for record in index.values())
    if invalid_count:
        warnings.append(f"Rejected {invalid_count} invalid daily record(s) from totals and hourly coverage.")
    if not usable:
        message = (f"Chill trend withheld: current reported hourly coverage {current['coverage']:.1%}; "
                   f"{len(references)} of 10 reference seasons meet 95% coverage (at least 5 required).")
    else:
        description = {"below20": "below the historical 20th percentile", "above80": "above the historical 80th percentile", "typical": "within the historical 20th–80th percentile range"}[status]
        message = f"Accumulated chill is {description} across {len(references)} usable prior seasons."
    return {
        "station_id": station_id, "source": "fnric", "metric": "hours_32_45",
        "season_start": start.isoformat(), "cutoff": cutoff.isoformat(),
        "total": total, "comparison_total": float(current["total"]),
        "coverage": current["coverage"], "expected_hours": current["expected_hours"],
        "reported_hours": current["reported_hours"], "reference_count": len(references),
        "comparison_coverage": current["comparison_coverage"],
        "comparison_expected_hours": current["comparison_expected_hours"],
        "comparison_reported_hours": current["comparison_reported_hours"],
        "references": references, "percentile20": p20, "percentile80": p80,
        "status": status, "message": message, "warning": " ".join(warnings) or None,
        "qc_suspect_dates": qc_dates, "source_flagged_dates": source_dates,
        "invalid_records": invalid_count,
        "timezone_assumption": "Fixed UTC-08:00; source alignment must be verified before combining sources.",
        "leap_day_policy": "Reported total and eligibility coverage include February 29; comparison totals and comparison coverage omit it in every season.",
    }


def transition(previous: dict | None, current: dict) -> dict | None:
    """Emit an entry/recovery only when a usable seasonal state changes.

    Callers should retain the last usable summary as ``previous`` across
    insufficient-data gaps, preventing repeated entries after coverage recovers.
    """
    before = previous.get("status") if previous else None
    after = current.get("status")
    if before == after or after == "insufficient":
        return None
    if after in ("below20", "above80"):
        kind = after
    elif after == "typical" and before in ("below20", "above80"):
        kind = "recovery"
    else:
        return None
    return {"kind": kind, "cutoff": current["cutoff"], "station_id": current["station_id"],
            "message": "Chill accumulation recovered into the historical typical range." if kind == "recovery" else current["message"]}
