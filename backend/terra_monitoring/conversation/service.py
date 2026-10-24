"""Durable onboarding and explicit message/reaction/none response outcomes."""
from __future__ import annotations

import json
import re

from ..farm_ops.store import encoded, fingerprint, utc
from .routing import ACK, THANKS, PROMPT, QUESTIONS, LOCAL_INTENTS, crops_from, route
from .ownership import active, current_owner, owner_status
from .local_facts import WEATHER_INTENTS


_FACT_CONTEXTS = WEATHER_INTENTS


def _weather_context(data):
    facts = data.get("local_facts", {})
    if data.get("intent") == "conversation":
        return {"weather_reference": bool(data.get("evidence_ids")),
                "weather_evidence_ids": data.get("evidence_ids", []),
                "temperature_evidence_id": data.get("temperature_evidence_id")}
    return {"weather_reference": data.get("intent") in _FACT_CONTEXTS - {"weather_history_unavailable"},
            "weather_evidence_ids": [facts["evidence_id"]] if facts.get("evidence_id") else [],
            "temperature_evidence_id": facts.get("temperature_evidence_id")}


def _publication_time(item):
    value = item.get("published_at")
    if not isinstance(value, str):
        return None
    try:
        return utc(value)
    except (ValueError, TypeError, OverflowError):
        return None


class RequestInProgress(ValueError):
    """A reserved request cannot safely be inferred again, even after restart."""


class RequestAlreadyCompleted(ValueError):
    """A recovery acknowledgment cannot replace a known real completion."""


class SetupChanged(ValueError):
    """A late device result cannot overwrite a restarted or changed draft."""


def empty_setup():
    return {"stage": "crop", "crops": [], "variety": None, "orchard_id": None,
            "location": None, "notification_preference": None}


def _owner_payload(value):
    try:
        return json.loads(value) if value else None
    except (ValueError, TypeError):
        return None


