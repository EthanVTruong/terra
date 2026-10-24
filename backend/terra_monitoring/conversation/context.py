"""Read-only, bounded same-farm context for Terra's conversational engine.

The application supplies factual sentences, not station arrays or hidden runtime
state. User dialogue is kept separately from authoritative facts. Saved-note and
referenced evidence remain distinct from the latest weather assessment.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re

from ..farm_ops.store import utc
from ..farm_ops.writer import fallback, message_input
from ..farm_ops.service import _facts
from .local_facts import gap_answer, profile_answer, weather_answer


MAX_PACKET_BYTES = 40_000
MAX_HISTORY_CHARS = 12_000
MAX_HISTORY = 16
MAX_FACTS = 24
_WEATHER = ("temperature", "freshness", "weather_variables", "station_findings",
            "temperature_normal", "weather_history_unavailable")
_SECRET = re.compile(r"\b(?:Bearer\s+[^\s,;]+|sk-[A-Za-z0-9_-]{8,}|eyJ[A-Za-z0-9_-]{15,}(?:\.[A-Za-z0-9_-]+){1,2})", re.I)
_COORD_PAIR = re.compile(r"(?<![\d.])-?\d{1,3}\.\d{3,}\s*[,/]\s*-?\d{1,3}\.\d{3,}(?![\d.])")


def _text(value, private=()):
    if not isinstance(value, str):
        return ""
    value = _SECRET.sub("[private credential]", value)
    value = _COORD_PAIR.sub("[private location]", value)
    for item in private:
        if isinstance(item, (float, int)) and not isinstance(item, bool):
            for variant in {str(item), f"{item:g}", f"{item:.4f}"}:
                # A configured elevation can be a common temperature or count;
                # only latitude/longitude are supplied to this exact-value pass.
                value = re.sub(r"(?<![\d.])" + re.escape(variant) + r"(?![\d.])", "[private location]", value)
    return "".join(c for c in value if ord(c) >= 32 or c in "\n\t")


def _published(item):
    for key in ("published_at", "created_at"):
        try:
            return utc(item[key]).isoformat()
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    return ""


def _note_text(note):
    packet = note.get("llm_input")
    if not isinstance(packet, dict) or not isinstance(packet.get("events"), list):
        return "A saved note is present, but its computed evidence is unavailable."
    try:
        text = fallback(packet)
    except (KeyError, TypeError, ValueError, OverflowError):
        return "A saved note is present, but its computed evidence is unavailable."
    # The old fallback's setup wording never proves a continuous worker exists.
    text = re.sub(r"monitoring is connected", "the farm profile was saved", text, flags=re.I)
    return "Saved note only; it does not verify current conditions. " + text


def context_packet(service, *, text, selected, context, reply_to=None, setup_override=None):
    """Collect local facts and bounded conversation without fetching or inference.

    Invalid explicit references raise before a caller makes an inference request.
    Context always labels original note facts separately from latest conditions.
    """
    state = service.state(selected)
    setup = setup_override if setup_override is not None else state["setup"]
    profile = service.store.orchard(selected) if selected else {}
    private = tuple(profile.get(key) for key in ("lat", "lon"))
    facts = []

    def add(identifier, value, *, scope="current", **metadata):
        clean = _text(value, private)
        if clean and len(facts) < MAX_FACTS:
            # A single pathological source must not fill the context budget.
            if len(clean) > 2800:
                clean = clean[:2600].rsplit(". ", 1)[0] + ". Further saved details are omitted."
            facts.append({"id": identifier, "text": clean, "scope": scope, **metadata})

    for intent in ("profile_crops", "profile_notifications", "profile_monitor", "chill_method"):
        local_state = {**state, "setup": setup}
        value, _ = profile_answer(intent, local_state)
        add(intent, value)
    add("profile_location", "The farm location is saved and confirmed." if selected
        else "The farm location is not confirmed. Use the location control to share it.")
    add("setup", {"crop": "Setup still needs the crops being grown.",
                  "location": "Crops are saved; setup still needs a confirmed farm location.",
                  "notifications": "The farm is saved; a separate optional notification choice remains.",
                  "complete": "Farm setup is saved. Monitoring availability is stated separately."}.get(
                      setup.get("stage"), "Farm setup is unfinished."))

    notes, turns = [], service._turns(selected or "onboarding")
    if selected:
        notes = [note for note in service.store.messages(selected, limit=20)
                 if note.get("orchard_id") == selected and note.get("kind") == "proactive"]
        for intent in _WEATHER:
            value, weather_facts = weather_answer(intent, service.monitor, selected)
            add("current_" + intent, value, evidence_id=weather_facts.get("evidence_id"), event_ids=[])
        status = service.monitor.status(selected)
        summary = status["summary"]
        event = {"id": "context-chill", "type": "chill_total", "transition": "answer", "severity": "info",
                 "occurred_at": utc(service.monitor.clock()).isoformat(), "facts": _facts(summary)}
        add("chill_total", fallback(message_input(profile, [event], service.monitor.clock())))
        gap, _ = gap_answer(summary)
        add("chill_history", gap)

    # Resolve the exact selected message, even if it is older than our tail.
    referenced_note, referenced_turn = None, None
    if reply_to:
        referenced_turn = next((turn for turn in turns if turn.get("id") == reply_to), None)
        if referenced_turn:
            message_id = referenced_turn.get("data", {}).get("message_id")
            if message_id:
                referenced_note = service.store.message(message_id)
        else:
            referenced_note = service.store.message(reply_to)
            if not referenced_note:
                raise ValueError("The referenced message is unavailable")
        if referenced_note and referenced_note.get("orchard_id") != selected:
            raise ValueError("The referenced message belongs to another farm")
    event_ids = list(dict.fromkeys(context.get("event_ids", [])))
    evidence_ids = list(dict.fromkeys(context.get("weather_evidence_ids", [])))
    if referenced_note:
        add("referenced_note", _note_text(referenced_note), scope="reference",
            source_note_id=referenced_note["id"], source_time=referenced_note.get("created_at"),
            event_ids=list(referenced_note.get("event_ids", [])))
        event_ids = list(dict.fromkeys(referenced_note.get("event_ids", [])))
    prior_answer = next((turn for turn in reversed(turns) if turn.get("role") == "assistant"
                         and turn.get("data", {}).get("source_facts")), None) if not reply_to else None
    fact_turn = referenced_turn if referenced_turn and referenced_turn.get("role") == "assistant" else prior_answer
    if fact_turn:
        # Only app-supplied, previously cited facts become reference facts.
        # A user turn is conversational context, never authoritative evidence.
        source_facts = fact_turn.get("data", {}).get("source_facts", [])
        source_facts = source_facts if isinstance(source_facts, list) else []
        for index, fact in enumerate(source_facts[:4]):
            if not isinstance(fact, dict) or not isinstance(fact.get("text"), str):
                continue
            add(("reference_cited_" if reply_to else "prior_answer_") + str(index), fact["text"], scope="reference",
                evidence_id=fact.get("evidence_id"), event_ids=list(fact.get("event_ids", [])),
                source_time=fact.get("source_time") or _published(fact_turn), source_note_id=fact_turn["id"])
    if selected and (reply_to or context.get("weather_reference")):
        if len(evidence_ids) > 1:
            add("reference_ambiguity", "The referenced note contains several saved assessments. Ask which reading is meant; do not substitute the latest assessment.", scope="reference")
        else:
            evidence = evidence_ids[0] if evidence_ids else None
            for intent in ("temperature", "freshness", "station_findings", "weather_variables"):
                value, _ = weather_answer(intent, service.monitor, selected, evidence_id=evidence, referenced=True)
                add("reference_" + intent, value, scope="reference", evidence_id=evidence, event_ids=event_ids)
    for index, note in enumerate(sorted(notes, key=_published)[-3:]):
        if referenced_note and note["id"] == referenced_note["id"]:
            continue
        add("saved_note_" + str(index), _note_text(note), scope="saved_note",
            source_note_id=note["id"], source_time=note.get("created_at"), event_ids=list(note.get("event_ids", [])))

    history = []
    for item in turns:
        if item.get("role") not in {"user", "assistant", "event"}:
            continue
        # The current reserved user turn is supplied separately, never twice.
        if item.get("role") == "user" and item.get("text", "").strip() == text.strip() and item is turns[-1]:
            with service.store.connect() as db:
                pending = db.execute("SELECT 1 FROM tara_requests WHERE id=? AND thread=? AND status='pending'",
                                     (item["id"].removesuffix(":user"), selected or "onboarding")).fetchone()
            if pending:
                continue
        row = {"id": item["id"], "role": item["role"], "kind": item.get("kind", "text"),
               "text": _text(item.get("text", ""), private)[:1500], "published_at": _published(item)}
        data = item.get("data", {})
        row["data"] = {key: deepcopy(data[key]) for key in ("intent", "focus", "fact_ids", "action") if key in data}
        history.append(row)
    for note in notes:
        history.append({"id": note["id"], "role": "assistant", "kind": "proactive",
                        "text": _text(note.get("text", ""), private)[:1500], "published_at": _published(note),
                        "data": {"source_event_ids": note.get("event_ids", [])}})
    history.sort(key=lambda item: item["published_at"])
    history = history[-MAX_HISTORY:]
    while history and sum(len(item["text"]) for item in history) > MAX_HISTORY_CHARS:
        history.pop(0)
    packet = {"schema_version": "terra-conversation-context-v1",
              "request": {"text": _text(text, private), "reply_to": reply_to},
              "setup": {"stage": setup.get("stage"),
                        "crops": [_text(item, private) for item in setup.get("crops", [])],
                        "variety": _text(setup.get("variety"), private) or None},
              "orchard": {"id": selected, "name": _text(profile.get("name", ""), private),
                          "crops": [_text(item, private) for item in profile.get("crops", [profile["crop"]] if profile else [])],
                          "mode": profile.get("mode", "current")},
              "monitoring": {key: state["monitoring"][key] for key in ("status", "reason")},
              "facts": facts, "history": history,
              "references": {"reply_to": reply_to, "event_ids": event_ids,
                             "evidence_ids": evidence_ids, "ambiguous": len(evidence_ids) > 1},
              "capabilities": {"actions_require_application": True, "location_control": not selected,
                               "notification_control": True, "forecasts": False,
                               "crop_outcome_predictions": False, "treatment_advice": False}}
    while len(json.dumps(packet, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_PACKET_BYTES:
        if packet["history"]:
            packet["history"].pop(0)
        elif len(packet["facts"]) > 1:
            packet["facts"].pop()
        else:
            raise ValueError("Conversation context exceeds its local bound")
    return packet
