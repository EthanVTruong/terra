"""Opted-in, fixed-recipient Twilio SMS and an explicit simulated-flag demo.

Assessment remains pure. This module has no scheduler, provider-data ingestion,
background retry, public recipient input or subscription service. SQLite records
one logical delivery with bounded caller-driven retries only for definitive
rate-limit rejection; an uncertain send is never blindly repeated.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from threading import Lock
from typing import Callable, Mapping

import httpx
from .configuration import (DEFAULT_STATE_DIR, JournalSelectionError,
                            check_sms_journal_selection, read_environment_file, sms_journal_path)

EVENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}\Z")
SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z")
PHONE = re.compile(r"\+[1-9][0-9]{6,14}\Z")
SID = re.compile(r"SM[0-9a-fA-F]{32}\Z")
OUTBOUND_STATUSES = {"accepted", "scheduled", "queued", "sending", "sent", "delivered",
                     "undelivered", "failed", "canceled"}
TERMINAL_STATUSES = {"delivered", "undelivered", "failed", "canceled"}
TRIAL_TEMPLATE = "sms_internal_alerts"
ENV_KEYS = {"TERRA_SMS_ENABLED", "TERRA_SMS_DEMO_ENABLED", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN",
            "TWILIO_API_KEY_SID", "TWILIO_API_KEY_SECRET", "API_KEY_SID", "API_KEY_SECRET",
            "TWILIO_FROM_NUMBER", "TERRA_SMS_TO_NUMBER", "TERRA_SMS_DB_PATH", "TERRA_SMS_MAX_PER_DAY",
            "TERRA_SMS_CONTENT_MODE"}
DEFAULT_DB = DEFAULT_STATE_DIR / "sms.sqlite3"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value) -> datetime:
    if not isinstance(value, (str, datetime)):
        raise ValueError("An aware timestamp is required")
    result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("An aware timestamp is required")
    return result.astimezone(timezone.utc)


def _mask(value: str) -> str | None:
    return "***" + value[-4:] if PHONE.fullmatch(value) else None


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_environment(environment: Mapping[str, str]) -> tuple[dict[str, str], list[str]]:
    """Read only an explicitly requested file; process values win over its values.

    Deliberately not a shell: no expansion, interpolation, command execution or
    implicit .env lookup. Unrelated keys can coexist but are never consumed.
    """
    values: dict[str, str] = {}
    errors: list[str] = []
    filename = environment.get("TERRA_SMS_ENV_FILE", "")
    if filename:
        try:
            _, parsed = read_environment_file(filename)
            values = {key: value for key, value in parsed.items() if key in ENV_KEYS}
        except ValueError:
            errors.append("env_file_unavailable_or_invalid")
            values = {}
    values.update({key: str(value) for key, value in environment.items() if key in ENV_KEYS})
    return values, errors


@dataclass(frozen=True)
class SMSConfig:
    account_sid: str = field(default="", repr=False)
    username: str = field(default="", repr=False)
    secret: str = field(default="", repr=False)
    sender: str = field(default="", repr=False)
    recipient: str = field(default="", repr=False)
    enabled: bool = False
    demo_enabled: bool = False
    db_path: Path = DEFAULT_DB
    max_per_day: int = 10
    errors: tuple[str, ...] = ()
    content_mode: str = "terra"

    @property
    def template_name(self) -> str | None:
        return TRIAL_TEMPLATE if self.content_mode == "trial_template" else None

    @classmethod
    def from_env(cls, environment: Mapping[str, str] | None = None, *,
                 state_dir: str | Path | None = None) -> SMSConfig:
        values, errors = _read_environment(os.environ if environment is None else environment)

        def boolean(key: str) -> bool:
            raw = values.get(key, "false").strip().lower()
            if raw not in {"", "0", "false", "no", "off", "1", "true", "yes", "on"}:
                errors.append("invalid_" + key.lower())
            return raw in {"1", "true", "yes", "on"}

        account = values.get("TWILIO_ACCOUNT_SID", "").strip()
        token = values.get("TWILIO_AUTH_TOKEN", "").strip()
        key = values.get("TWILIO_API_KEY_SID", values.get("API_KEY_SID", "")).strip()
        key_secret = values.get("TWILIO_API_KEY_SECRET", values.get("API_KEY_SECRET", "")).strip()
        sender = values.get("TWILIO_FROM_NUMBER", "").strip()
        recipient = values.get("TERRA_SMS_TO_NUMBER", "").strip()
        content_mode = values.get("TERRA_SMS_CONTENT_MODE", "terra").strip()
        if content_mode not in {"terra", "trial_template"}:
            errors.append("invalid_content_mode")
            content_mode = "terra"  # Never echo an invalid operator value.
        if not re.fullmatch(r"AC[0-9a-fA-F]{32}", account):
            errors.append("invalid_or_missing_account_sid")
        if key or key_secret:
            if not re.fullmatch(r"SK[0-9a-fA-F]{32}", key) or not key_secret or any(c.isspace() for c in key_secret):
                errors.append("invalid_or_missing_api_key")
            username, secret = key, key_secret
        else:
            if not re.fullmatch(r"[0-9a-fA-F]{32}", token):
                errors.append("invalid_or_missing_auth_token")
            username, secret = account, token
        if content_mode == "terra" and not PHONE.fullmatch(sender):
            errors.append("invalid_or_missing_sender")
        if not PHONE.fullmatch(recipient):
            errors.append("invalid_or_missing_recipient")
        try:
            limit = int(values.get("TERRA_SMS_MAX_PER_DAY", "10"))
            if not 1 <= limit <= 100:
                raise ValueError("range")
        except (TypeError, ValueError):
            limit = 10
            errors.append("invalid_daily_limit")
        try:
            db_path = sms_journal_path(values, state_dir)
        except (OSError, ValueError):
            db_path = DEFAULT_DB
            errors.append("invalid_delivery_store_path")
        return cls(account, username, secret, sender, recipient, boolean("TERRA_SMS_ENABLED"),
                   boolean("TERRA_SMS_DEMO_ENABLED"), db_path,
                   limit, tuple(errors), content_mode)


def build_demo_assessment() -> dict:
    """Run the real frozen detector on a fixed, explicitly synthetic spike.

    A fixed historical simulated hour makes repeated previews deterministic;
    it is never described as a present-day provider observation.
    """
    import pandas as pd
    from .assessment import assess_location

    hours = pd.date_range("2026-01-11T09:00:00Z", periods=8, freq="h")
    stations = pd.DataFrame({
        "station_id": [6, 226, 139, 212, 235],
        "lat": [38.535694, 38.672722, 38.501258, 38.278056, 38.797944],
        "lon": [-121.77636, -121.81172, -121.97853, -121.74111, -121.61136],
        "elevation_ft": [60.0, 82.0, 136.0, 7.0, 24.0],
    })
    observations = pd.DataFrame([
        {"station_id": station, "timestamp": hour,
         "temperature_f": 120.0 if station == 6 and hour == hours[-1] else 40.0,
         "quality": "usable", "source_flag": "", "source": "synthetic-demo",
         "temperature_unit": "F", "timestamp_verified": True}
        for hour in hours for station in stations.station_id
    ])
    return assess_location(lat=38.6, lon=-121.8, elevation_ft=60.0,
        valid_utc=hours[-1], as_of=hours[-1], observations=observations, stations=stations,
        snapshot_id="terra-sms-controlled-spike-v1", data_mode="synthetic", location_id="sms-demo")


class SMSDispatcher:
    """Single opted-in phone, durable attempt ledger and redacted public results."""

    def __init__(self, config: SMSConfig, *, transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], datetime] = _utcnow):
        self.config = config
        self._transport = transport
        self._clock = clock
        self._recipient_hash = _digest(config.recipient)
        self._schema_lock = Lock()
        self._schema_ready = False

    @classmethod
    def from_env(cls, environment: Mapping[str, str] | None = None, *,
                 state_dir: str | Path | None = None, **kwargs) -> SMSDispatcher:
        config = SMSConfig.from_env(environment, state_dir=state_dir)
        check_sms_journal_selection(config.db_path, state_dir)
        return cls(config, **kwargs)

    @contextmanager
    def _connection(self):
        self.config.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.config.db_path, timeout=10, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=10000")
            if not self._schema_ready:
                with self._schema_lock:
                    if not self._schema_ready:
                        self._initialize_schema(db)
                        self._schema_ready = True
            yield db
        finally:
            db.close()

    @staticmethod
    def _initialize_schema(db):
        """Initialize/migrate once per dispatcher, retaining every legacy row/table."""
        db.executescript("""
            CREATE TABLE IF NOT EXISTS sms_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sms_delivery (
                delivery_key TEXT PRIMARY KEY, event_id TEXT NOT NULL, recipient_hash TEXT NOT NULL,
                mode TEXT NOT NULL, body_hash TEXT NOT NULL, status TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, attempted_at TEXT,
                provider_sid TEXT, provider_status TEXT, error_code TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 1, next_attempt_at TEXT, source_event_id TEXT,
                content_mode TEXT NOT NULL DEFAULT 'terra', template_name TEXT, source_body_hash TEXT
            );
            CREATE INDEX IF NOT EXISTS sms_event ON sms_delivery(recipient_hash, event_id);
            CREATE TABLE IF NOT EXISTS sms_attempt (
                delivery_key TEXT NOT NULL, attempt_no INTEGER NOT NULL,
                attempted_at TEXT NOT NULL, outcome TEXT NOT NULL DEFAULT 'sending', error_code TEXT,
                PRIMARY KEY (delivery_key, attempt_no)
            );
        """)
        # Existing local ledgers predate retry and content metadata. Add fields
        # without discarding no-resend evidence or the retired episode table.
        columns = {row[1] for row in db.execute("PRAGMA table_info(sms_delivery)")}
        for column, declaration in (("attempt_count", "INTEGER NOT NULL DEFAULT 1"),
                                    ("next_attempt_at", "TEXT"), ("source_event_id", "TEXT"),
                                    ("content_mode", "TEXT NOT NULL DEFAULT 'terra'"),
                                    ("template_name", "TEXT"), ("source_body_hash", "TEXT")):
            if column not in columns:
                try:
                    db.execute("ALTER TABLE sms_delivery ADD COLUMN " + column + " " + declaration)
                except sqlite3.OperationalError:
                    if column not in {row[1] for row in db.execute("PRAGMA table_info(sms_delivery)")}:
                        raise
        db.execute("""INSERT OR IGNORE INTO sms_attempt
                (delivery_key,attempt_no,attempted_at,outcome,error_code)
                SELECT delivery_key,attempt_count,attempted_at,status,error_code FROM sms_delivery
                WHERE attempted_at IS NOT NULL""")

    def _suppressed(self) -> bool:
        if not self.config.db_path.exists():
            return False
        with self._connection() as db:
            row = db.execute("SELECT value FROM sms_settings WHERE key='disabled'").fetchone()
            return row is not None and row[0] == "true"

    def status(self) -> dict:
        errors = list(self.config.errors)
        if self.config.content_mode not in {"terra", "trial_template"}:
            errors.append("invalid_content_mode")
        configured = not errors
        try:
            suppressed = self._suppressed()
        except (OSError, sqlite3.Error, ValueError):
            suppressed = True
            errors.append("delivery_store_unavailable")
        enabled = bool(self.config.enabled and configured and not suppressed and not errors)
        return {"transport": "twilio", "configured": configured, "enabled": enabled,
                "live_enabled": enabled, "demo_enabled": bool(enabled and self.config.demo_enabled),
                "suppressed": suppressed, "recipient": _mask(self.config.recipient),
                "sender": _mask(self.config.sender) if self.config.content_mode == "terra" else None,
                "max_per_day": self.config.max_per_day,
                "content_mode": self.config.content_mode if self.config.content_mode in {"terra", "trial_template"} else None,
                "template_name": self.config.template_name,
                "configuration_errors": sorted(set(errors))}

    @staticmethod
    def _result(event_id: str, status: str, *, mode: str | None = None,
                error_code: str | None = None, duplicate: bool = False, **fields) -> dict:
        result = {"event_id": event_id if isinstance(event_id, str) and EVENT_ID.fullmatch(event_id) else None,
                  "status": status, "duplicate": duplicate}
        if mode is not None:
            result["mode"] = mode
        if error_code is not None:
            result["error_code"] = error_code
        result.update(fields)
        return result

    def _row_result(self, row: sqlite3.Row, *, duplicate: bool = False) -> dict:
        # A process may have died after claiming an attempt. Its absence of a
        # SID cannot prove that Twilio did not accept the message.
        status = "uncertain" if row["status"] == "sending" and not row["provider_sid"] else row["status"]
        fields = {key: row[column] for key, column in
                  (("provider_message_id", "provider_sid"), ("provider_status", "provider_status"),
                   ("error_code", "error_code"), ("updated_at_utc", "updated_at"),
                   ("attempt_count", "attempt_count"), ("next_attempt_at_utc", "next_attempt_at"),
                   ("source_event_id", "source_event_id")) if row[column] is not None}
        return self._result(row["event_id"], status, mode=row["mode"], duplicate=duplicate,
                            transport="twilio", data_mode={"demo": "synthetic", "farm": "current",
                                                         "live": "current"}.get(row["mode"]),
                            content_mode=row["content_mode"], template_name=row["template_name"], **fields)

    def _key(self, event_id: str) -> str:
        return _digest(self._recipient_hash + ":" + event_id)

    def _existing(self, event_id: str) -> sqlite3.Row | None:
        with self._connection() as db:
            return db.execute("SELECT * FROM sms_delivery WHERE delivery_key=?", (self._key(event_id),)).fetchone()

    def _update(self, event_id: str, **fields) -> dict:
        allowed = {"status", "provider_sid", "provider_status", "error_code", "next_attempt_at"}
        if not fields.keys() <= allowed:
            raise ValueError("Unsupported delivery update")
        fields["updated_at"] = _stamp(self._clock()).isoformat()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM sms_delivery WHERE delivery_key=?", (self._key(event_id),)).fetchone()
            if row is None:
                raise ValueError("Unknown delivery")
            rank = {"accepted": 0, "scheduled": 0, "queued": 1, "sending": 2, "sent": 3}
            old_rank = rank.get(row["provider_status"], -1)
            new_rank = rank.get(fields.get("provider_status"), 4)
            if row["status"] in TERMINAL_STATUSES or new_rank < old_rank:
                db.commit()
                return self._row_result(row)
            columns = ",".join(key + "=?" for key in fields)
            db.execute("UPDATE sms_delivery SET " + columns + " WHERE delivery_key=?",
                       (*fields.values(), self._key(event_id)))
            if "status" in fields:
                db.execute("UPDATE sms_attempt SET outcome=?,error_code=? WHERE delivery_key=? AND attempt_no=?",
                    (fields["status"], fields.get("error_code"), self._key(event_id), row["attempt_count"]))
            row = db.execute("SELECT * FROM sms_delivery WHERE delivery_key=?", (self._key(event_id),)).fetchone()
            db.commit()
            return self._row_result(row)

    def _reserve(self, event_id: str, mode: str, body: str, source_event_id: str | None = None,
                 source_body_hash: str | None = None) -> dict | None:
        now = _stamp(self._clock())
        source_body_hash = source_body_hash or _digest(body)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM sms_delivery WHERE delivery_key=?", (self._key(event_id),)).fetchone()
            if row is not None:
                if (row["body_hash"] != _digest(body) or row["mode"] != mode
                        or row["content_mode"] != self.config.content_mode
                        or row["template_name"] != self.config.template_name
                        or (row["source_body_hash"] or row["body_hash"]) != source_body_hash
                        or row["source_event_id"] not in {None, source_event_id}):
                    db.commit()
                    return self._result(event_id, "blocked", mode=mode, error_code="event_id_conflict")
                if source_event_id is not None and row["source_event_id"] is None:
                    db.execute("UPDATE sms_delivery SET source_event_id=? WHERE delivery_key=?",
                               (source_event_id, self._key(event_id)))
                    row = db.execute("SELECT * FROM sms_delivery WHERE delivery_key=?", (self._key(event_id),)).fetchone()
                retry_due = (mode == "farm" and row["status"] == "retry_wait" and row["attempt_count"] < 3
                             and row["next_attempt_at"] is not None and _stamp(row["next_attempt_at"]) <= now)
                if not retry_due:
                    db.commit()
                    return self._row_result(row, duplicate=True)
            disabled = db.execute("SELECT value FROM sms_settings WHERE key='disabled'").fetchone()
            if disabled is not None and disabled[0] == "true":
                db.commit()
                return self._result(event_id, "suppressed", mode=mode)
            count = db.execute("SELECT COUNT(*) FROM sms_attempt WHERE attempted_at>=?",
                               (now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),)).fetchone()[0]
            if count >= self.config.max_per_day:
                db.commit()
                return self._result(event_id, "rate_limited", mode=mode, error_code="daily_attempt_limit")
            attempt = 1 if row is None else row["attempt_count"] + 1
            if row is None:
                db.execute("""INSERT INTO sms_delivery
                    (delivery_key,event_id,recipient_hash,mode,body_hash,status,created_at,updated_at,attempted_at,
                     source_event_id,content_mode,template_name,source_body_hash)
                    VALUES (?,?,?,?,?,'sending',?,?,?,?,?,?,?)""",
                    (self._key(event_id), event_id, self._recipient_hash, mode, _digest(body),
                     now.isoformat(), now.isoformat(), now.isoformat(), source_event_id,
                     self.config.content_mode, self.config.template_name, source_body_hash))
            else:
                db.execute("""UPDATE sms_delivery SET status='sending',attempt_count=?,next_attempt_at=NULL,error_code=NULL,
                    attempted_at=?,updated_at=? WHERE delivery_key=?""",
                    (attempt, now.isoformat(), now.isoformat(), self._key(event_id)))
            db.execute("INSERT INTO sms_attempt(delivery_key,attempt_no,attempted_at) VALUES (?,?,?)",
                       (self._key(event_id), attempt, now.isoformat()))
            db.commit()
        return None

    def _client(self) -> httpx.Client:
        return httpx.Client(transport=self._transport, timeout=httpx.Timeout(15, connect=5),
                            follow_redirects=False, trust_env=False,
                            auth=httpx.BasicAuth(self.config.username, self.config.secret))

    def _url(self, sid: str | None = None) -> str:
        root = "https://api.twilio.com/2010-04-01/Accounts/" + self.config.account_sid + "/Messages"
        return root + ("/" + sid if sid is not None else "") + ".json"

    @staticmethod
    def _provider_code(payload, fallback: str) -> str:
        code = payload.get("code", payload.get("error_code")) if isinstance(payload, dict) else None
        return str(code) if isinstance(code, int) and not isinstance(code, bool) and 0 < code < 1000000 else fallback

    @staticmethod
    def _message_state(payload, expected_sid: str | None = None) -> tuple[str, str] | None:
        if not isinstance(payload, dict):
            return None
        sid, status = payload.get("sid"), payload.get("status")
        if not isinstance(sid, str) or not SID.fullmatch(sid) or not isinstance(status, str) or status not in OUTBOUND_STATUSES:
            return None
        if expected_sid is not None and sid != expected_sid:
            return None
        return sid, status

    def _send(self, event_id: str, mode: str, body: str, *, validity_period: int = 300,
              expires_at: datetime | None = None, source_event_id: str | None = None) -> dict:
        if not isinstance(event_id, str) or not EVENT_ID.fullmatch(event_id):
            return self._result(event_id, "blocked", mode=mode, error_code="invalid_event_id")
        if isinstance(validity_period, bool) or not isinstance(validity_period, int) or not 1 <= validity_period <= 300:
            return self._result(event_id, "blocked", mode=mode, error_code="invalid_validity_period")
        state = self.status()
        if not state["configured"] or state["configuration_errors"]:
            return self._result(event_id, "not_configured", mode=mode, error_code="sms_configuration")
        if state["suppressed"]:
            return self._result(event_id, "suppressed", mode=mode)
        if not state["enabled"] or mode == "demo" and not state["demo_enabled"]:
            return self._result(event_id, "disabled", mode=mode)
        wire_body = self.config.template_name if self.config.content_mode == "trial_template" else body
        try:
            duplicate = self._reserve(event_id, mode, wire_body, source_event_id, _digest(body))
            if duplicate is not None:
                return duplicate
            try:
                with self._client() as client:
                    if self._suppressed():
                        return self._update(event_id, status="suppressed")
                    if expires_at is not None:
                        # Recheck after ledger/client setup, directly before
                        # the request. Never round a sub-second lifetime up to
                        # Twilio's one-second minimum or extend the deadline.
                        remaining = int((_stamp(expires_at) - _stamp(self._clock())).total_seconds())
                        if remaining < 1:
                            return self._update(event_id, status="expired", error_code="event_expired")
                        validity_period = min(validity_period, remaining)
                    payload = {"To": self.config.recipient, "Body": wire_body}
                    if self.config.content_mode == "terra":
                        payload.update(From=self.config.sender, ValidityPeriod=str(validity_period))
                    # Trials accept predefined bodies only. Their API does not
                    # support From or ValidityPeriod; local expiry still gates
                    # submission, but cannot bound Twilio's later queue time.
                    response = client.post(self._url(), data=payload)
            except httpx.HTTPError:
                return self._update(event_id, status="uncertain", error_code="provider_outcome_unknown")
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if response.status_code >= 500 or response.status_code == 408:
                return self._update(event_id, status="uncertain",
                                    error_code=self._provider_code(payload, "provider_outcome_unknown"))
            if response.status_code == 429:
                possible_sid = payload.get("sid") if isinstance(payload, dict) else None
                possible_status = payload.get("status") if isinstance(payload, dict) else None
                has_sid = isinstance(possible_sid, str) and SID.fullmatch(possible_sid)
                accepted = isinstance(possible_status, str) and possible_status in {
                    "accepted", "scheduled", "queued", "sending", "sent", "delivered"}
                if has_sid or accepted:
                    return self._update(event_id, status="uncertain", error_code="provider_outcome_unknown",
                        provider_sid=possible_sid if has_sid else None,
                        provider_status=possible_status if isinstance(possible_status, str) and possible_status in OUTBOUND_STATUSES else None)
                current = self._existing(event_id)
                if mode == "farm" and current is not None and current["attempt_count"] < 3:
                    delay = 30 if current["attempt_count"] == 1 else 120
                    due = _stamp(self._clock()) + timedelta(seconds=delay)
                    return self._update(event_id, status="retry_wait",
                        error_code=self._provider_code(payload, "20429"), next_attempt_at=due.isoformat())
            if not 200 <= response.status_code < 300:
                return self._update(event_id, status="failed",
                                    error_code=self._provider_code(payload, "provider_rejected"))
            provider = self._message_state(payload)
            if provider is None:
                return self._update(event_id, status="uncertain", error_code="provider_invalid_response")
            sid, provider_status = provider
            outcome = provider_status if provider_status in TERMINAL_STATUSES else "provider_accepted"
            return self._update(event_id, status=outcome, provider_sid=sid, provider_status=provider_status,
                                error_code=self._provider_code(payload, "provider_delivery_failed")
                                if provider_status in {"failed", "undelivered", "canceled"} else None)
        except (OSError, sqlite3.Error, ValueError, TypeError):
            # Do not expose exception strings: paths, response text and auth may
            # contain private values. A possibly accepted send remains in the
            # pre-send durable record and cannot be resent automatically.
            return self._result(event_id, "uncertain", mode=mode, error_code="delivery_store_unavailable")

    @staticmethod
    def _body(finding: dict, evidence: dict, *, demo: bool) -> str:
        hour = _stamp(evidence["timestamp"]).strftime("%Y-%m-%d %H:%M UTC")
        prefix = "TERRA DEMO - simulated flag. Simulated " if demo else "TERRA - suspected temperature anomaly. "
        return (f"{prefix}station {finding['station_id']} at {hour}: {evidence['temperature_f']:.1f} F; "
                f"expected {evidence['expected_f']:.1f} F ({evidence['residual_f']:+.1f} F). "
                "Check an independent reading. This is not confirmed sensor failure.")

    def demo(self, event_id: str) -> dict:
        if not isinstance(event_id, str) or not EVENT_ID.fullmatch(event_id):
            return self._result(event_id, "blocked", mode="demo", error_code="invalid_event_id")
        try:
            packet = build_demo_assessment()
            finding = next(f for f in packet["station_findings"] if f["station_id"] == 6)
            evidence = next(e for e in finding["evidence"] if _stamp(e["timestamp"]) == _stamp(packet["query"]["valid_utc"]))
            return {**self._send(event_id, "demo", self._body(finding, evidence, demo=True)), "assessment": packet}
        except (ValueError, TypeError, KeyError, StopIteration, OSError):
            return self._result(event_id, "blocked", mode="demo", error_code="demo_assessment_unavailable")



    def dispatch_event(self, envelope: dict) -> dict:
        """Send one immutable, eligible orchard message from a trusted backend.

        This internal boundary accepts a prepared terra-notification-v1 message,
        never untrusted browser text. The caller owns orchard subscriptions,
        event grouping, text persistence, relevance rechecks and outbox draining.
        Real recipient/sender and transport opt-in stay in this dispatcher.
        """
        source_id = envelope.get("event_id") if isinstance(envelope, dict) else None
        valid_id = isinstance(source_id, str) and SOURCE_ID.fullmatch(source_id) is not None
        ledger_id = "farm-" + _digest(source_id) if valid_id else None

        def blocked(code: str, status: str = "blocked") -> dict:
            result = self._result(ledger_id, status, mode="farm", error_code=code)
            if valid_id:
                result["source_event_id"] = source_id
            return result

        if not isinstance(envelope, dict) or not valid_id:
            return blocked("invalid_notification")
        try:
            schema = envelope.get("schema_version", envelope.get("schema"))
            if schema != "terra-notification-v1" or envelope.get("schema", schema) != schema:
                return blocked("invalid_notification_schema")
            if envelope["data_mode"] != "current":
                return blocked("noncurrent_notification")
            if envelope["kind"] != "orchard_update" or envelope["transition"] not in {
                    "initial", "opening", "escalation", "recovery", "milestone", "combined"}:
                return blocked("invalid_notification_kind")
            if envelope["severity"] not in {"info", "moderate", "strong"} or envelope["category"] not in {
                    "temperature", "chill", "data_health"}:
                return blocked("invalid_notification_category")
            orchard = envelope["orchard_id"]
            if not isinstance(orchard, str) or not SOURCE_ID.fullmatch(orchard):
                return blocked("invalid_orchard_id")
            for key in ("source_event_ids", "episode_ids"):
                identifiers = envelope[key]
                if not isinstance(identifiers, list) or len(identifiers) > 100 or key == "source_event_ids" and not identifiers:
                    return blocked("invalid_notification_identifiers")
                if any(not isinstance(item, str) or not SOURCE_ID.fullmatch(item) for item in identifiers) or len(set(identifiers)) != len(identifiers):
                    return blocked("invalid_notification_identifiers")
            eligibility = envelope["eligibility"]
            if not isinstance(eligibility, dict) or not isinstance(eligibility.get("eligible"), bool):
                return blocked("invalid_notification_eligibility")
            if eligibility["eligible"] is not True:
                return blocked("notification_ineligible", "suppressed")
            text = envelope["text"]
            if not isinstance(text, str) or not text.strip() or len(text) > 1200 or any(ord(c) < 32 and c not in "\n\r\t" for c in text):
                return blocked("invalid_notification_text")
            text.encode("utf-8")  # Reject unpaired surrogates before reservation.
            now = _stamp(self._clock())
            created, checked, expires = (_stamp(envelope[key]) for key in
                ("created_at_utc", "checked_at_utc", "expires_at_utc"))
            if created > now or checked > now or expires <= created or expires - created > timedelta(hours=6):
                return blocked("invalid_notification_times")
            if now - checked > timedelta(minutes=5):
                return blocked("stale_monitor_check")
            if expires <= now or expires - now < timedelta(seconds=1):
                return blocked("event_expired", "expired")
            observation = envelope["observation_time_utc"]
            if observation is None:
                if envelope["category"] == "temperature":
                    return blocked("missing_observation_time")
            else:
                observed = _stamp(observation)
                if observed > checked:
                    return blocked("invalid_observation_time")
                if envelope["category"] == "temperature" and now - observed > timedelta(hours=6):
                    return blocked("stale_observation")
            validity = min(300, int((expires - now).total_seconds()))
            result = self._send(ledger_id, "farm", text, validity_period=validity, expires_at=expires,
                                source_event_id=source_id)
            return {**result, "source_event_id": source_id}
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError, UnicodeError):
            return blocked("invalid_notification")

    dispatch_message = dispatch_event


    def receipt(self, event_id: str) -> dict:
        """Reconcile only the persisted SID; never issue another send."""
        if not isinstance(event_id, str) or not EVENT_ID.fullmatch(event_id):
            return self._result(event_id, "blocked", error_code="invalid_event_id")
        try:
            row = self._existing(event_id)
            if row is None:
                return self._result(event_id, "not_found")
            result = self._row_result(row)
            if not row["provider_sid"] or row["status"] in TERMINAL_STATUSES:
                return result
            if not SID.fullmatch(row["provider_sid"]):
                return {**result, "receipt_error": "invalid_persisted_message_id"}
            # Advance the persisted poll-attempt time even when a provider
            # check fails. Oldest-first bounded batches must not let one bad
            # SID permanently starve later accepted messages.
            result = self._update(event_id)
            if result["status"] in TERMINAL_STATUSES:
                return result
            if self.config.errors:
                return {**result, "receipt_error": "sms_configuration"}
            try:
                with self._client() as client:
                    response = client.get(self._url(row["provider_sid"]))
                payload = response.json()
            except (httpx.HTTPError, ValueError):
                return {**result, "receipt_error": "provider_receipt_unavailable"}
            if not 200 <= response.status_code < 300:
                return {**result, "receipt_error": self._provider_code(payload, "provider_receipt_unavailable")}
            provider = self._message_state(payload, row["provider_sid"])
            if provider is None:
                return {**result, "receipt_error": "provider_invalid_receipt"}
            sid, provider_status = provider
            outcome = provider_status if provider_status in TERMINAL_STATUSES else "provider_accepted"
            return self._update(event_id, status=outcome, provider_sid=sid, provider_status=provider_status,
                error_code=self._provider_code(payload, "provider_delivery_failed")
                if provider_status in {"failed", "undelivered", "canceled"} else None)
        except (OSError, sqlite3.Error, ValueError, TypeError):
            return self._result(event_id, "blocked", error_code="delivery_store_unavailable")

    def reconcile_pending(self, limit: int = 10) -> list[dict]:
        """Poll a bounded oldest-first batch of persisted, nonterminal SIDs.

        The caller schedules this work. This method sends no SMS and cannot
        retry uncertain attempts without a provider SID. Original farm event
        IDs survive restart so callers can update their own outbox receipts.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            return [self._result(None, "blocked", error_code="invalid_receipt_limit")]
        try:
            with self._connection() as db:
                pending = db.execute("""SELECT event_id FROM sms_delivery
                    WHERE recipient_hash=? AND provider_sid IS NOT NULL
                    AND status IN ('provider_accepted','uncertain','sending')
                    ORDER BY updated_at,created_at,delivery_key LIMIT ?""",
                    (self._recipient_hash, limit)).fetchall()
            return [self.receipt(row["event_id"]) for row in pending]
        except (OSError, sqlite3.Error, ValueError, TypeError):
            return [self._result(None, "blocked", error_code="delivery_store_unavailable")]

    poll_pending_receipts = reconcile_pending

    def disable(self) -> dict:
        """Persist opt-out for this local dispatcher; already in-flight SMS cannot be recalled."""
        try:
            with self._connection() as db:
                db.execute("INSERT OR REPLACE INTO sms_settings(key,value) VALUES ('disabled','true')")
            return {"status": "disabled", **self.status()}
        except (OSError, sqlite3.Error, ValueError):
            return {"status": "blocked", "error_code": "delivery_store_unavailable"}

    def enable(self) -> dict:
        """Explicitly resume the opted-in local recipient without discarding history."""
        if self.config.errors or not self.config.enabled:
            return {"status": "blocked", "error_code": "sms_configuration", **self.status()}
        try:
            with self._connection() as db:
                db.execute("INSERT OR REPLACE INTO sms_settings(key,value) VALUES ('disabled','false')")
            return {"status": "enabled", **self.status()}
        except (OSError, sqlite3.Error, ValueError):
            return {"status": "blocked", "error_code": "delivery_store_unavailable"}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status", "demo", "receipt", "disable", "enable"])
    parser.add_argument("--event-id", help="Stable caller UUID; repeat it to inspect, never duplicate, one attempt")
    parser.add_argument("--state-dir", type=Path, help="Shared runtime state directory when using a custom runtime")
    args = parser.parse_args(argv)
    if args.command in {"demo", "receipt"} and not args.event_id:
        parser.error("--event-id is required for demo and receipt")
    try:
        dispatcher = SMSDispatcher.from_env(state_dir=args.state_dir)
    except JournalSelectionError as exc:
        parser.error(str(exc))
    result = getattr(dispatcher, args.command)(args.event_id) if args.command in {"demo", "receipt"} else getattr(dispatcher, args.command)()
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
