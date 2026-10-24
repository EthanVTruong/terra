"""A deliberately bounded local router. Raw conversation never enters inference."""
from __future__ import annotations

import re

PROMPT = "Hey, I'm Tara. What are you growing?"
QUESTIONS = {
    "chill_total": "How much chill has accumulated?",
    "history": "Compare historical chill pace.",
    "station_health": "Is supporting station weather coverage usable?",
    "explain_alert": "Explain the latest alert.",
}
LOCAL_INTENTS = {"profile_crops", "profile_location", "profile_notifications", "profile_monitor", "chill_method"}
OFF_TOPIC = re.compile(r"\b(minecraft|stardew|fortnite|pokemon|video ?games?|gaming|roblox|skyrim|zelda|elden ring|chill music|poem|joke|recipe|javascript|python code)\b", re.I)
THANKS = re.compile(r"^(?:(?:no )?thank(?:s| you)(?: so much)?(?: for (?:(?:the|your) )?(?:update|help))?|appreciate it|cheers|bye|goodbye)[.!\s👍]*$", re.I)
ACK = re.compile(r"^(?:got it|sounds good|okay|ok|great|understood|all right|👍)[.!\s]*$", re.I)
CORRECTION = re.compile(r"\b(wrong|incorrect|correction|actually|isn.t my|not my|change (?:my |the )?(?:crop|farm|location))\b", re.I)
FACT_WORDS = set("""a an the my our we us i me you it this that these those is are was were has have had do does did can could would should
what whats how whys why which when where tell show give check explain compare comparison compared with to than from for of at in on by since
through as about and or please thanks thank so far much many any now today currently right latest last past year years usual normally normal
historical history pace behind ahead accumulated accumulating accumulation total totals hour hours chill chilling estimated estimate estimates
temperature temperatures weather data station stations neighbor neighbors neighboring support supporting coverage reliable reliability usable
available unavailable incomplete complete missing spotty stale old new reading readings quality alert alerts warning warnings flag flags note notes
mean means meaning wrong off low high too long season seasonal orchard farm condition conditions status getting looking doing good okay ok back
still more less not no enough under over slow slowly recent look like says say help hey hi tara count counting current gap difference am s t jan january feb february mar march apr april
may jun june jul july aug august sep september oct october nov november dec december pst pdt utc""".split())


def _bounded(intent, text, known_subjects):
    """Positive vocabulary gate: unknown subjects never trigger paid inference.

    This intentionally favors one local clarification over arbitrary topic
    classification. Persisted farm/crop names may name the active subject.
    """
    allowed = FACT_WORDS | {token for value in known_subjects if isinstance(value, str)
                            for token in re.findall(r"[a-z]+", value.lower())}
    return intent if set(re.findall(r"[a-z]+", text)) <= allowed else "clarify"


