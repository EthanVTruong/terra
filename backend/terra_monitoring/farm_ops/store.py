"""One SQLite file for profiles, immutable evidence, events and the local inbox."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3

DEFAULT_DB = Path(__file__).resolve().parents[3] / ".terra-local" / "farm-chill-ops.sqlite3"


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def utc(value=None):
    stamp = datetime.now(timezone.utc) if value is None else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("A timezone-aware clock is required")
    return stamp.astimezone(timezone.utc)


class Store:
    def __init__(self, path=None):
        configured = path if path is not None else os.environ.get("TERRA_FARM_DB", DEFAULT_DB)
        if path is None and os.environ.get("TERRA_FARM_DB") and not Path(configured).is_absolute():
            raise ValueError("TERRA_FARM_DB must be an absolute path shared by API and worker")
        self.path = Path(configured).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS orchards(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS hours(
                  orchard TEXT NOT NULL, stamp TEXT NOT NULL, payload TEXT NOT NULL,
                  PRIMARY KEY(orchard,stamp), FOREIGN KEY(orchard) REFERENCES orchards(id));
                CREATE TABLE IF NOT EXISTS revisions(
                  orchard TEXT NOT NULL, stamp TEXT NOT NULL, signature TEXT NOT NULL,
                  payload TEXT NOT NULL, PRIMARY KEY(orchard,stamp,signature));
                CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(
                  id TEXT PRIMARY KEY, orchard TEXT NOT NULL, payload TEXT NOT NULL,
                  created TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS messages(
                  id TEXT PRIMARY KEY, orchard TEXT NOT NULL, kind TEXT NOT NULL,
                  created TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS writer_attempts(
                  id INTEGER PRIMARY KEY, orchard TEXT NOT NULL, created TEXT NOT NULL,
                  provider TEXT NOT NULL, outcome TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS leases(name TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS events_pending ON events(orchard,published,created);
                CREATE INDEX IF NOT EXISTS messages_orchard ON messages(orchard,created);
            """)

    @contextmanager
    def connect(self, *, write=False):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=15000")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def state(db, key, default=None):
        row = db.execute("SELECT payload FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def set_state(db, key, value):
        db.execute("INSERT INTO state VALUES(?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload",
                   (key, encoded(value)))

    def get(self, key, default=None):
        with self.connect() as db:
            return self.state(db, key, default)

    def orchards(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM orchards ORDER BY id")]

    def orchard(self, orchard_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM orchards WHERE id=?", (orchard_id,)).fetchone()
        if row is None:
            raise KeyError("Orchard not found")
        return json.loads(row[0])

    def save_orchard(self, profile, *, sms_opt_in=False, now=None):
        with self.connect(write=True) as db:
            old = db.execute("SELECT payload FROM orchards WHERE id=?", (profile["id"],)).fetchone()
            if old and json.loads(old[0]) != profile:
                raise ValueError("An orchard profile is immutable; create another ID for a different location or method")
            db.execute("INSERT OR IGNORE INTO orchards VALUES(?,?)", (profile["id"], encoded(profile)))
            if not old:
                self.set_state(db, "sms-opt-in:" + profile["id"],
                    {"enabled": sms_opt_in, "changed_at": utc(now).isoformat(), "channel": "sms",
                     "recipient_binding": "private transport configuration"})
        return profile

    @staticmethod
    def retain(db, packet):
        key = fingerprint(packet)
        db.execute("INSERT OR IGNORE INTO evidence VALUES(?,?)", (key, encoded(packet)))
        return key

    @staticmethod
    def put_hour(db, orchard, reading):
        stamp = utc(reading["valid_utc"]).isoformat()
        previous = db.execute("SELECT payload FROM hours WHERE orchard=? AND stamp=?", (orchard, stamp)).fetchone()
        # Acquisition/freshness clocks alone are not observation corrections.
        fields = ("temperature_f", "usable", "method_version", "qc_reference", "geometry_key", "status_reason")
        signature = fingerprint({k: reading.get(k) for k in fields})
        if previous:
            old = json.loads(previous[0])
            if signature == fingerprint({k: old.get(k) for k in fields}):
                return False
        body = encoded({**reading, "valid_utc": stamp})
        db.execute("INSERT OR IGNORE INTO revisions VALUES(?,?,?,?)", (orchard, stamp, signature, body))
        db.execute("INSERT INTO hours VALUES(?,?,?) ON CONFLICT(orchard,stamp) DO UPDATE SET payload=excluded.payload",
                   (orchard, stamp, body))
        return True

    @staticmethod
    def hours_in(db, orchard):
        return [json.loads(r[0]) for r in db.execute("SELECT payload FROM hours WHERE orchard=? ORDER BY stamp", (orchard,))]

    def hours(self, orchard):
        with self.connect() as db:
            return self.hours_in(db, orchard)

    @staticmethod
    def add_event(db, orchard, event, now):
        db.execute("INSERT OR IGNORE INTO events(id,orchard,payload,created) VALUES(?,?,?,?)",
                   (event["id"], orchard, encoded({**event, "created_at": utc(now).isoformat()}), utc(now).isoformat()))

    def pending(self, orchard):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute(
                "SELECT payload FROM events WHERE orchard=? AND published=0 ORDER BY created,id", (orchard,))]

    def events(self, orchard):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM events WHERE orchard=? ORDER BY created,id", (orchard,))]

    def publish(self, orchard, packet, result, event_ids, now, *, question_id=None, consumed_ids=None, created_at=None):
        message_id = fingerprint({"orchard": orchard, "events": sorted(event_ids), "question": question_id})
        body = {"id": message_id, "orchard_id": orchard, "created_at": utc(created_at or now).isoformat(),
                "published_at": utc(now).isoformat(),
                "kind": "answer" if question_id else "proactive",
                "text": result["text"], "generation": result["provider"], "generation_outcome": result["outcome"],
                "transport": "local_inbox", "status": "published", "llm_input": packet, "event_ids": event_ids}
        with self.connect(write=True) as db:
            db.execute("INSERT OR IGNORE INTO messages VALUES(?,?,?,?,?)", (message_id, orchard,
                       "answer" if question_id else "proactive", body["created_at"], encoded(body)))
            for event_id in consumed_ids if consumed_ids is not None else event_ids:
                db.execute("UPDATE events SET published=1 WHERE orchard=? AND id=?", (orchard, event_id))
            db.execute("INSERT INTO writer_attempts(orchard,created,provider,outcome) VALUES(?,?,?,?)",
                       (orchard, body["created_at"], result["provider"], result["outcome"]))
            saved = db.execute("SELECT payload FROM messages WHERE id=?", (message_id,)).fetchone()
        return json.loads(saved[0])

    def message(self, message_id):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM messages WHERE id=?", (message_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def messages(self, orchard, *, limit=100):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute(
                "SELECT payload FROM messages WHERE orchard=? ORDER BY created DESC,rowid DESC LIMIT ?", (orchard, limit))]

    def details(self, orchard, message_id):
        messages = [m for m in self.messages(orchard, limit=1000) if m["id"] == message_id]
        if not messages:
            raise KeyError("Message not found")
        message = messages[0]
        events = [e for e in self.events(orchard) if e["id"] in message["event_ids"]]
        refs = {e["evidence_id"] for e in events if e.get("evidence_id")}
        with self.connect() as db:
            facts = [json.loads(r[0]) for key in sorted(refs)
                     if (r := db.execute("SELECT payload FROM evidence WHERE id=?", (key,)).fetchone())]
        return {"message": message, "events": events, "assessments": facts,
                "fnric": self.get("fnric:" + orchard),
                "history_archives": self.get("history:" + orchard, []),
                "transport_receipt": self.get("transport:" + message_id),
                "note": "Full evidence and exact compact LLM input; local publication is not SMS delivery."}

    def acquire(self, name, owner, now, *, seconds=120):
        clock = utc(now).timestamp()
        with self.connect(write=True) as db:
            row = db.execute("SELECT owner,expires FROM leases WHERE name=?", (name,)).fetchone()
            if row and row["expires"] > clock and row["owner"] != owner:
                return False
            db.execute("INSERT INTO leases VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET owner=excluded.owner,expires=excluded.expires",
                       (name, owner, clock + seconds))
        return True

    def release(self, name, owner):
        with self.connect(write=True) as db:
            db.execute("DELETE FROM leases WHERE name=? AND owner=?", (name, owner))
