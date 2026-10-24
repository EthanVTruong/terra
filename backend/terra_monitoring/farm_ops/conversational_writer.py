"""Compose with the existing fact validator, then remove app-owned chat chrome.

The provider never decides scope or whether to reply. The conversation service
does that before calling this writer. Model wording still passes the production
role/quantity checks. Full input clocks and evidence stay in the immutable note.
"""
from __future__ import annotations

from datetime import timedelta, timezone
import re

from .store import utc
from .writer import Writer, fallback


CONVERSATIONAL_SYSTEM_PROMPT = """You write Tara's short replies to a grower about
their saved farm, chill hours, weather support, or an alert Tara has surfaced.
Prefer one brief, natural clause per supplied event; add detail only when it contributes a
useful comparison or limitation. Answer directly, without a greeting, repeated
thanks, a follow-up question, or a promise to keep watching. Keep the tone like a
quick text. Use only the bounded facts and intent supplied by the application.
Never broaden the conversation into games or unrelated topics, infer causes or
crop outcomes, or turn a question into permission for an action. Scope, reactions,
silence and actions have already been handled locally before this request.
The factual output/placeholder rules below remain mandatory.
"""


_SEASONAL = {"welcome", "chill_total", "history", "chill_pace", "chill_pace_recovered", "chill_reference"}


def _seasonal_date(packet, event):
    facts = event.get("facts", {})
    # Unavailable totals describe current availability, not a historic numeric
    # value. Retained numeric totals always retain their actual source date.
    if facts.get("hours") is None and event.get("type") in {"welcome", "chill_total", "history"}:
        return ""
    value = facts.get("observation_time") or facts.get("data_through")
    if value is None:
        return event["display_time"]
    try:
        zone = timezone(timedelta(hours=-8))
        stamp, now = utc(value).astimezone(zone), utc(packet["as_of"]).astimezone(zone)
        date = stamp.strftime("%b %d").replace(" 0", " ")
        return date if stamp.year == now.year else date + " " + str(stamp.year)
    except (TypeError, ValueError, OverflowError):
        return event["display_time"]


def compact_text(packet, text):
    """Remove only known Python-added framing, never search/replace model facts.

    Preserve mixed evidence times and data-health times. A single seasonal
    cutoff keeps its source date, including the year when needed. The note's
    exact input and Details retain full clock precision.
    """
    if not isinstance(text, str) or not text:
        return text
    mode_prefix = ("Demo replay: " if packet["mode"] != "current" else
                   "Demo orchard: " if packet["orchard"].get("demo_location") else "")
    prefix = mode_prefix + packet["orchard"]["name"] + ": "
    if not text.startswith(prefix):
        # The existing approved weather-texting renderer already supplies
        # concise text and its mode badge is rendered by the application.
        return text
    body = text[len(prefix):]
    events = packet.get("events", [])
    if any(event.get("type") == "welcome" for event in events):
        # A saved profile/first assessment does not establish that a continuous
        # worker is running. The conversation's readiness state owns that claim.
        body = re.sub(r"\bmonitoring\s+is\s+connected\b", "your farm is saved", body, flags=re.I)
    clocks = {event.get("display_time") for event in events}
    if events and len(clocks) == 1 and all(event.get("type") in _SEASONAL for event in events):
        suffix = " As of " + events[0]["display_time"] + "."
        if body.endswith(suffix):
            body = body[:-len(suffix)]
            date = _seasonal_date(packet, events[0])
            if date:
                body += " As of " + date + "."
    if body:
        body = body[0].upper() + body[1:]
    return mode_prefix + body


class ConversationalWriter:
    """Use the configured plan writer once, with a local fallback for questions.

    Explicit legacy API-key configuration is preserved for ordinary monitoring
    callers. Interactive replies do not silently consume API-key billing when
    their ChatGPT plan connection is absent.
    """

    def __init__(self, writer):
        self.writer = writer

    @property
    def generator(self):
        return getattr(self.writer, "generator", None)

    @property
    def voice(self):
        return getattr(self.writer, "voice", "legacy")

    def write(self, packet):
        unavailable = bool(packet.get("events")) and all(
            event.get("type") in {"chill_total", "history"}
            and event.get("facts", {}).get("hours") is None
            and event.get("facts", {}).get("historical_median_hours") is None
            for event in packet["events"])
        if "question" in packet and unavailable:
            result = {"text": fallback(packet), "provider": "template", "outcome": "local_unavailable"}
        elif "question" in packet and self.generator is None:
            result = {"text": fallback(packet), "provider": "template", "outcome": "chatgpt_not_connected"}
        elif "question" in packet:
            # A per-request adapter preserves the original provider's account,
            # model, pause and consent handling without mutating the shared
            # monitoring writer while a background check may also be running.
            generator = _QuestionGenerator(self.generator)
            result = Writer(key="", model="", generator=generator, voice=self.voice).write(packet)
        else:
            result = self.writer.write(packet)
        return {**result, "text": compact_text(packet, result["text"])}


class _QuestionGenerator:
    def __init__(self, generator):
        self.generator = generator

    def generate(self, *, instructions, packet, schema=None):
        return self.generator.generate(instructions=CONVERSATIONAL_SYSTEM_PROMPT + "\n" + instructions,
                                       packet=packet, schema=schema)