def route(text, context=None, known_subjects=()):
    """Return a local action/intent, with high priority for real requests."""
    text = text.strip()
    low = text.lower().replace("’", "'").replace("—", ";")
    clauses = re.split(r"[;.!]|\b(?:and then|then|also)\b", low)
    # Only affirmative action clauses may mutate preferences. Questions about
    # a command and fictional settings are never commands to this application.
    for clause in clauses:
        part = clause.strip(" ,👍")
        part = re.sub(r"^(?:(?:no )?thanks|thank you|got it)[,\s]+", "", part)
        if OFF_TOPIC.search(part):
            continue
        if re.search(r"\b(?:don't|do not|never)\s+(?:stop|turn off|disable|pause|unsubscribe|switch off)\b", part):
            return "preference_unchanged"
        imperative = re.sub(r"^(?:please\s+|(?:can|could|would) you\s+)", "", part).strip(" ?")
        target = r"(?:(?:the|my|all) )?(?:browser )?(?:alerts?|notifications?|sms|texts?)(?: only)?(?: please)?"
        if (re.fullmatch(r"(?:turn off|switch off|disable|pause|stop|unsubscribe from) " + target, imperative)
                or re.fullmatch(r"(?:turn|switch) (?:(?:the|my|all) )?(?:browser )?(?:alerts?|notifications?|sms|texts?) off(?: only)?(?: please)?", imperative)):
            return "sms_off" if re.search(r"\b(?:sms|texts?)\b", imperative) else "notifications_off"
        if re.fullmatch(r"(?:stop texting me|(?:don't|do not) text me|no more (?:sms|texts)|(?:don't|do not) send (?:me )?(?:sms|texts))(?: please)?", imperative):
            return "sms_off"
        if re.fullmatch(r"(?:i )?(?:don't|do not) (?:want|send)(?: me)? (?:any )?(?:alerts|notifications)(?: anymore)?", imperative):
            return "notifications_off"
        if (re.fullmatch(r"(?:turn on|switch on|enable|subscribe to) " + target, imperative)
                or re.fullmatch(r"(?:turn|switch) (?:(?:the|my|all) )?(?:browser )?(?:alerts?|notifications?|sms|texts?) on(?: please)?", imperative)):
            return "notifications_choice"
    if (CORRECTION.search(low) and re.search(r"\b(?:crop|farm|orchard|location|grow|growing)\b", low)
            and not re.search(r"\b(?:chill|reading|temperature|estimate|coverage)\b", low)):
        return "correction"
    if re.search(r"\b(?:no[, ]+)?i (?:grow|have)\b.+\bnot\b", low):
        return "correction"
    # Strip unrelated clauses before factual routing. A mixed clause is only
    # retained when it explicitly refers to the grower's actual monitored farm.
    factual = []
    for part in re.split(r"[;.!?]|\b(?:then|also|and then)\b", low):
        if OFF_TOPIC.search(part):
            prefix = re.split(r"\band\b", part)[0]
            explicit = re.search(r"\b(?:my|our)\s+(?:(?:latest|current)\s+)?(?:orchard|farm|chill|station|weather)\b|\bhow much chill\b", prefix)
            if OFF_TOPIC.search(prefix) or not explicit:
                continue
            part = prefix
        if not re.search(r"\b(chill|chilling|station|stations|weather|coverage|data|history|historical|compare|comparison|last year|past years?|how far|behind|why|explain|mean|farm|orchard|crops?|growing|location|monitor(?:ing)?|trees|plants|bloom|forecast|predict\w*|gdd|gdh|spray|irrigat\w*|alerts?|notifications?|sms|texting)\b", part):
            continue
        factual.append(part)
    scoped = " ".join(factual).strip()
    if not scoped and OFF_TOPIC.search(low):
        return "off_topic"
    if THANKS.fullmatch(text):
        return "thanks"
    if ACK.fullmatch(text):
        return "ack"
    if re.fullmatch(r"(?:got it|okay|understood)\s*\?", text, re.I):
        return "clarify"
    if re.search(r"\b(?:alerts?|notifications?|sms|texting)\b", scoped) and re.search(r"\b(?:turn|switch|disable|stop|pause|enable)\b", scoped):
        return "notification_status"
    low = scoped
    local_patterns = {
        "profile_crops": r"(?:what (?:crops?|varieties) (?:am i|are we) growing|what (?:am i|are we) growing|what (?:crops?|varieties) (?:do i|do we) (?:grow|have)|(?:show|tell) me (?:my|our) crops?|what(?:'s| is| are) (?:my|our|the) (?:crop|crops|variety|varieties))",
        "profile_location": r"(?:where(?:'s| is) (?:my|our|the) (?:farm|orchard)(?: (?:saved|located))?|what(?:'s| is) (?:my|our|the) (?:saved )?(?:(?:farm|orchard) )?location|what location did i save)",
        "profile_notifications": r"(?:are (?:my |our |the )?(?:browser )?(?:alerts|notifications) (?:enabled|on|off)|what(?:'s| is) (?:my |the )?(?:browser )?notification (?:status|preference))",
        "profile_monitor": r"(?:are you monitoring(?: (?:my|our|the) (?:farm|orchard))?|is monitoring (?:active|running|enabled|on)|is (?:the )?monitor (?:active|running|on)|is (?:my|our|the) (?:farm|orchard) (?:ready|being monitored)|are we ready)",
        "chill_method": r"(?:how (?:do|does) (?:you|terra|tara) (?:count|calculate|measure) chill(?: hours)?|how (?:is|are) chill(?: hours)? (?:counted|calculated)|what (?:counts|is counted) as (?:a )?chill(?: hour)?|what(?:'s| is) (?:the |your )?chill (?:method|calculation)|when does chill (?:counting |accumulation )?start|what are chill hours)",
    }
    for intent, pattern in local_patterns.items():
        if re.fullmatch(pattern, re.sub(r"^(?:thanks|thank you)[, ]+", "", low)):
            return intent
    if re.search(r"\b(?:my|our) (?:trees?|crop|orchard|farm|plants?)\b.*\b(?:dying|died|damage|sick|wilting|problem)\b", low):
        return "unsupported"
    # Unsupported predictions/actions take precedence over a supported metric
    # mentioned in the same request ("predict chill hours next week").
    if re.search(r"\b(bloom|crop damage|yield)\b", low):
        return "biological_limit"
    if re.search(r"\b(forecast|predict\w*|gdd|gdh|next week|next two|tomorrow|spray|irrigat\w*|frost protection|treatment)\b", low):
        return "unsupported"
    if re.search(r"\b(explain|why|what does .*mean|meaning)\b", low) and re.search(r"\b(alert|warning|flag|note)\b", low):
        return _bounded("explain_alert", low, known_subjects)
    if re.fullmatch(r"(?:(?:thanks|thank you)[, ]*)?(?:how (?:far|much) (?:are we |am i )?behind(?: are we| am i| is .+)?|how many chill hours (?:are we|am i) (?:behind|ahead)|what(?:'s| is) (?:the )?(?:chill )?(?:gap|difference))", low):
        return _bounded("history_gap", low, known_subjects) if context else "needs_context"
    if re.search(r"\b(how (?:far|much) (?:are we )?behind|behind (?:are we|the usual)|historical|past years?|last year|usual pace|compare|comparison)\b", low):
        domain = re.search(r"\b(chill|chilling|orchard|farm|station|temperature)\b", low)
        elliptical = re.fullmatch(r"(?:(?:thanks|thank you)[, ]*)?(?:how far (?:are we )?behind(?: are we)?|how much (?:are we )?behind|compare (?:past years?|historical (?:chill )?pace)|historical pace|last year|what about last year|are we behind|how (?:does|do) (?:that|we|this) compare)", low)
        if domain or elliptical:
            return _bounded("history", low, known_subjects) if context else "needs_context"
        return "off_topic"
    if (re.search(r"\b(station (?:health|coverage|data|reading)|(?:neighboring|supporting) stations?|stations? (?:normal|usable|available)|weather (?:support|coverage|data))\b", low)
            or re.fullmatch(r"(?:how'?s (?:the )?coverage|is (?:the )?(?:coverage|data) (?:usable|reliable|okay|current|back)|data quality|data reliability|coverage)", low)
            or re.fullmatch(r"(?:what'?s|how is|how'?s|is) (?:the )?weather(?: (?:looking|doing|today|now|here))?", low.strip())):
        return _bounded("station_health", low, known_subjects)
    natural_chill = re.fullmatch(r"(?:what(?:'s| is)|how(?:'s| is)|show(?: me)?|tell me|check) (?:(?:the|my|our) )?(?:estimated )?chill(?: (?:looking|doing|count|total|status|estimate|so far|today))?", low)
    if re.search(r"\b(chill hours?|chill totals?|how much chill|chill (?:pace|accumul\w*|estimate|status)|estimated chill|chilling hours?)\b", low) or natural_chill or low.strip() in {"chill", "chill?"}:
        if re.search(r"\b(bloom|damage|yield|bad crop|requirement|need|enough)\b", low):
            return "biological_limit"
        return _bounded("chill_total", low, known_subjects)
    if re.search(r"\b(forecast|gdd|gdh|bloom|spray|irrigat\w*|frost protection|treatment)\b", low):
        return "biological_limit" if re.search(r"\b(?:bloom|crop damage|yield)\b", low) else "unsupported"
    if re.fullmatch(r"(?:thanks[,! ]*)?why", low) and context in QUESTIONS and context != "explain_alert":
        return "cause_limit"
    if re.fullmatch(r"(?:why|why is that|how far|what does (?:that|this) mean|what about (?:that|this)|explain (?:that|this))", low):
        return context if context in QUESTIONS or context in LOCAL_INTENTS else "needs_context"
    if re.search(r"\b(chill|chilling|station|coverage)\b", low):
        return "clarify"
    if re.search(r"\b(monitoring|monitor|farm|orchard|location|growing|crop)\b", low):
        return "setup_help"
    if "?" in text and re.search(r"\b(got it|okay|understood)\b", low):
        return "clarify"
    return "off_topic"


