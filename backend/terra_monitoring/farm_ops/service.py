"""A small deterministic seasonal watch, not an autonomous agronomy decision maker."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import re
import uuid

from .season import METHOD, PST, completed_day_summaries, eligible, summarize
from .store import Store, fingerprint, utc
from .writer import Writer, message_input


def orchard_profile(name="West Orchard", *, orchard_id=None, lat=38.6, lon=-121.84,
                    elevation_ft=70.0, mode="current"):
    if not isinstance(name, str) or not name.strip() or len(name) > 80 or any(ord(c) < 32 for c in name):
        raise ValueError("Provide a short orchard name")
    for value, low, high in ((lat, -90, 90), (lon, -180, 180), (elevation_ft, -1500, 20000)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError("Provide finite coordinates and a supplied or explicitly configured elevation")
    orchard_id = orchard_id or uuid.uuid4().hex[:12]
    if not isinstance(orchard_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", orchard_id):
        raise ValueError("Invalid orchard ID")
    if mode not in {"current", "replay", "injected"}:
        raise ValueError("Invalid monitoring mode")
    geometry = {"lat": float(lat), "lon": float(lon), "elevation_ft": float(elevation_ft)}
    demo_location = geometry == {"lat": 38.6, "lon": -121.84, "elevation_ft": 70.0}
    return {"id": orchard_id, "name": name.strip(), "crop": "almond", "variety": "Nonpareil",
            **geometry, "geometry_key": fingerprint(geometry), "mode": mode, "chill_method": METHOD,
            "demo_location": demo_location,
            "location_source": "configured Davis-area demonstration pin" if demo_location else "caller-supplied orchard pin and elevation",
            "target": {"hours": 400.0, "kind": "configured_reference", "agronomic_requirement_verified": False,
                       "source": "https://fps.ucdavis.edu/treedetails.cfm?hidepics=yes&v=1064",
                       "note": "Published cultivar reference; counting-method equivalence has not been verified."}}


def _facts(summary):
    keys = ("hours", "coverage", "data_through", "cutoff", "season_start", "status", "reference_count", "percentile20_hours",
            "historical_median_hours", "complete")
    result = {key: summary.get(key) for key in keys}
    for key, value in result.items():
        if isinstance(value, float):
            result[key] = round(value, 3)
    return result


def _event(profile, kind, transition, severity, occurred_at, facts, key, evidence_id=None):
    identity = fingerprint({"orchard": profile["id"], "mode": profile["mode"], "kind": kind,
                            "transition": transition, "key": key})
    category = "data_health" if kind.startswith("monitoring_") else "chill_pace" if kind.startswith("chill_pace") else kind
    episode = fingerprint({"orchard": profile["id"], "mode": profile["mode"], "category": category, "key": key})
    return {"id": identity, "episode_id": episode, "mode": profile["mode"], "category": category,
            "type": kind, "transition": transition, "severity": severity,
            "occurred_at": utc(occurred_at).isoformat(), "facts": facts, "evidence_id": evidence_id}


def _recovery_streak(rows, reading, unavailable_through):
    """Count the latest causal, same-contract run; revisions and gaps break it."""
    stamp = utc(reading["valid_utc"])
    boundary = utc(unavailable_through) if unavailable_through else None
    count = 0
    # Store.hours_in supplies unique ascending hours. The common case reads
    # only the final three rows, rather than re-indexing full seasonal history.
    for row in reversed(rows):
        candidate = utc(row["valid_utc"])
        if candidate > stamp:
            continue
        if (candidate != stamp or (boundary is not None and stamp <= boundary) or not eligible(row)
                or any(row.get(key) != reading.get(key) for key in ("geometry_key", "method_version", "qc_reference"))):
            break
        count += 1
        if count == 3:
            break
        stamp -= timedelta(hours=1)
    return count


class Monitor:
    def __init__(self, store=None, *, source=None, writer=None, clock=None, delivery=None):
        self.store = store or Store()
        self.source = source
        self.writer = writer or Writer()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.delivery = delivery

    def create_orchard(self, name="West Orchard", *, sms_opt_in=False, **options):
        profile = orchard_profile(name, **options)
        if not isinstance(sms_opt_in, bool) or (sms_opt_in and profile["mode"] != "current"):
            raise ValueError("SMS opt-in needs explicit current-mode consent")
        return self.store.save_orchard(profile, sms_opt_in=sms_opt_in, now=self.clock())

    @staticmethod
    def _reading(profile, packet, now):
        if packet.get("schema_version") != "terra-location-assessment-v1" or packet.get("data_mode") != profile["mode"]:
            raise ValueError("Assessment schema/mode does not match the saved orchard")
        query, estimate = packet["query"], packet["location_estimate"]
        for key in ("lat", "lon", "elevation_ft"):
            if isinstance(query.get(key), bool) or not isinstance(query.get(key), (int, float)) or not math.isclose(query[key], profile[key], abs_tol=1e-8):
                raise ValueError("Assessment geometry differs from the saved orchard")
            value = estimate.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isclose(value, profile[key], abs_tol=1e-8):
                raise ValueError("The estimated location does not match the orchard pin")
        stamp, as_of = utc(query["valid_utc"]), utc(query["as_of_utc"])
        if stamp.minute or stamp.second or stamp.microsecond or not stamp <= as_of <= utc(now):
            raise ValueError("Assessment clocks must be aware causal hour endings")
        if utc(estimate["valid_utc"]) != stamp or estimate.get("unit") != "degF":
            raise ValueError("Estimate units/time must match the assessment")
        status, value = estimate.get("status"), estimate.get("temperature")
        if status not in {"ok", "stale", "insufficient_data"}:
            raise ValueError("Unknown estimate availability")
        finite = isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)
        usable = status in {"ok", "stale"} and finite
        if usable and (estimate.get("n_used", 0) < 3 or not -10 <= value <= 125):
            raise ValueError("Usable estimates need supported peers and temperature units/range")
        if status == "ok" and not usable:
            raise ValueError("An available estimate must have a finite temperature")
        provenance = packet["provenance"]
        method = estimate.get("method_version")
        reference = provenance.get("qc_reference_id")
        if not isinstance(method, str) or not method or not isinstance(reference, str) or not reference:
            raise ValueError("Estimates need exact estimator and QC identities")
        from terra_qc.reference import REFERENCE_ID
        if reference != REFERENCE_ID:
            raise ValueError("The frozen QC reference cannot change implicitly")
        contract = fingerprint({"estimator": method, "qc": reference})
        return {"valid_utc": stamp.isoformat(), "temperature_f": float(value) if usable else None,
                "usable": usable, "status": status, "status_reason": estimate.get("status_reason", ""),
                "method_version": contract, "estimator_method_version": method, "qc_reference": reference,
                "geometry_key": profile["geometry_key"], "as_of": as_of.isoformat()}

    def apply(self, orchard_id, collection, *, now=None):
        """Persist observations and event decisions together; wording/publication follows separately."""
        now = utc(now or self.clock())
        profile = self.store.orchard(orchard_id)
        latest = collection["latest"]
        history = collection.get("history", [latest])
        if not history or len(history) > 73:
            raise ValueError("Expected a bounded recent collection")
        validated = [(packet, self._reading(profile, packet, now)) for packet in history]
        newest = self._reading(profile, latest, now)
        with self.store.connect(write=True) as db:
            previous = self.store.state(db, "watch:" + orchard_id, {})
            if previous.get("last_check") and utc(previous["last_check"]) > now:
                raise ValueError("Cannot rewind a persistent watch; use another replay orchard")
            if previous.get("method_version") and previous["method_version"] != newest["method_version"]:
                # Old versions remain auditable but cannot supply today's history or recovery streak.
                previous.pop("health_good_since", None)
                previous.pop("health_good_count", None)
            for packet, reading in validated:
                reading["evidence_id"] = self.store.retain(db, packet)
                self.store.put_hour(db, orchard_id, reading)
            evidence_id = self.store.retain(db, latest)
            rows = self.store.hours_in(db, orchard_id)
            snapshot = summarize(rows, now, profile["geometry_key"], newest["method_version"])
            available = newest["status"] == "ok" and now - utc(newest["valid_utc"]) <= timedelta(hours=6)
            events = self._decide(profile, previous, rows, snapshot, newest, available, now, evidence_id)
            previous.update(last_check=now.isoformat(), last_observation=newest["valid_utc"],
                            method_version=newest["method_version"], latest_evidence_id=evidence_id,
                            comparison_available=available, latest_status=newest["status"], summary=snapshot)
            self.store.set_state(db, "watch:" + orchard_id, previous)
            for event in events:
                self.store.add_event(db, orchard_id, event, now)
        return {"orchard_id": orchard_id, "new_events": len(events), "summary": snapshot,
                "comparison_available": available, "data_through": newest["valid_utc"]}

    def _decide(self, profile, state, rows, summary, reading, available, now, evidence_id):
        events, stamp = [], utc(reading["valid_utc"])
        if not state.get("welcomed"):
            events.append(_event(profile, "welcome", "initial", "info", stamp,
                {**_facts(summary), "comparison_available": available}, "once", evidence_id))
            state["welcomed"] = True
        incident = state.get("health_incident")
        if not available:
            state["health_good_count"], state["health_good_since"] = 0, None
            old_boundary = utc(state["health_unavailable_through"]) if state.get("health_unavailable_through") else stamp
            state["health_unavailable_through"] = max(old_boundary, stamp).isoformat()
            if incident is None:
                incident = {"id": fingerprint({"time": now.isoformat(), "orchard": profile["id"]}),
                            "opened_at": now.isoformat(), "escalated": False}
                state["health_incident"] = incident
                events.append(_event(profile, "monitoring_limited", "opening", "moderate", now,
                    {"reason": reading.get("status_reason") or reading["status"], "data_through": reading["valid_utc"]}, incident["id"], evidence_id))
            elif not incident["escalated"] and now - utc(incident["opened_at"]) >= timedelta(hours=6):
                incident["escalated"] = True
                events.append(_event(profile, "monitoring_limited", "escalation", "strong", now,
                    {"reason": "unavailable_for_six_hours", "data_through": reading["valid_utc"]}, incident["id"], evidence_id))
        elif incident is not None:
            boundary = state.get("health_unavailable_through")
            if boundary is None:
                # Existing journals predate the explicit outage boundary. Retain
                # their last counted run, or start after the last unavailable hour.
                last_good = state.get("health_good_since")
                boundary = ((utc(last_good) - timedelta(hours=state.get("health_good_count", 0))).isoformat()
                            if last_good else state.get("last_observation"))
                state["health_unavailable_through"] = boundary
            count = _recovery_streak(rows, reading, boundary)
            state["health_good_since"], state["health_good_count"] = stamp.isoformat() if count else None, count
            if count >= 3:
                recovered = _event(profile, "monitoring_recovered", "recovery", "info", stamp,
                    {"consecutive_valid_readings": 3, "data_through": reading["valid_utc"]}, incident["id"], evidence_id)
                events.append(recovered)
                state["last_health_recovery"] = recovered["id"]
                state["health_incident"] = None
                state.pop("health_unavailable_through", None)
        # A station's original anomaly stays in evidence if the orchard estimate is usable.
        events.extend(self._season_events(profile, state, rows, summary, reading, available, now, evidence_id))
        return events

    def _season_events(self, profile, state, rows, summary, reading, available, now, evidence_id):
        events = []
        if not summary["active_season"]:
            return events
        daily = completed_day_summaries(rows, now, profile["geometry_key"], reading["method_version"])
        if daily[-1]["cutoff"] != state.get("last_completed_day"):
            state["last_completed_day"] = daily[-1]["cutoff"]
            pace = state.get("pace_incident")
            if pace and pace["season"] != summary["season_start"]:
                state["pace_incident"], pace = None, None
            if all(d["status"] == "below20" for d in daily[-2:]) and pace is None:
                pace = {"id": daily[-1]["completed_day"], "season": summary["season_start"]}
                state["pace_incident"] = pace
                events.append(_event(profile, "chill_pace", "opening", "moderate", daily[-1]["cutoff"],
                    _facts(daily[-1]), pace["id"], evidence_id))
            elif pace and all(d["status"] == "typical" for d in daily):
                state["pace_incident"] = None
                recovered = _event(profile, "chill_pace_recovered", "recovery", "info", daily[-1]["cutoff"],
                    {**_facts(daily[-1]), "consecutive_valid_readings": 3}, pace["id"], evidence_id)
                events.append(recovered)
                state["last_pace_recovery"] = recovered["id"]
        target = profile["target"]
        if summary["complete"] and summary["hours"] is not None and summary["hours"] >= target["hours"] and available:
            key = summary["season_start"] + ":" + METHOD + ":" + str(target["hours"])
            if state.get("last_reference") != key:
                state["last_reference"] = key
                events.append(_event(profile, "chill_reference", "milestone", "info", summary["cutoff"],
                    {**_facts(summary), "reference_hours": target["hours"], "target_kind": target["kind"]}, key, evidence_id))
        return events

    def _source_failure(self, orchard_id, now):
        profile = self.store.orchard(orchard_id)
        # A transport failure never overwrites a retained hourly estimate.
        with self.store.connect(write=True) as db:
            state = self.store.state(db, "watch:" + orchard_id, {})
            if not state.get("welcomed"):
                self.store.add_event(db, orchard_id, _event(profile, "welcome", "initial", "info", now,
                    {"hours": None, "complete": False, "comparison_available": False}, "once"), now)
                state["welcomed"] = True
            state["source_error"] = {"checked_at": now.isoformat(), "code": "provider_unavailable"}
            latest = state.get("last_observation")
            usable_cache = latest and now - utc(latest) <= timedelta(hours=6) and state.get("comparison_available")
            if not usable_cache:
                if latest:
                    old_boundary = utc(state["health_unavailable_through"]) if state.get("health_unavailable_through") else utc(latest)
                    state["health_unavailable_through"] = max(old_boundary, utc(latest)).isoformat()
                incident = state.get("health_incident")
                if incident is None:
                    incident = {"id": fingerprint({"orchard": orchard_id, "time": now.isoformat()}),
                                "opened_at": now.isoformat(), "escalated": False}
                    state["health_incident"] = incident
                    self.store.add_event(db, orchard_id, _event(profile, "monitoring_limited", "opening", "moderate", now,
                        {"reason": "provider_unavailable"}, incident["id"]), now)
                elif not incident["escalated"] and now - utc(incident["opened_at"]) >= timedelta(hours=6):
                    incident["escalated"] = True
                    self.store.add_event(db, orchard_id, _event(profile, "monitoring_limited", "escalation", "strong", now,
                        {"reason": "unavailable_for_six_hours"}, incident["id"]), now)
                state.update(comparison_available=False, health_good_count=0, health_good_since=None)
            state["last_check"] = now.isoformat()
            if state.get("method_version"):
                rows = self.store.hours_in(db, orchard_id)
                summary = summarize(rows, now, profile["geometry_key"], state["method_version"])
                state["summary"] = summary
                for event in self._season_events(profile, state, rows, summary,
                    {"method_version": state["method_version"]}, bool(usable_cache), now, None):
                    self.store.add_event(db, orchard_id, event, now)
            self.store.set_state(db, "watch:" + orchard_id, state)

    def flush(self, orchard_id, *, now=None):
        now = utc(now or self.clock())
        profile = self.store.orchard(orchard_id)
        owner = uuid.uuid4().hex
        if not self.store.acquire("publish:" + orchard_id, owner, now, seconds=90):
            return None
        try:
            pending = self.store.pending(orchard_id)
            state = self.store.get("watch:" + orchard_id, {})
            keep = []
            # Old openings that already recovered are summarized by the recovery, not replayed as fresh.
            for event in pending:
                health_open = event["type"] == "monitoring_limited"
                pace_open = event["type"] == "chill_pace"
                obsolete = (health_open and not state.get("health_incident")) or (pace_open and not state.get("pace_incident"))
                if not obsolete:
                    keep.append(event)
            if not pending:
                return None
            if not keep:
                with self.store.connect(write=True) as db:
                    for event in pending:
                        db.execute("UPDATE events SET published=1 WHERE id=?", (event["id"],))
                return None
            # One most recent event per category prevents an unsent opening+escalation double text.
            grouped = {}
            for event in keep:
                old = grouped.get(event["type"])
                if old is None or event["transition"] == "escalation" or event["occurred_at"] > old["occurred_at"]:
                    grouped[event["type"]] = event
            packet = message_input(profile, list(grouped.values()), now.isoformat())
            result = self.writer.write(packet)
            if not self.store.acquire("publish:" + orchard_id, owner, max(utc(self.clock()), now) if profile["mode"] == "current" else now, seconds=90):
                return None
            return self.store.publish(orchard_id, packet, result, [e["id"] for e in grouped.values()], now,
                                      consumed_ids=[e["id"] for e in pending],
                                      created_at=max(e.get("created_at", e["occurred_at"]) for e in grouped.values()))
        finally:
            self.store.release("publish:" + orchard_id, owner)

    def check(self):
        owner, now = uuid.uuid4().hex, utc(self.clock())
        if not self.store.acquire("worker", owner, now, seconds=300):
            return {"status": "already_running"}
        results = []
        try:
            for profile in self.store.orchards():
                if profile["mode"] != "current":
                    continue
                if self.source is None:
                    raise ValueError("A live source must be configured for monitoring")
                try:
                    collection = self.source.collect(profile)
                    if not self.store.acquire("worker", owner, self.clock(), seconds=300):
                        return {"status": "lease_lost"}
                    result = self.apply(profile["id"], collection, now=self.clock())
                    with self.store.connect(write=True) as db:
                        state = self.store.state(db, "watch:" + profile["id"], {})
                        state.pop("source_error", None)
                        self.store.set_state(db, "watch:" + profile["id"], state)
                except Exception:
                    self._source_failure(profile["id"], utc(self.clock()))
                    result = {"orchard_id": profile["id"], "status": "source_unavailable"}
                self.flush(profile["id"])
                results.append(result)
                if not self.store.acquire("worker", owner, self.clock(), seconds=300):
                    raise RuntimeError("Worker lease was lost")
            with self.store.connect(write=True) as db:
                self.store.set_state(db, "worker", {"last_checked": utc(self.clock()).isoformat(), "orchards": results})
            self.drain_notifications(lease_owner=owner)
            return {"status": "checked", "orchards": results}
        finally:
            self.store.release("worker", owner)

    def status(self, orchard_id, *, now=None):
        profile = self.store.orchard(orchard_id)
        state = self.store.get("watch:" + orchard_id, {})
        now = utc(now or self.clock())
        method = state.get("method_version", "no-estimator-history")
        summary = summarize(self.store.hours(orchard_id), now, profile["geometry_key"], method)
        available = bool(state.get("comparison_available") and state.get("last_observation")
                         and now - utc(state["last_observation"]) <= timedelta(hours=6))
        return {"orchard": profile, "summary": summary, "comparison_available": available,
                "last_checked": state.get("last_check"), "data_through": state.get("last_observation"),
                "source_error": state.get("source_error"), "worker": self.worker_status(),
                "pending_events": len(self.store.pending(orchard_id)), "transport": "local_inbox"}

    def ask(self, orchard_id, question, *, request_id=None):
        if not isinstance(question, str) or not question.strip() or len(question) > 500:
            raise ValueError("Provide a short question")
        now, profile = utc(self.clock()), self.store.orchard(orchard_id)
        if request_id is not None:
            if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", request_id):
                raise ValueError("Invalid question request identity")
            message_id = fingerprint({"orchard": orchard_id, "events": [], "question": request_id})
            existing = self.store.reserve_question(orchard_id, message_id, question.strip(), now)
            if existing:
                return existing
        text = question.lower()
        status = self.status(orchard_id, now=now)
        if re.search(r"gdd|gdh|forecast|next week|next two|bloom|spray|irrigat", text):
            intent, kind, facts = "unsupported", "unsupported", {}
        elif re.search(r"explain|alert|warning|flag", text):
            intent, kind = "explain_alert", "explain_alert"
            events = [e for e in self.store.events(orchard_id) if e["type"] != "welcome"]
            event = events[-1] if events else None
            facts = {"explanation": "no alert has been recorded"}
            if event:
                facts = {"explanation": _explanation(event), "observation_time": event["occurred_at"]}
        elif re.search(r"station|neighbor|health|weather|normal|coverage", text):
            intent, kind, facts = "station_health", "station_health", {"comparison_available": status["comparison_available"],
                "data_through": status["data_through"]}
        elif re.search(r"histor|compar|past|last year|usual|pace", text):
            intent, kind, facts = "history", "history", _facts(status["summary"])
        elif re.search(r"chill|status|accumul|how much|total", text):
            intent, kind, facts = "chill_total", "chill_total", _facts(status["summary"])
        else:
            intent, kind, facts = "unsupported", "unsupported", {}
        event = _event(profile, kind, "answer", "info", now, facts, uuid.uuid4().hex)
        packet = message_input(profile, [event], now.isoformat(), question={"intent": intent, "text": question.strip()})
        # Idempotency keys are local request identities, not a receipt that a phone received anything.
        question_id = request_id or uuid.uuid4().hex
        return self.store.publish(orchard_id, packet, self.writer.write(packet), [], now, question_id=question_id)

    def set_sms_opt_in(self, orchard_id, enabled):
        profile = self.store.orchard(orchard_id)
        if not isinstance(enabled, bool) or (enabled and profile["mode"] != "current"):
            raise ValueError("Only an explicit current-mode subscription can enable SMS")
        old = self.store.get("sms-opt-in:" + orchard_id, {})
        # Repeated preference saves cannot move the boundary and re-enable an old backlog.
        if old.get("enabled") is enabled:
            return old
        record = {"enabled": enabled, "changed_at": utc(self.clock()).isoformat(),
                  "channel": "sms", "recipient_binding": "private transport configuration"}
        with self.store.connect(write=True) as db:
            self.store.set_state(db, "sms-opt-in:" + orchard_id, record)
        return record

    def _notification(self, message, profile, state, now):
        """Trusted immutable text plus freshly rechecked eligibility for one transport boundary."""
        events = self.store.events_by_ids(profile["id"], message["event_ids"])
        events.sort(key=lambda e: {"strong": 0, "moderate": 1, "info": 2}[e["severity"]])
        subscription = self.store.get("sms-opt-in:" + profile["id"], {})
        created, checked = utc(message["created_at"]), utc(state.get("last_check", message["created_at"]))
        expires = created + timedelta(hours=6)
        reason = "eligible"
        if profile["mode"] != "current": reason = "demo_mode"
        elif not subscription.get("enabled"): reason = "not_opted_in"
        elif created < utc(subscription["changed_at"]): reason = "before_opt_in"
        elif not created <= checked <= now or now - checked > timedelta(minutes=5): reason = "monitor_check_not_fresh"
        elif now >= expires: reason = "expired"
        elif not events: reason = "no_proactive_events"
        if reason == "eligible":
            for event in events:
                kind = event["type"]
                if kind == "monitoring_limited":
                    incident = state.get("health_incident")
                    if not incident or (incident.get("escalated") and event["severity"] != "strong"):
                        reason = "superseded_incident"
                    elif event["episode_id"] != fingerprint({"orchard": profile["id"], "mode": profile["mode"],
                                                            "category": "data_health", "key": incident["id"]}):
                        reason = "superseded_incident"
                elif kind == "monitoring_recovered" and (state.get("health_incident") or event["id"] != state.get("last_health_recovery")):
                    reason = "superseded_recovery"
                elif kind == "chill_pace" and not state.get("pace_incident"):
                    reason = "superseded_pace"
                elif kind == "chill_pace_recovered" and (state.get("pace_incident") or event["id"] != state.get("last_pace_recovery")):
                    reason = "superseded_pace_recovery"
                elif kind == "chill_reference":
                    summary = state.get("summary", {})
                    if not summary.get("complete") or (summary.get("hours") or 0) < event["facts"]["reference_hours"]:
                        reason = "revised_reference"
        primary = events[0] if events else None
        category = "data_health" if primary and primary["category"] == "data_health" else "chill"
        observation = state.get("last_observation")
        seasonal = [e for e in events if e["type"] in {"chill_pace", "chill_pace_recovered", "chill_reference"}]
        if reason == "eligible" and seasonal:
            method = state.get("method_version", "unknown")
            summary = summarize(self.store.hours(profile["id"]), now, profile["geometry_key"], method)
            daily = completed_day_summaries(self.store.hours(profile["id"]), now, profile["geometry_key"], method)
            for event in seasonal:
                if not summary["active_season"]:
                    reason = "outside_active_season"
                elif event["type"] in {"chill_pace", "chill_pace_recovered"} and event["facts"].get("cutoff") != daily[-1]["cutoff"]:
                    reason = "completed_day_changed"
                elif event["type"] == "chill_reference" and (not summary["complete"] or (summary["hours"] or 0) < event["facts"]["reference_hours"]):
                    reason = "reference_no_longer_supported"
        return {"schema_version": "terra-notification-v1", "event_id": message["id"],
                "source_event_ids": message["event_ids"], "episode_ids": sorted({e["episode_id"] for e in events}),
                "orchard_id": profile["id"], "data_mode": profile["mode"], "kind": "orchard_update",
                "category": category, "transition": events[0]["transition"] if len(events) == 1 else "combined",
                "severity": events[0]["severity"] if events else "info", "text": message["text"],
                "created_at_utc": created.isoformat(), "checked_at_utc": checked.isoformat(),
                "observation_time_utc": observation, "expires_at_utc": expires.isoformat(),
                "evidence_cutoff_utc": next((e["facts"].get("cutoff") for e in seasonal), observation),
                "eligibility": {"eligible": reason == "eligible", "reason": reason}}

    def notifications_pending(self):
        now = utc(self.clock())
        result = []
        for profile in self.store.orchards():
            state = self.store.get("watch:" + profile["id"], {})
            for message in reversed(self.store.messages(profile["id"], limit=1000)):
                receipt = self.store.get("transport:" + message["id"], {})
                if message.get("kind") != "proactive" or receipt.get("status") in {"accepted", "provider_accepted", "delivered", "read", "uncertain", "suppressed", "expired", "failed", "failed_permanent", "undelivered", "canceled"}:
                    continue
                envelope = self._notification(message, profile, state, now)
                if envelope["eligibility"]["eligible"]:
                    result.append(envelope)
        return result

    def delivery_status(self, message):
        saved = self.store.get("transport:" + message["id"])
        if saved:
            return saved
        if message.get("kind") != "proactive":
            return {"status": "not_requested", "reason": "local_question_answer"}
        profile = self.store.orchard(message["orchard_id"])
        state = self.store.get("watch:" + profile["id"], {})
        envelope = self._notification(message, profile, state, utc(self.clock()))
        allowed = envelope["eligibility"]["eligible"]
        reason = envelope["eligibility"]["reason"]
        return {"status": "pending" if allowed else "not_requested" if reason in {"demo_mode", "not_opted_in", "before_opt_in"} else "withheld",
                "reason": reason}

    def record_transport_receipt(self, notification_id, receipt):
        if self.store.message(notification_id) is None:
            raise KeyError("Notification not found")
        if not isinstance(receipt, dict) or receipt.get("source_event_id", notification_id) != notification_id:
            raise ValueError("Transport receipt belongs to another notification")
        # The transport owns attempts and private recipient binding; retain only safe receipt fields here.
        allowed = {"event_id", "source_event_id", "status", "provider_status", "attempts", "retryable",
                   "accepted_at", "delivered_at", "error_code", "uncertain", "message_sid", "reason",
                   "next_attempt_at", "next_attempt_at_utc", "provider_message_id", "updated_at_utc", "attempt_count"}
        clean = {k: v for k, v in receipt.items() if k in allowed}
        clean["recorded_at"] = utc(self.clock()).isoformat()
        with self.store.connect(write=True) as db:
            old = self.store.state(db, "transport:" + notification_id, {})
            old_status, new_status = old.get("status"), clean.get("status")
            rank = {"retry_wait": 1, "uncertain": 2, "provider_accepted": 3, "accepted": 3,
                    "sent": 4, "failed": 5, "failed_permanent": 5, "undelivered": 5,
                    "canceled": 5, "delivered": 6, "read": 7}
            old_time, new_time = old.get("updated_at_utc"), clean.get("updated_at_utc")
            regresses = rank.get(new_status, 0) < rank.get(old_status, 0)
            older = old_time and new_time and utc(new_time) < utc(old_time)
            # Receipt GETs may finish out of order, even after a worker lease expires.
            # The same atomic write transaction protects the terminal delivery proof.
            if old and (regresses or (older and rank.get(new_status, 0) <= rank.get(old_status, 0))):
                return old
            clean = {**old, **clean}
            self.store.set_state(db, "transport:" + notification_id, clean)
        return clean

    def drain_notifications(self, *, lease_owner=None):
        if self.delivery is None:
            return []
        results = []
        if hasattr(self.delivery, "reconcile_pending"):
            try:
                for receipt in self.delivery.reconcile_pending(limit=10):
                    notification_id = receipt.get("source_event_id")
                    if notification_id and self.store.message(notification_id):
                        self.record_transport_receipt(notification_id, receipt)
            except Exception:
                results.append({"status": "receipt_reconciliation_unavailable"})
        for queued in self.notifications_pending():
            if lease_owner and not self.store.acquire("worker", lease_owner, self.clock(), seconds=300):
                results.append({"status": "worker_lease_lost"})
                break
            message = self.store.message(queued["event_id"])
            profile = self.store.orchard(queued["orchard_id"])
            # A preceding provider call may have blocked or another worker may have
            # changed the incident. Recheck this immutable message immediately.
            envelope = self._notification(message, profile, self.store.get("watch:" + profile["id"], {}), utc(self.clock()))
            if not envelope["eligibility"]["eligible"]:
                continue
            try:
                receipt = self.delivery.dispatch_event(envelope)
                self.record_transport_receipt(envelope["event_id"], receipt)
                results.append({"notification_id": envelope["event_id"], "status": receipt.get("status")})
            except Exception:
                # Keep the immutable source message pending. The transport reconciles uncertain sends.
                results.append({"notification_id": envelope["event_id"], "status": "transport_unavailable"})
        return results

    def import_estimates(self, orchard_id, archive):
        """Import an explicitly supplied trusted estimate archive, never raw station chill totals."""
        profile, now = self.store.orchard(orchard_id), utc(self.clock())
        from terra_qc.reference import REFERENCE_ID
        allowed = {"schema_version", "data_mode", "geometry_key", "estimator_method_version", "qc_reference_id", "unit", "source", "rows"}
        if not isinstance(archive, dict) or set(archive) - allowed or archive.get("schema_version") != "terra-orchard-history-v1":
            raise ValueError("Expected a versioned orchard estimate archive")
        if (archive.get("data_mode") != profile["mode"] or archive.get("geometry_key") != profile["geometry_key"]
            or archive.get("qc_reference_id") != REFERENCE_ID or archive.get("unit") != "degF"):
            raise ValueError("History mode, geometry, QC reference and temperature units must match")
        estimator = archive.get("estimator_method_version")
        if not isinstance(estimator, str) or not estimator.strip() or len(estimator) > 200:
            raise ValueError("History needs the exact estimator method version")
        contract = fingerprint({"estimator": estimator, "qc": REFERENCE_ID})
        state = self.store.get("watch:" + orchard_id, {})
        if state.get("method_version") and contract != state["method_version"]:
            raise ValueError("History and current estimation contracts differ")
        raw = archive.get("rows")
        if not isinstance(raw, list) or not 1 <= len(raw) <= 200000:
            raise ValueError("Provide a bounded existing hourly estimate archive")
        rows, seen = [], set()
        for item in raw:
            if not isinstance(item, dict) or set(item) - {"valid_utc", "temperature_f", "usable", "status_reason"}:
                raise ValueError("Unexpected hourly history fields")
            stamp = utc(item["valid_utc"])
            value, usable = item.get("temperature_f"), item.get("usable")
            if stamp.minute or stamp.second or stamp.microsecond or stamp > now or stamp in seen or not isinstance(usable, bool):
                raise ValueError("History hours must be unique, completed aware hours")
            seen.add(stamp)
            if usable and (isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not -10 <= value <= 125):
                raise ValueError("Usable history needs finite Fahrenheit estimates")
            if usable and (stamp.astimezone(PST) - timedelta(seconds=1)).month not in {10, 11, 12, 1, 2, 3}:
                raise ValueError("History lies outside the estimator's validated winter window")
            rows.append({"valid_utc": stamp.isoformat(), "temperature_f": float(value) if usable else None,
                         "usable": usable, "status": "ok" if usable else "insufficient_data",
                         "status_reason": "" if usable else "history_unavailable", "method_version": contract,
                         "estimator_method_version": estimator, "qc_reference": REFERENCE_ID,
                         "geometry_key": profile["geometry_key"], "as_of": now.isoformat()})
        with self.store.connect(write=True) as db:
            evidence = self.store.retain(db, archive)
            changed = sum(self.store.put_hour(db, orchard_id, {**row, "evidence_id": evidence}) for row in rows)
            retained = self.store.state(db, "history:" + orchard_id, [])
            if not any(item["id"] == evidence for item in retained):
                retained.append({"id": evidence, "source": archive.get("source", "caller-supplied trusted estimate archive"),
                                 "rows": len(rows), "estimator_method_version": estimator, "mode": profile["mode"]})
            self.store.set_state(db, "history:" + orchard_id, retained)
            state.setdefault("method_version", contract)
            self.store.set_state(db, "watch:" + orchard_id, state)
            # No finding, delivery or 'healthy current station' claim is created by an import.
        return {"changed_hours": changed, "archive_id": evidence, "mode": profile["mode"]}

    def retain_fnric(self, orchard_id, station_id, records):
        self.store.orchard(orchard_id)
        if isinstance(station_id, bool) or not isinstance(station_id, int) or not isinstance(records, list) or len(records) > 50000:
            raise ValueError("Provide bounded reported station chill records")
        from terra_qc.chill import summarize_chill
        summary = summarize_chill(records, station_id, utc(self.clock()))
        value = {"basis": "reported FNRIC station chill; not orchard estimates", "source": "FNRIC",
                 "station_id": station_id, "season_start_convention": "September 1", "summary": summary,
                 "checked_at": utc(self.clock()).isoformat()}
        with self.store.connect(write=True) as db:
            self.store.set_state(db, "fnric:" + orchard_id, value)
        return value

    def worker_status(self):
        runtime = self.store.get("worker-runtime")
        result = {"last_cycle": self.store.get("worker"), "runtime": runtime}
        if runtime:
            age = max(0, (utc(self.clock()) - utc(runtime["heartbeat"])).total_seconds())
            result["heartbeat_age_seconds"] = age
            result["heartbeat_status"] = "fresh" if runtime.get("running") and age <= max(90, runtime["interval_seconds"] * 2) else "stale_or_stopped"
        else:
            result["heartbeat_status"] = "no_continuous_worker_recorded"
        return result


def _explanation(event):
    return {"monitoring_limited": "weather data could not support a current orchard estimate; the cause is unconfirmed",
            "monitoring_recovered": "three consecutive valid hourly estimates confirmed weather coverage recovery",
            "chill_pace": "two completed days were below the historical 20th-percentile chill pace; this is not a crop-failure prediction",
            "chill_pace_recovered": "three valid completed-day comparisons returned out of the low-pace range",
            "chill_reference": "the configured chill reference was reached; biological dormancy completion is not established"}.get(event["type"], "the recorded event is available under Details")
