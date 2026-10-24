"""Explain monitor-selected facts through the connected conversational engine.

Python has already decided which events matter. This adapter gives the model
their factual meaning and limitations, then retains the original immutable input
and uses a local renderer if the single generation attempt cannot be accepted.
"""
from __future__ import annotations

from datetime import timedelta
import re

from .conversational_writer import compact_text
from .store import utc
from .writer import Writer, fallback


_KINDS = {
    "welcome", "chill_total", "history", "station_health", "explain_alert",
    "monitoring_limited", "monitoring_recovered", "chill_pace",
    "chill_pace_recovered", "chill_reference", "sensor_flag",
    "weather_discrepancy", "weather_discrepancy_recovered",
    "weather_data_limited", "weather_data_recovered",
}

_MEANING = {
    "welcome": "Saving this farm alone does not establish an active background monitor.",
    "monitoring_limited": "The weather support limits the reliability or availability of the orchard estimate; it does not establish crop danger.",
    "monitoring_recovered": "Python confirmed that weather support recovered; that does not establish a sensor repair or a change in crop health.",
    "chill_pace": "The estimated chill accumulation is below the historical comparison; it is not a forecast of bloom, yield or crop damage.",
    "chill_pace_recovered": "Python confirmed recovery of the chill-pace comparison, not biological recovery or completion.",
    "chill_reference": "This is a configured reference, not proof that biological dormancy requirements are complete.",
    "sensor_flag": "The local sensor differs from the calibrated-station estimate at its location. The cause is unconfirmed; this is not a diagnosis of sensor failure or crop risk.",
    "weather_discrepancy": "This is a provisional station-to-peer comparison, not a measurement at the orchard or a confirmed sensor failure. The cause is unconfirmed.",
    "weather_discrepancy_recovered": "The station comparison recovered; this does not establish a physical repair or orchard conditions.",
    "weather_data_limited": "Missing or source-warned data limits this variable's station comparison. That alone does not establish a numerical discrepancy or invalidate another variable's estimate.",
    "weather_data_recovered": "Support for this variable's station comparison recovered; that alone does not establish a change in orchard conditions.",
    "history": "Only the supplied comparable history supports a pace comparison. Missing history does not mean the orchard is ahead or behind.",
}


def _respond(generator, packet):
    # Disconnected and explicitly disabled runtimes need no engine import.
    from ..conversation.engine import respond
    return respond(generator, packet)


def _prefix(packet):
    return ("Demo replay: " if packet.get("mode") != "current" else
            "Demo orchard: " if packet.get("orchard", {}).get("demo_location") else "")


def _local(packet, voice, outcome):
    # Never delegate failure to the configured writer: it could make a second
    # provider request or silently switch to an explicitly configured API key.
    result = Writer(key="", model="", generator=None, voice=voice).write(packet)
    text = compact_text(packet, result["text"])
    prefix = _prefix(packet)
    if prefix and not text.startswith(prefix):
        text = prefix + text
    return {**result, "text": text, "provider": "template", "outcome": outcome}


def insight_context(packet):
    """Build bounded authoritative event statements without raw station arrays."""
    events = packet.get("events")
    if not isinstance(events, list) or not 1 <= len(events) <= 32:
        raise ValueError("No bounded event facts")
    facts = []
    for index, event in enumerate(events):
        if not isinstance(event, dict) or event.get("type") not in _KINDS:
            raise ValueError("Unsupported event family")
        single = {**packet, "events": [event]}
        text = compact_text(single, fallback(single))
        meaning = _MEANING.get(event["type"])
        if meaning:
            text += " " + meaning
        facts.append({"id": f"event_{index}", "text": text,
                      "kind": event["type"], "source_time": event.get("occurred_at"),
                      "severity": event.get("severity")})
    return {
        "schema_version": "terra-conversation-context-v1",
        "request": {"text": "Explain these newly recorded changes as a concise alert: what changed and what it means for the estimate or comparison. Use all cited event facts; do not add a follow-up question.", "reply_to": None},
        "setup": {"stage": "complete"},
        "orchard": {"mode": packet["mode"], "demo_location": bool(packet["orchard"].get("demo_location"))},
        "monitoring": {"status": "event_recorded", "as_of": packet["as_of"]},
        "facts": facts, "history": [], "references": {},
        "capabilities": {"communication": "proactive_alert", "allow_clarification": False,
                         "settings_changes": False, "forecasts": False, "treatment_advice": False},
    }