def crops_from(text):
    """Keep supplied crop metadata, never infer a variety or crop requirement."""
    if len(text) > 240 or OFF_TOPIC.search(text) or re.search(r"[?{}<>]|\b(ignore|instructions|system|prompt|turn off|disable)\b", text, re.I):
        return None
    aliases = {"almond": "almond", "walnut": "walnut", "pistachio": "pistachio", "grape": "grape",
               "citrus": "citrus", "tomato": "tomato", "olive": "olive", "cherr(?:y|ies)": "cherry",
               "apple": "apple", "peach(?:es)?": "peach", "pear": "pear", "prune": "prune",
               "plum": "plum", "apricot": "apricot", "strawberr(?:y|ies)": "strawberry", "avocado": "avocado",
               "corn": "corn", "wheat": "wheat", "lettuce": "lettuce", "rice": "rice",
               "blueberr(?:y|ies)": "blueberry", "dragon ?fruit": "dragon fruit", "potato(?:es)?": "potato",
               "onion": "onion", "carrot": "carrot", "broccoli": "broccoli", "cauliflower": "cauliflower",
               "soybean": "soybean", "cotton": "cotton", "alfalfa": "alfalfa", "kiwi": "kiwi"}
    found = [(match.start(), value) for pattern, value in aliases.items()
             if (match := re.search(r"\b(?:" + pattern + r")s?\b", text, re.I))]
    crops = [value for _, value in sorted(found)]
    variety = "Nonpareil" if "almond" in crops and re.search(r"\bnonpareil\b", text, re.I) else None
    explicit = re.search(r"\b(?:variety|cultivar)\s*[:=]?\s+([\w -]{1,60})", text, re.I)
    if explicit:
        variety = explicit.group(1).strip()
    elif found and not variety:
        prefix = text[:min(start for start, _ in found)].strip(" ,.!:")
        prefix = re.sub(r"^(?:(?:i|we)(?:'m| are| am)?\s+(?:grow|growing|have)\b\s*|my\b\s*)", "", prefix, flags=re.I).strip()
        filler = {"organic", "some", "mostly", "mainly", "a", "an", "the", "we", "i", "grow", "growing", "have", "our", "my"}
        if (prefix and re.fullmatch(r"[A-Z][A-Za-z'-]*(?: [A-Z][A-Za-z'-]*){0,2}", prefix)
                and not set(prefix.lower().split()) & filler):
            variety = prefix
    if not crops:
        match = re.fullmatch(r"(?:i (?:grow|have|am growing)|we (?:grow|have)|growing)\s+([\w ,'-]{2,80})[.!]?", text.strip(), re.I)
        if not match:
            return None
        crops = [part.strip().lower() for part in re.split(r",|\band\b", match.group(1)) if part.strip()]
    if not 1 <= len(crops) <= 12:
        raise ValueError("Provide between one and twelve actual crops")
    crops = list(dict.fromkeys(crops))
    return {"crops": crops, "variety": variety if len(crops) == 1 else None, "crop_description": text.strip()}
