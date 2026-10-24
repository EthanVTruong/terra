"""A deliberately bounded local router. Raw conversation never enters inference."""
from __future__ import annotations

import re

PROMPT = "Hey, I'm Terra. What are you growing?"
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
still more less not no enough under over slow slowly recent look like says say help hey hi tara terra count counting current gap difference am s t
get got but cold hot warm degrees fahrenheit fresh freshness age outdated acting up suspect flagged findings taken recorded updated checked there
humidity relative rh wind winds windy speed speeds mph solar radiation sunlight sun percent percentage watts meter meters square w m
factors variables factor variable measure measured measurements discrepancy discrepancies unusual changes change cloudy daylight bright
jan january feb february mar march apr april
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
        part = re.sub(r"^(?:(?:no )?thanks|thank you|got it)[,\s]+(?:but\s+)?", "", part)
        if OFF_TOPIC.search(part):
            continue
        if (re.search(r"\b(?:don't|do not|never)\s+(?:stop|turn off|disable|pause|unsubscribe|switch off)\b", part)
                or re.search(r"\b(?:don't|do not|never)\s+(?:turn|switch)\s+(?:(?:the|my|all)\s+)?(?:browser\s+)?(?:alerts?|notifications?|sms|texts?)\s+(?:off|on)\b", part)):
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
        if not re.search(r"\b(chill|chilling|station|stations|weather|temperature|humidity|rh|wind|winds|windy|solar|radiation|sunlight|cold|warm|hot|readings?|estimate|normal|coverage|data|history|historical|compare[ds]?|comparison|last year|past years?|how far|behind|why|explain|mean|farm|orchard|crops?|growing|location|monitor(?:ing)?|trees|plants|bloom|forecast|predict\w*|gdd|gdh|spray|irrigat\w*|alerts?|notifications?|sms|texting)\b", part):
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
    low = re.sub(r"^(?:thanks|thank you|got it)[,\s]+(?:but\s+)?", "", scoped).strip()
    local_patterns = {
        "profile_crops": r"(?:what (?:crops?|varieties) (?:am i|are we) growing(?: here)?|what (?:am i|are we) growing(?: here)?|what (?:crops?|varieties) (?:do i|do we) (?:grow|have)|(?:show|tell) me (?:my|our) crops?|what(?:'s| is| are) (?:my|our|the) (?:crop|crops|variety|varieties))",
        "profile_location": r"(?:where(?:'s| is) (?:my|our|the) (?:farm|orchard)(?: (?:saved|located))?|what(?:'s| is) (?:my|our|the) (?:saved )?(?:(?:farm|orchard) )?location|what location did i save)",
        "profile_notifications": r"(?:are (?:my |our |the )?(?:browser )?(?:alerts|notifications) (?:enabled|on|off)|what(?:'s| is) (?:my |the )?(?:browser )?notification (?:status|preference))",
        "profile_monitor": r"(?:are you monitoring(?: (?:my|our|the) (?:farm|orchard))?|is monitoring (?:active|running|enabled|on)|is (?:the )?monitor (?:active|running|on)|is (?:my|our|the) (?:farm|orchard) (?:ready|being monitored)|are we ready)",
        "chill_method": r"(?:how (?:do|does) (?:you|terra) (?:count|calculate|measure) chill(?: hours)?|how (?:is|are) chill(?: hours)? (?:counted|calculated)|what (?:counts|is counted) as (?:a )?chill(?: hour)?|what(?:'s| is) (?:the |your )?chill (?:method|calculation)|when does chill (?:counting |accumulation )?start|what are chill hours)",
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
    historical = re.search(r"\b(?:last year|past years?|previous years?|historical|history)\b", low)
    weather_subject = re.search(r"\b(?:temperatures?|weather|cold|hot|warm|humidity|rh|wind|winds|solar|radiation|sunlight)\b", low)
    chill_subject = re.search(r"\bchill(?:ing)?\b", low)
    if historical and weather_subject:
        if chill_subject:
            return "needs_context"
        # A current estimate cannot answer a past-weather question, and the
        # orchard's seasonal chill comparison is a different quantity.
        return _bounded("weather_history_unavailable", low, known_subjects)
    weather_variables = [intent for intent, pattern in (
        ("humidity", r"\b(?:humidity|rh)\b"),
        ("wind", r"\b(?:wind|winds|windy)\b"),
        ("solar_radiation", r"\b(?:solar|radiation|sunlight)\b")) if re.search(pattern, low)]
    if weather_variables:
        return _bounded(weather_variables[0] if len(weather_variables) == 1 else "weather_variables", low, known_subjects)
    if re.search(r"\bweather (?:factors|variables|measurements)\b", low):
        return _bounded("weather_variables", low, known_subjects)
    if re.search(r"\b(explain|why|what does .*mean|meaning)\b", low) and re.search(r"\b(alert|warning|flag|note)\b", low):
        return _bounded("explain_alert", low, known_subjects)
    year_followup = re.fullmatch(r"(?:compared? (?:with|to) last year|what about last year|last year)", low)
    if year_followup:
        if context in {"temperature", "temperature_normal", "weather_history_unavailable", "humidity", "wind", "solar_radiation", "weather_variables"}:
            return "weather_history_unavailable"
        if context not in {"chill_total", "history", "history_gap"}:
            return "needs_context"
    normal_context = {"chill_total": "history", "history": "history", "history_gap": "history",
                      "station_health": "station_health", "freshness": "station_health",
                      "temperature": "temperature_normal", "temperature_normal": "temperature_normal",
                      "station_findings": "station_findings", "explain_alert": "explain_alert",
                      "humidity": "humidity", "wind": "wind", "solar_radiation": "solar_radiation", "weather_variables": "weather_variables"}
    if re.fullmatch(r"(?:is (?:that|this|it) (?:normal|usual|okay)|does (?:that|this) look normal)", low):
        return normal_context.get(context, "needs_context")
    if re.search(r"\btemperature\b", low) and re.search(r"\bnormal\b", low):
        return _bounded("temperature_normal", low, known_subjects)
    # These factual intents are answered locally from retained observations;
    # they never enter the writer or broaden its positive vocabulary gate.
    if (re.search(r"\b(?:how old|how fresh)\b", low) and re.search(r"\b(?:data|readings?|estimate)\b", low)
            or re.search(r"\b(?:data|readings?|estimate)\b.*\b(?:old|stale|fresh|outdated)\b", low)
            or re.search(r"\bwhen\b.*\b(?:data|readings?|estimate)\b.*\b(?:taken|recorded|updated|checked)\b", low)):
        return _bounded("freshness", low, known_subjects)
    if (re.search(r"\b(?:which|what|any)\b.*\bstations?\b.*\b(?:acting up|flagged|suspect|wrong|unusual)\b", low)
            or re.search(r"\bstation (?:flags?|findings?|warnings?)\b", low)):
        return _bounded("station_findings", low, known_subjects)
    if (re.search(r"\btemperature\b", low)
            or re.search(r"\bhow (?:cold|hot|warm)\b", low)
            or re.fullmatch(r"(?:what(?:'s| is)|how(?:'s| is)) (?:(?:the|my|our) )?weather(?: like)?(?: (?:at (?:my|our|the) (?:farm|orchard)|right now|now|today|here))*", low)):
        return _bounded("temperature", low, known_subjects)
    if re.search(r"\b(?:estimate|readings?)\b.*\b(?:reliable|usable|available|okay)\b", low):
        return _bounded("station_health", low, known_subjects)
    if re.fullmatch(r"(?:(?:thanks|thank you)[, ]*)?(?:how (?:far|much) (?:are we |am i )?behind(?: are we| am i| is .+)?|how many chill hours (?:are we|am i) (?:behind|ahead)|what(?:'s| is) (?:the )?(?:chill )?(?:gap|difference))", low):
        return _bounded("history_gap", low, known_subjects) if context else "needs_context"
    if re.search(r"\b(how (?:far|much) (?:are we )?behind|behind (?:are we|the usual)|historical|past years?|last year|usual pace|compare[ds]?|comparison)\b", low):
        domain = re.search(r"\b(chill|chilling|orchard|farm|station|temperature)\b", low)
        elliptical = re.fullmatch(r"(?:(?:thanks|thank you)[, ]*)?(?:how far (?:are we )?behind(?: are we)?|how much (?:are we )?behind|compare (?:past years?|historical (?:chill )?pace)|compared? (?:with|to) last year|historical pace|last year|what about last year|are we behind|how (?:does|do) (?:that|we|this) compare)", low)
        if domain or elliptical:
            return _bounded("history", low, known_subjects) if context else "needs_context"
        return "clarify" if re.search(r"\b(?:chill|weather|station|farm|orchard)\b", low) else "off_topic"
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
        return context if context in QUESTIONS or context in LOCAL_INTENTS or context in {"temperature", "temperature_normal", "freshness", "station_findings", "weather_history_unavailable", "humidity", "wind", "solar_radiation", "weather_variables"} else "needs_context"
    if re.search(r"\b(chill|chilling|station|coverage|temperature|weather|readings?|estimate|data)\b", low):
        return "clarify"
    if re.search(r"\b(monitoring|monitor|farm|orchard|location|growing|crop)\b", low):
        return "setup_help"
    if "?" in text and re.search(r"\b(got it|okay|understood)\b", low):
        return "clarify"
    if re.match(r"\s*(?:(?:i|we)\s+(?:(?:mainly|mostly|primarily)\s+)?(?:grow|have|am growing|are growing)|(?:i'm|we're)\s+growing|growing)\b", text, re.I):
        return "clarify"
    return "off_topic"


def crops_from(text):
    """Keep supplied crop metadata, never infer a variety or crop requirement."""
    if (not isinstance(text, str) or len(text) > 240 or OFF_TOPIC.search(text)
            or re.search(r"[?{}<>\n\r]|\b(ignore|instructions?|system|prompt|turn off|disable|enable|send|text me)\b", text, re.I)
            or re.match(r"\s*(?:what|which|how|where|when|why|do|does|can|could|should|would|is|are|tell me|show me|compare|explain)\b", text, re.I)):
        return None
    aliases = {"almond": "almond", "walnut": "walnut", "pistachio": "pistachio", "grape": "grape",
               "citrus": "citrus", "tomato(?:es)?": "tomato", "olive": "olive", "cherr(?:y|ies)": "cherry",
               "apple": "apple", "peach(?:es)?": "peach", "pear": "pear", "prune": "prune",
               "plum": "plum", "apricot": "apricot", "strawberr(?:y|ies)": "strawberry", "avocado": "avocado",
               "corn": "corn", "wheat": "wheat", "lettuce": "lettuce", "rice": "rice",
               "blueberr(?:y|ies)": "blueberry", "dragon ?fruit": "dragon fruit", "potato(?:es)?": "potato",
               "onion": "onion", "carrot": "carrot", "broccoli": "broccoli", "cauliflower": "cauliflower",
               "soybean": "soybean", "cotton": "cotton", "alfalfa": "alfalfa", "kiwi": "kiwi"}
    description = text.strip()
    body = description.rstrip(".!").strip()
    reversed_declaration = re.fullmatch(
        r"(.+?)\s+(?:are|is)\s+what\s+(?:i(?:['’]?m| am)|we(?:['’]?re| are))\s+growing", body, re.I)
    declaration = re.match(r"^(?:(?:i|we)\s+(?:(?:mainly|mostly|primarily|currently)\s+)?(?:grow|have|am growing|are growing)|(?:i'm|we're)\s+growing|growing)\s+", body, re.I)
    if reversed_declaration:
        body = reversed_declaration.group(1).strip()
        declaration = True
    elif declaration:
        body = body[declaration.end():].strip()
    else:
        body = re.sub(r"^(?:my|our)\s+", "", body, flags=re.I)
    if re.search(r"[;+]|\b(?:with|plus|or)\b|\bas well as\b", body, re.I):
        return None
    explicit = re.search(r"\b(?:variety|cultivar)\s*[:=]?\s+([\w '-]{1,60})$", body, re.I)
    explicit_variety = explicit.group(1).strip() if explicit else None
    if explicit:
        if re.search(r",|\band\b|[&/]", explicit_variety, re.I):
            return None
        body = body[:explicit.start()].rstrip(" ,:=")
    pieces = [part.strip() for part in re.split(r",\s*(?:and\b\s*)?|\band\b|[&/]", body, flags=re.I)]
    if not 1 <= len(pieces) <= 12 or any(not piece for piece in pieces):
        if declaration:
            raise ValueError("Provide between one and twelve actual crops")
        return None
    filler = {"organic", "some", "mostly", "mainly", "primarily", "a", "an", "the", "we", "i", "grow", "growing", "have", "our", "my"}
    crops, varieties, known_count = [], [], 0
    for piece in pieces:
        piece = re.sub(r"^(?:(?:i|we)\s+(?:(?:mainly|mostly|primarily|currently)\s+)?(?:grow|have|am growing|are growing)|(?:i'm|we're)\s+growing|growing)\s+", "", piece, count=1, flags=re.I)
        if re.search(
                r"\b(?:no|not|never|nothing|else|don['’]?t|doesn['’]?t|can['’]?t|won['’]?t|"
                r"i|we|they|you|are|is|was|were|will|would|should|could|might|must|"
                r"grow|growing|have|had|want|planning|planting)\b|"
                r"\b(?:next|last|this)\s+(?:year|season)\b|\bother\s+crops?\b", piece, re.I):
            return None
        if (not re.fullmatch(r"[\w][\w '-]{0,79}", piece) or not re.search(r"[^\W\d_]", piece)
                or re.search(r"\b(?:what|which|how|would|should|could|can|want|plan|because|then|please|not|or)\b", piece, re.I)
                or set(piece.lower().split()) <= filler):
            return None
        matches = [(match, value) for pattern, value in aliases.items()
                   for match in re.finditer(r"\b(?:" + pattern + r")s?\b", piece, re.I)]
        if len(matches) > 1:
            # Compound crop/variety wording needs clarification rather than a
            # partial match that quietly discards another supplied crop.
            return None
        variety = None
        if matches:
            match, crop = matches[0]
            if piece[match.end():].strip().lower() not in {"", "trees", "vines", "plants", "crops"}:
                return None
            known_count += 1
            prefix = piece[:match.start()].strip()
            if crop == "almond" and prefix.lower() == "nonpareil":
                variety = "Nonpareil"
            elif (prefix and re.fullmatch(r"[A-Z][A-Za-z'-]*(?: [A-Z][A-Za-z'-]*){0,2}", prefix)
                  and not set(prefix.lower().split()) & filler):
                variety = prefix
        else:
            # Generic crop names are permitted by the profile contract. Keep
            # unknown list entries instead of retaining only recognized nouns.
            if re.search(r"\b(?:on|at|because|when|where|for|to|weather|data|estimate|station|notifications?|alerts?|question)\b", piece, re.I):
                return None
            crop = piece.lower()
        crops.append(crop)
        varieties.append(variety)
    if not declaration and not known_count:
        return None
    if not 1 <= len(crops) <= 12:
        raise ValueError("Provide between one and twelve actual crops")
    crops = list(dict.fromkeys(crops))
    variety = (explicit_variety or varieties[0]) if len(pieces) == 1 else None
    if explicit_variety and varieties[0] and explicit_variety.casefold() != varieties[0].casefold() and len(pieces) == 1:
        return None
    return {"crops": crops, "variety": variety, "crop_description": description}
