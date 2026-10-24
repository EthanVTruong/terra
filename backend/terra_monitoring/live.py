"""Bounded current CIMIS snapshots joined to the existing assessment function.

No training, repair or continuous polling runs here. Acquisition timestamps are
Terra's observation of a revision, never a claimed provider publication time.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
from threading import RLock
from typing import Any

import pandas as pd

from chillwatch import config
from chillwatch.ingest import select_region
from chillwatch.qc.neighbors import nearest_neighbors
from chillwatch.sources.cimis import CimisAuthError, CimisBadRequest, CimisRefused, CimisSource
from terra_qc import qc
from terra_qc.reference import load_reference
from .assessment import _json, assess_location
from .weather import VARIABLES, normalize_cimis_weather

PST = timezone(timedelta(hours=-8))
LIVE_COLUMNS = ["station_id", "timestamp", "temperature_f", "quality", "source_flag", "source",
                "temperature_unit", "timestamp_verified", "timestamp_basis", "visible_at",
                "source_value", "source_unit", "original_timestamp", "temperature_reason"]


class LiveQueryError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 503, retryable: bool = True):
        super().__init__(message)
        self.code, self.message = code, message
        self.status_code, self.retryable = status_code, retryable


def _utc(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError("An aware UTC acquisition clock is required")
    return stamp.tz_convert("UTC")


def _id(value: Any) -> int:
    if isinstance(value, bool) or not str(value).isdecimal() or int(value) < 1:
        raise ValueError("Invalid station identity")
    return int(value)


def _canonical(rows: list[dict]) -> tuple[list[dict], int, int]:
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        groups.setdefault((row["station_id"], row["timestamp"]), []).append(row)
    result, duplicates, conflicts = [], 0, 0
    for _, group in sorted(groups.items()):
        row = dict(group[0])
        if len(group) > 1:
            duplicates += 1
            signatures = {json.dumps(_json({k: item.get(k) for k in
                ("temperature_f", "quality", "source_flag", "temperature_unit", "temperature_reason")}),
                sort_keys=True, allow_nan=False) for item in group}
            if len(signatures) > 1:
                conflicts += 1
                row.update(temperature_f=None, quality="invalid", temperature_reason="conflicting_duplicate",
                           availability_reason="conflicting_duplicate", available=False)
            # Conflicts in one variable never invalidate another measurement.
            for field in VARIABLES:
                keys = (field, field + "_quality", field + "_source_flag", field + "_source_unit")
                signatures = {json.dumps(_json({k: item.get(k) for k in keys}), sort_keys=True)
                              for item in group}
                if len(signatures) > 1:
                    row.update({field: None, field + "_quality": "invalid",
                                field + "_availability_reason": "conflicting_duplicate", field + "_available": False,
                                field + "_availability_reasons": ["conflicting_duplicate"]})
        result.append(row)
    return result, duplicates, conflicts


def normalize_cimis_payload(payload: dict, *, requested_station_ids, observed_at) -> tuple[pd.DataFrame, dict]:
    """Verify raw station/hour/scope/units/QC before certifying normalized rows."""
    observed_at = _utc(observed_at)
    requested = {_id(value) for value in requested_station_ids}
    data = payload.get("Data") if isinstance(payload, dict) else None
    providers = data.get("Providers") if isinstance(data, dict) else None
    if not isinstance(providers, list):
        raise LiveQueryError("provider_invalid_response", "The weather provider returned an unsupported response.")
    rows, quarantined, received = [], [], 0
    for provider in providers:
        records = provider.get("Records") if isinstance(provider, dict) else None
        if not isinstance(records, list):
            raise LiveQueryError("provider_invalid_response", "The weather provider returned an unsupported response.")
        for record in records:
            received += 1
            reason, sid = None, None
            try:
                if not isinstance(record, dict):
                    raise ValueError("record")
                sid = _id(record.get("Station"))
                if sid not in requested:
                    reason = "unrequested_station"
                elif str(record.get("Scope", "")).strip().lower() != "hourly":
                    reason = "unverified_scope"
                elif not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(record.get("Date", ""))):
                    reason = "invalid_date"
                elif not re.fullmatch(r"(?:0[1-9]|1\d|2[0-4])00", str(record.get("Hour", ""))):
                    reason = "invalid_hour"
                if reason:
                    raise ValueError(reason)
                source_day = date.fromisoformat(record["Date"])
                hour = int(record["Hour"][:2])
                stamp = pd.Timestamp(datetime.combine(source_day, datetime.min.time(), PST) + timedelta(hours=hour)).tz_convert("UTC")
                if stamp > observed_at:
                    reason = "future_hour"
                    raise ValueError(reason)
            except (TypeError, ValueError, OverflowError):
                quarantined.append({"station_id": sid, "reason": reason or "invalid_station_or_date"})
                continue
            measurement = record.get("HlyAirTmp")
            measurement = measurement if isinstance(measurement, dict) else {}
            raw_value, raw_unit = measurement.get("Value"), measurement.get("Unit")
            flag = measurement.get("Qc") if "Qc" in measurement else None
            flag = None if flag is None else str(flag).strip()
            unit = str(raw_unit or "").strip().upper().replace(" ", "")
            fahrenheit = unit in {"F", "°F", "(F)", "(°F)", "DEGF", "FAHRENHEIT"}
            celsius = unit in {"C", "°C", "(C)", "(°C)", "DEGC", "CELSIUS"}
            temperature, quality, value_reason = None, "usable", None
            if raw_value is None or raw_value == "":
                quality, value_reason = "missing", "missing_temperature"
            else:
                try:
                    if isinstance(raw_value, bool):
                        raise ValueError("boolean temperature")
                    temperature = float(raw_value)
                    if not math.isfinite(temperature):
                        raise ValueError("nonfinite temperature")
                except (TypeError, ValueError, OverflowError):
                    temperature, quality, value_reason = None, "invalid", "invalid_temperature"
            if not fahrenheit and not celsius:
                temperature, quality, value_reason = None, "invalid", "unit_unverified"
            elif temperature is not None and celsius:
                temperature = temperature * 9 / 5 + 32
            if quality == "usable" and flag is None:
                quality, value_reason = "flagged", "source_qc_unknown"
            elif quality == "usable" and flag:
                quality, value_reason = "flagged", "source_warning"
            rows.append({**normalize_cimis_weather(record), "station_id": sid, "timestamp": stamp, "temperature_f": temperature,
                "quality": quality, "source_flag": flag, "source": "cimis",
                "temperature_unit": "F" if fahrenheit or celsius else None,
                "timestamp_verified": True, "timestamp_basis": "CIMIS fixed-PST hour-ending verified",
                "visible_at": observed_at, "source_value": _json(raw_value), "source_unit": raw_unit,
                "original_timestamp": f"{record['Date']} {record['Hour']} PST", "temperature_reason": value_reason})
    rows, duplicate_groups, conflicting_groups = _canonical(rows)
    frame = pd.DataFrame(rows) if rows else pd.DataFrame(columns=LIVE_COLUMNS)
    frame["timestamp"] = pd.to_datetime(frame.timestamp, utc=True)
    frame["visible_at"] = pd.to_datetime(frame.visible_at, utc=True)
    frame["timestamp_verified"] = frame.timestamp_verified.astype(bool)
    audit = {"received_records": received, "parsed_rows": len(rows), "quarantined": quarantined,
             "source_unavailable_rows": sum(row["quality"] != "usable" for row in rows),
             "duplicate_groups": duplicate_groups, "conflicting_groups": conflicting_groups}
    return frame, audit


@dataclass
class _Snapshot:
    observations: pd.DataFrame
    stations: pd.DataFrame
    snapshot_id: str
    retrieved_at: pd.Timestamp
    station_ids: list[int]
    data_mode: str


@dataclass
class _Cohort:
    snapshot: _Snapshot | None = None
    last_attempt: pd.Timestamp | None = None
    failure: LiveQueryError | None = None


class LiveQueryService:
    """One shared, throttled source and immutable recent snapshots per process."""
    def __init__(self, app_key=None, data_dir=None, clock=None, source=None, ttl_seconds=120,
                 max_cache_entries=32, failure_cooldown_seconds=120):
        self.reference = load_reference()
        self.data_dir = Path(data_dir or config.repo_root() / ".terra-local" / "live")
        self.clock = clock or (lambda: pd.Timestamp.now(tz="UTC"))
        self.ttl_seconds = float(ttl_seconds)
        if not math.isfinite(self.ttl_seconds) or self.ttl_seconds < 0:
            raise ValueError("Cache duration must be finite and nonnegative")
        if isinstance(max_cache_entries, bool) or not isinstance(max_cache_entries, int) or max_cache_entries < 1:
            raise ValueError("Cache capacity must be a positive integer")
        self.max_cache_entries = max_cache_entries
        self.failure_cooldown_seconds = float(failure_cooldown_seconds)
        if not math.isfinite(self.failure_cooldown_seconds) or self.failure_cooldown_seconds < 0:
            raise ValueError("Failure cooldown must be finite and nonnegative")
        key = app_key if app_key is not None else (os.environ.get("CIMIS_APP_KEY") or os.environ.get("CIMIS_API_KEY"))
        self.configured = source is not None or bool(key)
        self.source = source or CimisSource(app_key=key or "", api="new",
            hourly_items=["hly-air-tmp", "hly-rel-hum", "hly-wind-spd", "hly-sol-rad"],
            cache_dir=self.data_dir / "raw" / "cimis-new-weather-v1", timeout_s=10)
        self._lock = RLock()
        self._catalog = None
        self._catalog_at = None
        self._catalog_attempt_at = None
        self._catalog_failure = None
        self._cohorts: OrderedDict[tuple, _Cohort] = OrderedDict()
        self._seen: dict[str, str] = {}
        path = self.data_dir / "first-seen.json"
        if path.exists():
            try:
                self._seen = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                raise LiveQueryError("provider_invalid_response", "The local source-revision index needs review.", retryable=False)

    def _now(self):
        return _utc(self.clock())

    def _cohort(self, key):
        # Date keys from earlier days cannot serve a current query. Discard only
        # process caches; immutable snapshots and first-seen evidence stay intact.
        for old in list(self._cohorts):
            if old[2] != key[2]:
                del self._cohorts[old]
        cohort = self._cohorts.pop(key, None) or _Cohort()
        self._cohorts[key] = cohort
        while len(self._cohorts) > self.max_cache_entries:
            self._cohorts.popitem(last=False)
        return cohort

    def context(self) -> dict:
        geometry = self.reference.manifest["station_geometry"]["stations"]
        return {"schema_version": "terra-live-context-v1", "configured": self.configured, "source": "CIMIS",
            "max_age_hours": 6, "station_presets": [{"station_id": int(s), "name": row["name"],
                "lat": row["latitude"], "lon": row["longitude"], "elevation_ft": row["elevation_ft"]}
                for s, row in geometry.items()]}

    def _load_catalog(self, now, force=False):
        if (not force and self._catalog_failure is not None and self._catalog_attempt_at is not None
                and (now - self._catalog_attempt_at).total_seconds() < self.failure_cooldown_seconds):
            raise self._catalog_failure from None
        if self._catalog is not None and self._catalog_failure is None and not force and (now - self._catalog_at).total_seconds() < 3600:
            return self._catalog.copy()
        self._catalog_attempt_at = now
        try:
            raw = self._refresh_catalog()
        except Exception as exc:
            self._catalog_failure = self._as_source_error(exc)
            raise self._catalog_failure from None
        self._catalog_failure = None
        self._catalog, self._catalog_at = raw.copy(), self._now()
        return raw

    def _refresh_catalog(self):
        raw = self.source.list_stations(refresh=True).copy()
        if "station_id" not in raw:
            if "source_station_id" not in raw:
                raise LiveQueryError("provider_invalid_response", "The station catalog has no verified station identities.")
            raw["station_id"] = raw.source_station_id.map(_id)
        raw["station_id"] = raw.station_id.map(_id)
        if raw.station_id.duplicated().any():
            raise LiveQueryError("provider_invalid_response", "The station catalog contains conflicting identities.")
        for name in ("lat", "lon", "elevation_ft"):
            raw[name] = pd.to_numeric(raw[name], errors="coerce")
        raw = raw[raw.lat.between(-90, 90) & raw.lon.between(-180, 180) & raw.elevation_ft.map(math.isfinite)].copy()
        for name in ("connect_date", "disconnect_date"):
            raw[name] = pd.to_datetime(raw[name], errors="coerce") if name in raw else pd.NaT
        if "is_active" not in raw:
            raw["is_active"] = True
        frozen = self.reference.manifest["station_geometry"]["stations"]
        for row in raw[raw.station_id.isin(qc.STATIONS)].to_dict("records"):
            before = frozen[str(row["station_id"])]
            if not all(math.isclose(row[key], before[old], rel_tol=0, abs_tol=1e-6) for key, old in
                       (("lat", "latitude"), ("lon", "longitude"), ("elevation_ft", "elevation_ft"))):
                raise LiveQueryError("source_geometry_changed", "A calibrated station's location metadata changed and needs review.", retryable=False)
        return raw

    def _candidates(self, query, catalog, now):
        regional = catalog[select_region(catalog, config.region())].copy()
        target = query.get("target_station_id")
        if target is not None and target not in set(catalog.station_id):
            raise LiveQueryError("invalid_query", "The selected station is not in the current station catalog.", status_code=422, retryable=False)
        if target is not None:
            row = catalog[catalog.station_id.eq(target)].iloc[0]
            if not all(math.isclose(query[key], row[key], rel_tol=0, abs_tol=1e-6)
                       for key in ("lat", "lon", "elevation_ft")):
                raise LiveQueryError("invalid_query", "The selected sensor must match the queried coordinates and elevation.", status_code=422, retryable=False)
        nearby = nearest_neighbors(query["lat"], query["lon"], query["elevation_ft"], regional,
            day=now.tz_convert(PST).date(), exclude=set() if target is None else {target})
        ids = set(qc.STATIONS) & set(catalog.station_id)
        ids.update(int(s) for s in nearby.neighbor_id)
        if target is not None:
            ids.add(target)
        return sorted(ids)

    def _refresh(self, ids, start, end, catalog):
        returned = self.source.fetch_hourly(",".join(map(str, ids)), start, end, refresh=True)
        if returned.empty or "raw_ref" not in returned:
            raise LiveQueryError("no_hourly_observations", "The provider returned no verifiable hourly observations.")
        observed_at = self._now()
        frames, payloads, audits = [], [], []
        source_root = Path(self.source.cache_dir).resolve()
        for ref in dict.fromkeys(returned.raw_ref.dropna().astype(str)):
            path = Path(ref).resolve()
            if not path.is_relative_to(source_root):
                raise LiveQueryError("provider_invalid_response", "The provider snapshot reference is outside its cache.")
            raw = path.read_bytes()
            def invalid_number(value):
                raise ValueError("Non-standard provider JSON")
            try:
                payload = json.loads(raw, parse_constant=invalid_number)
            except (ValueError, UnicodeError):
                raise LiveQueryError("provider_invalid_response", "The weather provider returned an unsupported response.") from None
            frame, audit = normalize_cimis_payload(payload, requested_station_ids=ids, observed_at=observed_at)
            frames.append(frame)
            payloads.append(payload)
            audits.append(audit)
        if not frames:
            raise LiveQueryError("no_hourly_observations", "The provider returned no verifiable hourly observations.")
        combined = pd.concat(frames, ignore_index=True)
        canonical, _, _ = _canonical(combined.to_dict("records"))
        observations = pd.DataFrame(canonical) if canonical else pd.DataFrame(columns=LIVE_COLUMNS)
        if observations.empty:
            raise LiveQueryError("no_hourly_observations", "The provider returned no verifiable hourly observations.")
        for index, row in observations.iterrows():
            revision = _json({key: row.get(key) for key in
                ("station_id", "timestamp", "temperature_f", "quality", "source_flag", "source_value", "source_unit", "temperature_reason")})
            revision["weather"] = _json({key: value for key, value in row.items()
                                        if any(key == field or key.startswith(field + "_") for field in VARIABLES)})
            digest = hashlib.sha256(json.dumps(revision, sort_keys=True, allow_nan=False).encode()).hexdigest()
            visible = self._seen.setdefault(digest, observed_at.isoformat())
            observations.at[index, "visible_at"] = _utc(visible)
        observations["timestamp"] = pd.to_datetime(observations.timestamp, utc=True)
        observations["visible_at"] = pd.to_datetime(observations.visible_at, utc=True)
        stations = catalog[catalog.station_id.isin(ids)].copy()
        mode = getattr(self.source, "data_mode", "current")
        snapshot = _json({"schema_version": "terra-live-snapshot-v1", "provider": "CIMIS", "api_generation": "new",
            "requested_station_ids": ids, "request_start": start, "request_end": end,
            "retrieved_at_utc": observed_at, "data_mode": mode, "stations": stations.to_dict("records"),
            "observations": observations.to_dict("records"), "raw_payloads": payloads, "audit": audits})
        encoded = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        folder = self.data_dir / "snapshots"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (digest + ".json")
        if path.exists() and path.read_bytes() != encoded:
            raise LiveQueryError("provider_invalid_response", "A local immutable snapshot failed its integrity check.", retryable=False)
        if not path.exists():
            path.write_bytes(encoded)
        seen = self.data_dir / "first-seen.json"
        temporary = seen.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._seen, sort_keys=True), encoding="utf-8")
        temporary.replace(seen)
        return _Snapshot(observations, stations, "sha256:" + digest, observed_at, ids, mode)

    def assess(self, query: dict) -> dict:
        query = dict(query)
        for key in ("lat", "lon", "elevation_ft"):
            value = query.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise LiveQueryError("invalid_query", "Coordinates and elevation must be finite numbers.", status_code=422, retryable=False)
        if not -90 <= query["lat"] <= 90 or not -180 <= query["lon"] <= 180:
            raise LiveQueryError("invalid_query", "The coordinates are out of range.", status_code=422, retryable=False)
        target = query.get("target_station_id")
        if target is not None:
            try:
                target = _id(target)
            except ValueError as exc:
                raise LiveQueryError("invalid_query", "The station identity is invalid.", status_code=422, retryable=False) from exc
            query["target_station_id"] = target
        if not self.configured:
            raise LiveQueryError("source_not_configured", "Live weather access is not configured.", retryable=False)
        with self._lock:
            started = self._now()
            try:
                catalog = self._load_catalog(started, force=bool(query.get("refresh")))
            except LiveQueryError:
                raise
            except Exception as exc:
                self._source_error(exc)
            ids = self._candidates(query, catalog, started)
            end = started.tz_convert(PST).date()
            start = end - timedelta(days=2)
            key = (tuple(ids), start, end)
            cohort = self._cohort(key)
            cached = cohort.snapshot
            status = "cached_after_error" if cohort.failure else "cached"
            age = (started - cached.retrieved_at).total_seconds() if cached else float("inf")
            forced = bool(query.get("refresh"))
            cooling = (cohort.failure is not None and cohort.last_attempt is not None
                       and (started - cohort.last_attempt).total_seconds() < self.failure_cooldown_seconds)
            refresh_due = forced or cached is None or age >= self.ttl_seconds
            if refresh_due and (forced or not cooling):
                cohort.last_attempt = started
                try:
                    cached = self._refresh(ids, start, end, catalog)
                    cohort.snapshot = cached
                    cohort.failure = None
                    status = "refreshed"
                except Exception as exc:
                    failure = self._as_source_error(exc)
                    cohort.failure = failure
                    if cached is None or failure.code != "provider_unavailable":
                        raise failure from None
                    status = "cached_after_error"
            elif cohort.failure and (cached is None or cohort.failure.code != "provider_unavailable"):
                # Cooldown never turns a malformed/auth response into success.
                raise cohort.failure from None
            clock = self._now()
            eligible = cached.observations[cached.observations.timestamp.le(clock)]
            if eligible.empty:
                raise LiveQueryError("no_hourly_observations", "No completed hourly observations are available.")
            valid = eligible.timestamp.max()
            packet = assess_location(lat=query["lat"], lon=query["lon"], elevation_ft=query["elevation_ft"],
                valid_utc=valid, as_of=clock, observations=cached.observations, stations=cached.stations,
                snapshot_id=cached.snapshot_id, data_mode=cached.data_mode,
                location_id=query.get("location_id") or (f"CIMIS-{target}" if target is not None else f"LOCATION-{query['lat']:.4f},{query['lon']:.4f}"),
                target_station_id=target, max_age_hours=6, reference=self.reference,
                geometry_source="current CIMIS catalog; caller-supplied query elevation")
            through = eligible.groupby("station_id").timestamp.max()
            live = {"provider": "CIMIS", "status": status, "checked_at_utc": clock,
                "retrieved_at_utc": cached.retrieved_at, "last_attempt_at_utc": cohort.last_attempt,
                "data_through_utc": valid, "per_station_data_through_utc": {sid: through.get(sid) for sid in ids},
                "cache_age_seconds": max(0, (clock - cached.retrieved_at).total_seconds()),
                "station_ids_requested": ids, "missing_station_ids": sorted(set(ids) - set(through.index)),
                "failed_station_ids": ids if status == "cached_after_error" else [],
                "warning": "The provider check failed. Retained observations are shown with their original times." if status == "cached_after_error" else None}
            packet["provenance"]["live_source"] = _json(live)
            packet["provenance"]["source_visibility_basis"] = "first observed by Terra; not original provider publication time"
            packet["provenance"]["qc_calibrated_station_ids"] = list(qc.STATIONS)
            packet["provenance"]["uncalibrated_source_station_ids"] = sorted(set(ids) - set(qc.STATIONS))
            json.dumps(packet, allow_nan=False)
            return packet

    @staticmethod
    def _as_source_error(exc):
        if isinstance(exc, LiveQueryError):
            return exc
        if isinstance(exc, CimisAuthError):
            return LiveQueryError("provider_auth", "Live weather access was refused by the provider.", retryable=False)
        if isinstance(exc, (ValueError, TypeError, KeyError, AttributeError, CimisBadRequest, CimisRefused)):
            return LiveQueryError("provider_invalid_response", "The weather provider returned an unsupported response.", retryable=False)
        return LiveQueryError("provider_unavailable", "The live weather provider could not be checked. Please retry.")

    @classmethod
    def _source_error(cls, exc):
        raise cls._as_source_error(exc) from None