class ConversationService:
    def __init__(self, monitor, *, notification_capability="in_app", location_resolver=None):
        if notification_capability not in {"in_app", "browser_open"}:
            raise ValueError("Unsupported notification capability")
        self.monitor, self.store = monitor, monitor.store
        self.owner = current_owner()
        self.notification_capability = notification_capability
        from .location import GroundElevationResolver
        self.location_resolver = location_resolver or GroundElevationResolver()
        with self.store.connect(write=True) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tara_turns(
                  id TEXT PRIMARY KEY, thread TEXT NOT NULL, created TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS tara_thread ON tara_turns(thread,created);
                CREATE TABLE IF NOT EXISTS tara_requests(
                  id TEXT PRIMARY KEY, thread TEXT NOT NULL, signature TEXT NOT NULL,
                  created TEXT NOT NULL, status TEXT NOT NULL, payload TEXT);
            """)
            if "owner" not in {row["name"] for row in db.execute("PRAGMA table_info(tara_requests)")}:
                db.execute("ALTER TABLE tara_requests ADD COLUMN owner TEXT")

    def _saved_setup(self):
        return self.store.get("tara:setup")

    def _device_setup_snapshot(self):
        with self.store.connect() as db:
            db.execute("BEGIN")
            setup = self.store.state(db, "tara:setup") or empty_setup()
            cursor = db.execute("SELECT COALESCE(MAX(rowid),0) FROM tara_requests WHERE thread='onboarding'").fetchone()[0]
        return setup, (fingerprint(setup), cursor)

    def _selection(self, orchard_id=None):
        saved = self._saved_setup()
        if orchard_id:
            self.store.orchard(orchard_id)
            return orchard_id
        if saved is not None:
            return saved.get("orchard_id")
        return None

    def _turns(self, thread):
        with self.store.connect() as db:
            rows = db.execute("SELECT payload FROM tara_turns WHERE thread=? ORDER BY rowid", (thread,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def state(self, orchard_id=None):
        selected = self._selection(orchard_id)
        saved = self._saved_setup()
        setup = saved or empty_setup()
        if selected and (not saved or selected != saved.get("orchard_id")):
            p = self.store.orchard(selected)
            setup = {"stage": "complete", "crops": p.get("crops", [p["crop"]]), "variety": p.get("variety"),
                     "orchard_id": selected, "location": {k: p[k] for k in ("lat", "lon", "elevation_ft", "name")},
                     "notification_preference": None}
        thread = selected or "onboarding"
        preference = self.store.get("tara:notifications:" + thread, {"enabled": False, "permission": "default"})
        status = self.monitor.status(selected) if selected else None
        monitoring = {"status": "not_configured", "reason": "Confirm your farm location first.", "details": status}
        if status:
            worker = status.get("worker", {}).get("heartbeat_status")
            if status["orchard"]["mode"] != "current":
                monitoring.update(status="pending", reason="Saved replay readings aren't live monitoring; the live monitor is off for this farm.")
            elif status.get("comparison_available") and worker == "fresh":
                monitoring.update(status="ready", reason="Current estimates and the monitor are available.")
            elif status.get("comparison_available"):
                monitoring.update(status="unavailable" if worker == "stale_or_stopped" else "pending",
                                  reason="Saved weather data is usable, but the monitor isn't running.")
            elif worker == "stale_or_stopped":
                monitoring.update(status="unavailable", reason="The monitor is stopped or its heartbeat is stale.")
            elif status.get("source_error") or status.get("last_checked") and not status.get("comparison_available"):
                monitoring.update(status="unavailable", reason="Weather data is unavailable or incomplete.")
            elif worker == "fresh":
                monitoring.update(status="pending", reason="Waiting for usable weather data.")
            else:
                monitoring.update(status="pending", reason="Waiting for usable weather data and the monitor.")
        with self.store.connect() as db:
            pending = []
            for row in db.execute("SELECT id,created,owner FROM tara_requests WHERE thread=? AND status='pending' ORDER BY created", (thread,)):
                status_owner = owner_status(_owner_payload(row["owner"]), self.store.path, row["id"])
                pending.append({"request_id": row["id"], "created_at": row["created"], "owner_status": status_owner,
                                "reconcilable": status_owner == "exited"})
        return {"schema_version": "terra-conversation-v1", "setup": setup, "orchard_id": selected,
                "orchards": self.store.orchards(), "turns": self._turns(thread), "monitoring": monitoring,
                "initial_prompt": PROMPT if not selected and setup["stage"] == "crop" else None,
                "notifications": {**preference, "capability": self.notification_capability, "scope": "while_open"},
                "pending_requests": pending}

    def _context(self, orchard_id, reply_to):
        context, event_ids = None, []
        weather = _weather_context({})
        if reply_to:
            with self.store.connect() as db:
                row = db.execute("SELECT thread,payload FROM tara_turns WHERE id=?", (reply_to,)).fetchone()
            if row:
                if row["thread"] != (orchard_id or "onboarding"):
                    raise ValueError("The referenced message belongs to another farm")
                data = json.loads(row["payload"]).get("data", {})
                context = data.get("focus") if data.get("intent") == "conversation" else data.get("intent")
                event_ids = data.get("source_event_ids", [])
                weather = _weather_context(data)
            else:
                note = self.store.message(reply_to)
                if not note or note["orchard_id"] != orchard_id:
                    raise ValueError("Choose an existing message from this farm")
                kinds = [e["type"] for e in note.get("llm_input", {}).get("events", [])]
                context = "explain_alert" if any(k not in {"welcome", "chill_total", "history", "station_health"} for k in kinds) else "history" if "history" in kinds else "station_health" if "station_health" in kinds else "chill_total"
                event_ids = note.get("event_ids", [])
                events = self.store.events_by_ids(orchard_id, event_ids)
                weather = {"weather_reference": True,
                           "weather_evidence_ids": list(dict.fromkeys(e["evidence_id"] for e in events if e.get("evidence_id"))),
                           "temperature_evidence_id": None}
        elif orchard_id:
            assistant = None
            for turn in reversed(self._turns(orchard_id)):
                if turn.get("role") == "assistant":
                    assistant = turn
                    intent = turn.get("data", {}).get("intent")
                    context = (turn.get("data", {}).get("focus") or "farm") if intent == "conversation" else intent if intent in QUESTIONS or intent in LOCAL_INTENTS or intent in _FACT_CONTEXTS else "farm"
                    event_ids = turn.get("data", {}).get("source_event_ids", []) if intent in QUESTIONS or intent == "conversation" else []
                    weather = _weather_context(turn.get("data", {}))
                    break
            # Match the latest visible proactive note when actual publication
            # metadata establishes recency. Never guess from a replay clock or
            # let a stored question-answer duplicate replace its Terra context.
            notes = [note for note in self.store.messages(orchard_id)
                     if note.get("kind") == "proactive" and _publication_time(note) is not None]
            if notes:
                newest = max(notes, key=_publication_time)
                previous = _publication_time(assistant) if assistant else None
                if assistant is None or previous is not None and _publication_time(newest) > previous:
                    return self._context(orchard_id, newest["id"])
            # A farm supplies history's subject, not a fictitious earlier reply.
            context = context or "farm"
        return {"intent": context, "event_ids": event_ids, **weather}

    def _weather_reply(self, intent, text, selected, context, reply_to):
        from .local_facts import weather_answer
        if intent == "weather_history_unavailable":
            return weather_answer(intent, self.monitor, selected)
        current = re.search(r"\b(now|currently|current|latest|newest|updated)\b", text, re.I)
        elliptical = intent in {"temperature_normal", "freshness"} or re.search(r"\b(that|this|those|these|them)\b", text, re.I)
        referenced = bool(context["weather_reference"] and not current and (reply_to or elliptical))
        ids = context["weather_evidence_ids"]
        if referenced and intent in {"temperature", "temperature_normal"} and context["temperature_evidence_id"]:
            ids = [context["temperature_evidence_id"]]
        if referenced and len(ids) > 1:
            return "Which reading in that note do you mean?", {"referenced_snapshot": True, "evidence_ids": ids}
        return weather_answer(intent, self.monitor, selected, evidence_id=ids[0] if referenced and ids else None, referenced=referenced)

    @staticmethod
    def _identifier(value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
            raise ValueError("Provide a stable request identifier")

    def _reserve(self, request_id, thread, body, user_text=None, reply_to=None, *, expected_setup=None):
        self._identifier(request_id)
        signature, now = fingerprint(body), utc(self.monitor.clock()).isoformat()
        user = None
        with self.store.connect(write=True) as db:
            old = db.execute("SELECT * FROM tara_requests WHERE id=?", (request_id,)).fetchone()
            if old:
                if old["signature"] != signature:
                    raise ValueError("A request identifier cannot be reused for different input")
                if old["status"] == "pending":
                    raise RequestInProgress("This request is pending; refresh its status before trying another request")
                return json.loads(old["payload"]), None
            if expected_setup is not None:
                current = self.store.state(db, "tara:setup") or empty_setup()
                cursor = db.execute("SELECT COALESCE(MAX(rowid),0) FROM tara_requests WHERE thread='onboarding'").fetchone()[0]
                if (fingerprint(current), cursor) != expected_setup:
                    raise SetupChanged("The setup changed during location lookup")
            if db.execute("SELECT 1 FROM tara_requests WHERE thread=? AND status='pending'", (thread,)).fetchone():
                raise RequestInProgress("A request in this conversation is pending")
            db.execute("INSERT INTO tara_requests(id,thread,signature,created,status,payload,owner) VALUES(?,?,?,?,?,NULL,?)",
                       (request_id, thread, signature, now, "pending", encoded(self.owner) if self.owner else None))
            if user_text is not None:
                if thread == "onboarding" and not db.execute("SELECT 1 FROM tara_turns WHERE thread=?", (thread,)).fetchone():
                    opening = self._turn(request_id + ":opening", "assistant", PROMPT, now,
                                         data={"action": "initial_prompt"})
                    self._put(db, thread, opening)
                user = self._turn(request_id + ":user", "user", user_text, now, reply_to=reply_to)
                self._put(db, thread, user)
        active(self.store.path, request_id, True)
        return None, user

    def _existing(self, request_id, body):
        self._identifier(request_id)
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM tara_requests WHERE id=?", (request_id,)).fetchone()
        if row is None:
            return None
        if row["signature"] != fingerprint(body):
            raise ValueError("A request identifier cannot be reused for different input")
        if row["status"] == "pending":
            raise RequestInProgress("This request is pending; refresh its status before trying another request")
        core = json.loads(row["payload"])
        return {**core, "state": self.state(core["orchard_id"])}

    @staticmethod
    def _turn(identifier, role, text, created, *, kind="text", reply_to=None, data=None):
        return {"id": identifier, "role": role, "kind": kind, "text": text, "created_at": created,
                "published_at": utc().isoformat(),
                "reply_to": reply_to, "reactions": [], "delivery": "local_saved", "data": data or {}}

    @staticmethod
    def _put(db, thread, turn):
        existing = db.execute("SELECT payload FROM tara_turns WHERE id=?", (turn["id"],)).fetchone()
        if existing:
            turn.clear()
            turn.update(json.loads(existing[0]))
            return
        # Serialize publication with native notes in the shared SQLite journal,
        # rather than timestamping a reply while it waits for the write lock.
        turn["published_at"] = utc().isoformat()
        db.execute("INSERT OR IGNORE INTO tara_turns VALUES(?,?,?,?)", (turn["id"], thread, turn["created_at"], encoded(turn)))

    def _finish(self, request_id, thread, user, *, text=None, reaction=False, event=None, data=None, setup=None, preference=None, migrate=None, decision=None):
        now = utc(self.monitor.clock()).isoformat()
        assistant = self._turn(request_id + ":assistant", "assistant", text, now, data=data) if text else None
        attached = {"emoji": "👍", "target_id": user["id"]} if reaction and user else None
        with self.store.connect(write=True) as db:
            if setup is not None:
                self.store.set_state(db, "tara:setup", setup)
            if preference is not None:
                self.store.set_state(db, "tara:notifications:" + thread, preference)
                current_setup = self.store.state(db, "tara:setup")
                if current_setup and current_setup.get("orchard_id") == thread:
                    self.store.set_state(db, "tara:setup", {**current_setup, "notification_preference": preference})
            if event:
                self._put(db, thread, event)
            if assistant:
                self._put(db, thread, assistant)
            if attached:
                user = {**user, "reactions": [attached]}
                db.execute("UPDATE tara_turns SET payload=? WHERE id=?", (encoded(user), user["id"]))
            if migrate:
                db.execute("UPDATE tara_turns SET thread=? WHERE thread=?", (migrate, thread))
                db.execute("UPDATE tara_requests SET thread=? WHERE thread=?", (migrate, thread))
                thread = migrate
            core = {"request_id": request_id, "outcome": "message" if assistant else "reaction" if attached else "none",
                    "user_turn": user, "assistant_turn": assistant, "reaction": attached, "event": event, "orchard_id": None if thread == "onboarding" else thread}
            if decision is not None:
                core["decision"] = decision
            db.execute("UPDATE tara_requests SET status='complete',payload=? WHERE id=?", (encoded(core), request_id))
        active(self.store.path, request_id, False)
        return {**core, "state": self.state(core["orchard_id"])}

    def reconcile(self, *, request_id, target_request_id, confirm_uncertain, orchard_id=None):
        self._identifier(request_id)
        self._identifier(target_request_id)
        if confirm_uncertain is not True or request_id == target_request_id:
            raise ValueError("Explicitly acknowledge uncertainty with a new acknowledgment identifier")
        body = {"action": "reconcile", "target_request_id": target_request_id,
                "confirm_uncertain": True, "orchard_id": orchard_id}
        old = self._existing(request_id, body)
        if old:
            return old
        now = utc(self.monitor.clock()).isoformat()
        with self.store.connect(write=True) as db:
            # Serialize competing acknowledgment IDs and completion races.
            previous = db.execute("SELECT * FROM tara_requests WHERE id=?", (request_id,)).fetchone()
            if previous:
                if previous["signature"] != fingerprint(body):
                    raise ValueError("A request identifier cannot be reused")
                core = json.loads(previous["payload"])
            else:
                target = db.execute("SELECT * FROM tara_requests WHERE id=?", (target_request_id,)).fetchone()
                if target is None:
                    raise KeyError("Request not found")
                selected = self._selection(orchard_id)
                thread = target["thread"]
                if thread != (selected or "onboarding"):
                    raise ValueError("Choose the conversation containing this request")
                if target["status"] != "pending":
                    raise RequestAlreadyCompleted("This request already has a saved outcome")
                owner = _owner_payload(target["owner"])
                if owner_status(owner, self.store.path, target_request_id) != "exited":
                    raise RequestInProgress("The original owner is active or its exit cannot be verified")
                event = self._turn(request_id + ":event", "event",
                    "The earlier request may have used ChatGPT allowance. Continuing will not retry it.", now,
                    kind="notice", data={"type": "usage_uncertain", "target_request_id": target_request_id,
                                         "acknowledged": True, "not_retried": True})
                self._put(db, thread, event)
                scope = None if thread == "onboarding" else thread
                core = {"request_id": request_id, "outcome": "none", "user_turn": None, "assistant_turn": None,
                        "reaction": None, "event": event, "orchard_id": scope, "uncertain_usage": True}
                user = db.execute("SELECT payload FROM tara_turns WHERE id=?", (target_request_id + ":user",)).fetchone()
                target_core = {**core, "request_id": target_request_id, "user_turn": json.loads(user[0]) if user else None,
                               "resolution": "acknowledged_unknown"}
                db.execute("UPDATE tara_requests SET status='acknowledged_unknown',payload=? WHERE id=?",
                           (encoded(target_core), target_request_id))
                db.execute("INSERT INTO tara_requests(id,thread,signature,created,status,payload,owner) VALUES(?,?,?,?,?,?,?)",
                           (request_id, thread, fingerprint(body), now, "complete", encoded(core), encoded(self.owner) if self.owner else None))
        return {**core, "state": self.state(core["orchard_id"])}

    def _engine_fallback(self, *, selected, setup, supplied, intent, context, weather_reply):
        """Retain useful local facts after one failed plan attempt; never retry it."""
        if not selected:
            if supplied or setup["stage"] == "location":
                return "Share your location so I can find the weather data for your farm."
            return "What are you growing?"
        if intent in LOCAL_INTENTS:
            from .local_facts import profile_answer
            return profile_answer(intent, self.state(selected))[0]
        if weather_reply is not None:
            return weather_reply[0]
        if intent in {"history", "history_gap"}:
            from .local_facts import gap_answer
            return gap_answer(self.monitor.status(selected)["summary"])[0]
        if intent == "chill_total":
            from ..farm_ops.service import _event, _facts
            from ..farm_ops.writer import message_input
            from ..farm_ops.conversational_writer import compact_text
            from ..farm_ops.writer import Writer
            status = self.monitor.status(selected)
            profile = self.store.orchard(selected)
            event = _event(profile, "chill_total", "answer", "info", self.monitor.clock(),
                           _facts(status["summary"]), "local-conversation-fallback")
            packet = message_input(profile, [event], self.monitor.clock(),
                                   question={"intent": "chill_total", "text": QUESTIONS["chill_total"]})
            return compact_text(packet, Writer(key="", model="").write(packet)["text"])
        events = self.store.events_by_ids(selected, context.get("event_ids", []))
        if events:
            from ..farm_ops.service import _explanation
            return " ".join(dict.fromkeys(_explanation(event) for event in events[:3]))
        return "ChatGPT couldn't finish that reply. Your saved readings are available in Details; try the connection again."

    def _engine_reply(self, *, finish, packet, selected, setup, supplied, intent, context, weather_reply, setup_update):
        from .engine import EngineUnavailable, respond
        try:
            result = respond(self.monitor.writer.generator, packet)
        except EngineUnavailable:
            try:
                answer = self._engine_fallback(selected=selected, setup=setup, supplied=supplied,
                    intent=intent, context=context, weather_reply=weather_reply)
            except Exception:
                # Finish the reserved turn even when a local renderer cannot
                # describe an unfamiliar event. Never retry an uncertain call.
                answer = "ChatGPT couldn't finish that reply. Your saved conversation is retained; check the connection before asking again."
            data = {"intent": "conversation", "focus": context.get("intent"),
                    "generation": "template", "generation_outcome": "engine_unavailable",
                    "source_event_ids": context.get("event_ids", [])}
            if not selected and (supplied or setup["stage"] == "location"):
                data["action"] = "location_choice"
            return finish(text=answer, data=data, setup=setup_update,
                          decision={"generation": "template", "generation_outcome": "engine_unavailable"})
        cited = [fact for fact in packet["facts"] if fact["id"] in result["fact_ids"]]
        event_ids = list(dict.fromkeys(identifier for fact in cited for identifier in fact.get("event_ids", [])))
        evidence_ids = list(dict.fromkeys(fact["evidence_id"] for fact in cited if fact.get("evidence_id")))
        data = {"intent": "conversation", "focus": result["focus"], "fact_ids": result["fact_ids"],
                "source_facts": cited, "source_event_ids": event_ids, "evidence_ids": evidence_ids,
                "history_ids": [turn["id"] for turn in packet["history"]],
                "generation": result["provider"], "generation_outcome": result["generation_outcome"],
                "response_kind": result["outcome"]}
        if not selected and (supplied or setup["stage"] == "location"):
            data["action"] = "location_choice"
        elif setup["stage"] == "notifications":
            data["action"] = "notification_choice"
        return finish(text=result["text"] if result["outcome"] in {"message", "clarify"} else None,
                      reaction=result["outcome"] == "reaction", data=data, setup=setup_update,
                      decision={"generation": result["provider"], "generation_outcome": result["generation_outcome"],
                                "response_kind": result["outcome"], "fact_ids": result["fact_ids"], "focus": result["focus"]})

    def message(self, *, request_id, text, orchard_id=None, reply_to=None):
        if not isinstance(text, str) or not text.strip() or len(text) > 1000 or any(ord(c) < 32 and c not in "\n\t" for c in text):
            raise ValueError("Provide a short message")
        body = {"text": text, "orchard_id": orchard_id, "reply_to": reply_to}
        existing = self._existing(request_id, body)
        if existing:
            return existing
        selected = self._selection(orchard_id)
        context = self._context(selected, reply_to)
        setup = self.state(selected)["setup"]
        profile = self.store.orchard(selected) if selected else {}
        known_subjects = [profile.get("name"), profile.get("crop"), profile.get("variety"), *profile.get("crops", [])]
        intent = route(text, context["intent"], known_subjects)
        # Resolve and validate an immutable reference before reserving a turn;
        # a bad cross-farm or unavailable reference must not strand a request.
        weather_reply = self._weather_reply(intent, text, selected, context, reply_to) if selected and intent in _FACT_CONTEXTS else None
        supplied = None
        if not selected and setup["stage"] == "crop" and intent not in {
                "thanks", "ack", "correction", "preference_unchanged", "notification_status",
                "notifications_off", "sms_off", "notifications_choice"}:
            supplied = crops_from(text)
            if supplied:
                from ..farm_ops.service import orchard_profile
                orchard_profile(crops=supplied["crops"], variety=supplied.get("variety"), crop_description=supplied.get("crop_description"))
        # Local actions retain their existing executor and permission checks.
        # Every other connected conversational message goes to the model with
        # the actual question/history, without the legacy vocabulary gate.
        local_actions = {"notifications_off", "sms_off", "preference_unchanged", "notifications_choice", "notification_status", "correction"}
        engine_enabled = bool(getattr(self.monitor.writer, "conversation_engine", False)
                              and getattr(self.monitor.writer, "generator", None) is not None)
        setup_update = {**setup, **supplied, "stage": "location"} if supplied else None
        engine_packet = None
        if engine_enabled and intent not in local_actions:
            from .context import context_packet
            engine_packet = context_packet(self, text=text.strip(), selected=selected, context=context,
                                           reply_to=reply_to, setup_override=setup_update)
        previous, user = self._reserve(request_id, selected or "onboarding", body, text.strip(), reply_to)
        if previous:
            return {**previous, "state": self.state(previous["orchard_id"])}
        thread = selected or "onboarding"
        finish = lambda **kwargs: self._finish(request_id, thread, user, **kwargs)
        if intent in {"notifications_off", "sms_off"}:
            if intent == "sms_off":
                if selected:
                    self.monitor.set_sms_opt_in(selected, False)
                return finish(text="SMS alerts are off." if selected else "SMS alerts haven't been enabled.", data={"action": "sms_off"})
            old = self.store.get("tara:notifications:" + thread, {})
            if selected and "browser" not in text.lower():
                self.monitor.set_sms_opt_in(selected, False)
            return finish(text="Notifications are off. Your in-chat updates stay available.",
                          preference={**old, "enabled": False, "permission": old.get("permission", "default")}, data={"action": "notifications_off"})
        if intent == "preference_unchanged":
            return finish(text="Your notification preferences are unchanged.")
        if intent == "notifications_choice":
            return finish(text="Use the notification choice to enable browser alerts while Terra is open.", data={"action": "notification_choice"})
        if intent == "notification_status":
            return finish(text="Notification preferences only change when you explicitly ask or save a choice.")
        if intent == "correction":
            return finish(text="Let's correct the farm profile. Start a new setup to save different crops or a location.", data={"action": "restart_setup"})
        if engine_packet is not None:
            return self._engine_reply(finish=finish, packet=engine_packet, selected=selected, setup=setup,
                supplied=supplied, intent=intent, context=context, weather_reply=weather_reply, setup_update=setup_update)
        if intent == "thanks":
            return finish()
        if intent == "ack":
            return finish(reaction=True)
        if intent in LOCAL_INTENTS:
            from .local_facts import profile_answer
            from .factual_writer import write_factual
            answer, facts = profile_answer(intent, self.state(selected))
            result = write_factual(intent, facts, answer, self.monitor.writer, mode=profile.get("mode", "current"))
            return finish(text=result["text"], data={"intent": intent, "local_facts": facts,
                          "generation": result["provider"], "generation_outcome": result["outcome"]})
        if not selected and setup["stage"] == "crop":
            if supplied:
                return finish(text="Where are you growing? Share your location.",
                              setup={**setup, **supplied, "stage": "location"}, data={"action": "location_choice"})
            if intent == "off_topic":
                return finish(reaction=True)
            return finish(text=PROMPT)
        if not selected:
            return finish(text="Share your location to start monitoring.", data={"action": "location_choice"})
        if intent in _FACT_CONTEXTS:
            from .factual_writer import write_factual
            answer, facts = weather_reply
            result = write_factual(intent, facts, answer, self.monitor.writer, mode=profile.get("mode", "current"))
            return finish(text=result["text"], data={"intent": intent, "local_facts": facts,
                          "generation": result["provider"], "generation_outcome": result["outcome"]})
        if intent in {"off_topic"}:
            return finish(reaction=True)
        if intent in {"needs_context", "clarify"}:
            return finish(text="Which farm note or monitored condition do you mean?")
        if intent == "cause_limit":
            return finish(text="I don't have enough information to explain the cause yet.")
        if intent == "setup_help":
            return finish(text="Your farm is saved. You can change farms or start another setup.")
        if intent == "history_gap":
            from .local_facts import gap_answer
            from ..farm_ops.service import _event, _facts
            from ..farm_ops.writer import message_input
            status = self.monitor.status(selected)
            answer, facts = gap_answer(status["summary"])
            event = _event(profile, "history", "answer", "info", self.monitor.clock(), _facts(status["summary"]), request_id)
            packet = message_input(profile, [event], self.monitor.clock(), question={"intent": "history", "text": "How far behind is the comparable chill pace?"})
            packet["calculation"] = facts
            saved = self.store.publish(selected, packet, {"text": answer, "provider": "template", "outcome": "local_facts"},
                                       context["event_ids"], self.monitor.clock(), question_id="tara-" + fingerprint(request_id)[:64])
            return finish(text=answer, data={"intent": "history", "local_facts": facts, "message_id": saved["id"], "source_event_ids": context["event_ids"]})
        if intent in {"unsupported", "biological_limit"}:
            return finish(text="Chill comparisons alone can't predict bloom or crop damage." if intent == "biological_limit"
                          else "I can explain monitored chill and weather coverage; forecasts and farm treatments aren't supported.")
        if intent not in QUESTIONS:
            return finish(text="Which monitored condition do you mean?")
        # Only canonical in-domain questions enter the writer. Raw text stays local.
        try:
            source_event_ids = context["event_ids"]
            if intent == "explain_alert" and not source_event_ids:
                latest = [event for event in self.store.events(selected) if event["type"] != "welcome"]
                source_event_ids = [latest[-1]["id"]] if latest else []
            if intent == "explain_alert" and source_event_ids:
                from ..farm_ops.service import _event, _explanation
                from ..farm_ops.writer import message_input
                events = self.store.events_by_ids(selected, source_event_ids)
                profile = self.store.orchard(selected)
                explanations = []
                for source in events:
                    facts = source.get("facts", {})
                    observed = facts.get("observation_time") or facts.get("data_through") or source.get("occurred_at")
                    if not observed:
                        raise ValueError("The source alert has no recorded evidence time")
                    if source["type"] in {"weather_discrepancy", "weather_discrepancy_recovered",
                                          "weather_data_limited", "weather_data_recovered"}:
                        # Preserve the saved station, variable and lifecycle for
                        # the fact validator instead of discarding that evidence
                        # in a generic explanation. The raw question stays local.
                        explanations.append({**source, "facts": {**facts, "observation_time": observed}})
                        continue
                    explanations.append(_event(profile, "explain_alert", "answer", source["severity"],
                        source.get("occurred_at") or observed,
                        {"explanation": _explanation(source), "observation_time": observed,
                         "data_through": facts.get("data_through")},
                        {"request_id": request_id, "source_event_id": source["id"]}, source.get("evidence_id")))
                packet = message_input(profile, explanations, self.monitor.clock(), question={"intent": intent, "text": QUESTIONS[intent]})
                # Different-year evidence must not inherit this year's date in
                # either a single reply or mixed-date clause framing.
                from ..farm_ops.season import PST
                year = utc(packet["as_of"]).astimezone(PST).year
                for event in packet["events"]:
                    source_year = utc(event["facts"]["observation_time"]).astimezone(PST).year
                    if source_year != year:
                        day, clock = event["display_time"].split(",", 1)
                        event["display_time"] = f"{day} {source_year},{clock}"
                answer = self.store.publish(selected, packet, self.monitor.writer.write(packet), source_event_ids, self.monitor.clock(),
                                            question_id="tara-" + fingerprint(request_id)[:64])
            else:
                answer = self.monitor.ask(selected, QUESTIONS[intent], request_id="tara-" + fingerprint(request_id)[:64])
        except Exception:
            # Do not automatically retry an uncertain provider request under a new ID.
            return finish(text="I couldn't finish that reply. The request is saved; check the connection before asking again.", data={"intent": intent, "generation_outcome": "unavailable"})
        return finish(text=answer["text"], data={"intent": intent, "message_id": answer["id"],
                      "source_event_ids": source_event_ids if intent == "explain_alert" else [],
                      "generation_outcome": answer.get("generation_outcome"), "generation": answer.get("generation")})

    def action(self, *, request_id, action, location=None, notifications=None, orchard_id=None, device_location=None):
        body = {"action": action, "location": location, "notifications": notifications, "orchard_id": orchard_id}
        if action == "device_location":
            from .location import device_coordinates
            body["device_location"] = device_coordinates(device_location)
        existing = self._existing(request_id, body)
        if existing:
            return existing
        if action not in {"restart", "location", "notifications", "device_location"}:
            raise ValueError("Unsupported setup action")
        expected_setup, device_profile = None, None
        if action == "device_location":
            if location is not None or notifications is not None or orchard_id is not None:
                raise ValueError("Device setup accepts only the actual device location")
            setup, expected_setup = self._device_setup_snapshot()
            if setup["stage"] != "location":
                raise ValueError("Supply your crops before sharing a location")
            resolved = self.location_resolver.resolve(body["device_location"], now=self.monitor.clock())
            location = {**resolved, "name": "Shared location", "source": "device", "confirmed": True}
            from ..farm_ops.service import orchard_profile
            device_profile = orchard_profile("Shared location", orchard_id="farm-" + fingerprint(request_id)[:20],
                lat=resolved["lat"], lon=resolved["lon"], elevation_ft=resolved["elevation_ft"], crops=setup["crops"],
                variety=setup.get("variety"), crop_description=setup.get("crop_description"),
                location_metadata={"location_source": "device geolocation; ground elevation from USGS EPQS",
                                   "location_accuracy_m": resolved["accuracy_m"], "elevation_source": resolved["elevation_source"]})
        if action == "location":
            if not isinstance(location, dict) or location.get("confirmed") is not True:
                raise ValueError("Confirm the farm location before saving it")
            if location.get("source") not in {"manual", "device", "preset"}:
                raise ValueError("Provide the actual location source")
            from ..farm_ops.service import orchard_profile
            # Validate all supplied geometry before reserving a request.
            current = self._saved_setup() or empty_setup()
            orchard_profile(location.get("name", "My farm"), lat=location["lat"], lon=location["lon"], elevation_ft=location["elevation_ft"],
                            crops=current["crops"], variety=current.get("variety"), crop_description=current.get("crop_description"))
        if action == "notifications":
            if (not isinstance(notifications, dict) or not isinstance(notifications.get("enabled"), bool)
                    or notifications.get("permission") not in {"granted", "denied", "default", "unsupported"}):
                raise ValueError("Provide an explicit notification choice")
            if notifications["enabled"] and (notifications["permission"] != "granted" or self.notification_capability != "browser_open"):
                raise ValueError("Browser permission and supported open-browser delivery are required")
        setup = setup if action == "device_location" else self._saved_setup() or empty_setup()
        saved_setup = setup
        if action == "notifications" and orchard_id:
            self.store.orchard(orchard_id)
            setup = self.state(orchard_id)["setup"]
        if action in {"location", "device_location"} and setup["stage"] != "location":
            raise ValueError("Supply your crops before confirming a location")
        if action == "notifications" and not setup.get("orchard_id"):
            raise ValueError("Confirm your farm before choosing notifications")
        thread = "onboarding" if action in {"restart", "location", "device_location"} else setup["orchard_id"]
        previous, _ = self._reserve(request_id, thread, body, expected_setup=expected_setup)
        if previous:
            return {**previous, "state": self.state(previous["orchard_id"])}
        if action == "restart":
            return self._finish(request_id, thread, None, text=PROMPT,
                                data={"action": "initial_prompt"}, setup=empty_setup())
        now = utc(self.monitor.clock()).isoformat()
        if action in {"location", "device_location"}:
            p = self.monitor.create_orchard(location.get("name", "My farm"), orchard_id="farm-" + fingerprint(request_id)[:20],
                lat=location["lat"], lon=location["lon"], elevation_ft=location["elevation_ft"], crops=setup["crops"], variety=setup.get("variety"),
                crop_description=setup.get("crop_description"),
                **({"location_metadata": {key: device_profile[key] for key in
                    ("location_source", "location_accuracy_m", "elevation_source")}} if device_profile else {}))
            event = self._turn(request_id + ":event", "event", "Location shared" if device_profile else p["name"], now, kind="location", data={**location, "orchard_id": p["id"], "saved": True})
            return self._finish(request_id, thread, None, text="Want a heads-up when something needs attention? Browser notifications work while Terra is open.",
                event=event, setup={**setup, "orchard_id": p["id"], "location": location, "stage": "notifications"}, migrate=p["id"], data={"action": "notification_choice"})
        event = self._turn(request_id + ":event", "event", "Browser notifications enabled" if notifications["enabled"] else "In-chat updates only", now,
                           kind="notification", data={**notifications, "saved": True, "scope": "while_open"})
        monitoring = self.state(setup["orchard_id"])["monitoring"]
        details = monitoring.get("details") or {}
        if details.get("orchard", {}).get("mode") != "current":
            text = "Your farm is saved. These saved replay readings aren't live monitoring."
        elif monitoring["status"] == "ready":
            text = "You're set. I'll flag meaningful changes here."
        elif details.get("comparison_available"):
            text = "Your farm is saved. Saved weather data is usable, but the monitor isn't running."
        elif details.get("worker", {}).get("heartbeat_status") == "fresh":
            text = "Your farm is saved. I'm waiting for usable weather data."
        else:
            reason = monitoring["reason"]
            text = "Your farm is saved. " + ("I'm " + reason[0].lower() + reason[1:] if reason.startswith("Waiting ") else reason)
        return self._finish(request_id, thread, None, text=text, event=event, preference=notifications,
                            setup=({**setup, "stage": "complete", "notification_preference": notifications}
                                   if saved_setup.get("orchard_id") == thread else None))