def _chronology(packet, text):
    """Retain material source clocks independently of generated prose."""
    times = []
    for event in packet["events"]:
        facts = event.get("facts", {})
        value = facts.get("observation_time") or facts.get("data_through") or event.get("occurred_at")
        if not value:
            continue
        source = utc(value)
        # A retained/simulated source must not read as a current measurement.
        if packet["mode"] != "current" or utc(packet["as_of"]) - source > timedelta(hours=6):
            stamp = source.strftime("%b %d, %Y %H:%M UTC").replace(" 0", " ")
            if stamp not in times:
                times.append(stamp)
    if times:
        text += " Saved evidence: " + "; ".join(times[:3]) + ("; other source times are in Details." if len(times) > 3 else ".")
    prefix = _prefix(packet)
    if prefix and not text.startswith(prefix):
        text = prefix + text
    return text


def _check_meaning(packet, text):
    """Reject reversals of the core coverage state without a prose whitelist."""
    normalized = text.lower().replace("’", "'")
    kinds = {event["type"] for event in packet["events"]}
    if kinds == {"monitoring_limited"}:
        if (not re.search(r"\b(weather|coverage|data|support|readings)\b", normalized)
                or not re.search(r"\b(limited|spotty|patchy|missing|incomplete|unavailable|insufficient)\b|too few|can(?:not|'t)", normalized)
                or re.search(r"\b(?:estimate|coverage|weather data) (?:is|has) (?:complete|recovered|reliable|restored)\b", normalized)):
            raise ValueError("Coverage limitation was reversed or omitted")
        if (any(event["severity"] == "strong" for event in packet["events"])
                and not re.search(r"\b(unavailable|missing)\b|can(?:not|'t) (?:reliably )?(?:update|verify|estimate)", normalized)):
            raise ValueError("Continued unavailability was omitted")
        if (all(event["severity"] != "strong" for event in packet["events"])
                and re.search(r"\bweather (?:data|coverage) (?:is |remains )?(?:unavailable|absent)\b|can(?:not|'t) update (?:the |your |an? )?(?:orchard )?estimate", normalized)):
            raise ValueError("Partial coverage was overstated as full unavailability")
    if kinds == {"monitoring_recovered"}:
        if (not re.search(r"\b(weather|coverage|data|support|readings)\b", normalized)
                or not re.search(r"\b(recovered|back|restored|returned|resumed|updating)\b", normalized)
                or re.search(r"still missing|can(?:not|'t) update|coverage is unavailable", normalized)):
            raise ValueError("Coverage recovery was reversed or omitted")
    if kinds & {"sensor_flag", "weather_discrepancy"}:
        if not re.search(r"\b(suspected|provisional|unconfirmed|possible|uncertain|unknown|unclear)\b|can(?:not|'t) tell|not sure|not (?:established|confirmed)", normalized):
            raise ValueError("Discrepancy uncertainty was omitted")
    if kinds <= {"weather_discrepancy", "weather_discrepancy_recovered", "weather_data_limited", "weather_data_recovered"}:
        if re.search(r"\b(?:orchard |temperature )?estimate (?:is |remains )?(?:unavailable|incomplete|invalid)|can(?:not|'t) update (?:the |your |an? )?(?:orchard )?estimate", normalized):
            raise ValueError("A variable comparison was turned into an orchard-estimate limitation")
    if re.search(r"\b(?:sensor|station) (?:is|was|has) (?:broken|failed|repaired)|\b(?:crop|trees?) (?:is|are|will be) (?:damaged|safe|dead)", normalized):
        raise ValueError("Unsupported diagnosis")


class InsightWriter:
    """Use the connected conversational engine for proactive explanations once."""

    def __init__(self, writer):
        self.writer = writer

    @property
    def generator(self):
        return getattr(self.writer, "generator", None)

    @property
    def voice(self):
        return getattr(self.writer, "voice", "legacy")

    def write(self, packet):
        if "question" in packet:
            return self.writer.write(packet)
        if self.generator is None:
            return _local(packet, self.voice, "chatgpt_not_connected")
        try:
            context = insight_context(packet)
            result = _respond(self.generator, context)
            required = {fact["id"] for fact in context["facts"]}
            text = result.get("text")
            if (result.get("outcome") != "message" or not isinstance(text, str)
                    or not text.strip() or len(text) > 420 or "?" in text
                    or set(result.get("fact_ids", [])) != required
                    or result.get("provider") != "chatgpt_plan"
                    or result.get("generation_outcome") != "completed"):
                raise ValueError("Not a grounded proactive message")
            _check_meaning(packet, text)
            text = _chronology(packet, text)
            if len(text) > 420:
                raise ValueError("Final message exceeds the alert bound")
            return {"text": text, "provider": "chatgpt_plan", "outcome": "completed"}
        except Exception:
            # Provider errors and prompt bodies never enter published evidence.
            return _local(packet, self.voice, "insight_failed_or_invalid")
