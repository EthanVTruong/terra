"""Contextual model decisions and grounding; no credentials or live requests."""
from copy import deepcopy
import json

import pytest

from terra_monitoring.conversation.engine import EngineUnavailable, MAX_HISTORY, respond


def packet(text="What does that mean for my orchard?"):
    return {"request": {"text": text, "reply_to": "saved-note"},
            "setup": {"stage": "complete", "crops": ["peach"]},
            "orchard": {"id": "farm-a", "name": "North orchard", "mode": "current"},
            "monitoring": {"status": "pending", "reason": "Coverage is incomplete."},
            "facts": [{"id": "weather_support", "text": "Weather support is incomplete; the orchard estimate cannot be updated reliably."},
                      {"id": "temperature", "text": "The saved orchard temperature estimate was 46.5°F at 08:00 PST."}],
            "history": [{"id": "turn-a", "role": "user", "text": "Why did you send that alert?"},
                        {"id": "turn-b", "role": "assistant", "text": "Weather support is incomplete.",
                         "data": {"focus": "weather_support", "fact_ids": ["weather_support"]}}],
            "references": {"event_ids": ["saved-event"]}, "capabilities": {"settings_changes": False}}


def answer(**changes):
    return {"outcome": "message", "text": "The data is too patchy to trust an updated orchard estimate.",
            "fact_ids": ["weather_support"], "focus": "weather_support", **changes}


class Generator:
    def __init__(self, output=None):
        self.output = answer() if output is None else output
        self.calls = []

    def generate(self, **request):
        self.calls.append(deepcopy(request))
        result = self.output(request) if callable(self.output) else self.output
        return {"status": "completed", "text": json.dumps(result)}


@pytest.mark.parametrize("text", [
    "The cause is not known because the SMS was delivered.",
    "The cause is not confirmed: irrigate immediately.",
    "The cause is not established although the SMS was delivered.",
    "The cause is not confirmed\u2014irrigate immediately.",
])
def test_a_negated_cause_does_not_waive_a_later_positive_assertion(text):
    generator = Generator(answer(text=text))
    with pytest.raises(EngineUnavailable):
        respond(generator, packet())
    assert len(generator.calls) == 1


def test_actual_question_and_recent_dialogue_reach_the_selected_generator():
    source = packet()
    before = deepcopy(source)
    generator = Generator()
    result = respond(generator, source)
    assert len(generator.calls) == 1
    sent = generator.calls[0]["packet"]
    assert sent["request"] == source["request"]
    assert sent["history"][0]["text"] == "Why did you send that alert?"
    assert sent["history"][1]["data"]["focus"] == "weather_support"
    assert sent["references"]["event_ids"] == ["saved-event"]
    assert result["text"] == generator.output["text"]
    assert result["provider"] == "chatgpt_plan" and result["generation_outcome"] == "completed"
    assert source == before


@pytest.mark.parametrize("text", ["What additional context do you need from me?",
                                    "Does this represent an anomaly?", "Tell me more", "What's the implication?"])
def test_ordinary_followups_are_not_dropped_by_a_local_vocabulary_gate(text):
    generator = Generator(answer(outcome="clarify", text="Do you mean the flagged station or the orchard estimate?", focus="weather"))
    result = respond(generator, packet(text))
    assert result["outcome"] == "clarify"
    assert generator.calls[0]["packet"]["request"]["text"] == text


def test_model_can_ask_for_the_actual_missing_setup_step():
    source = packet("What else do you need?")
    source.update(setup={"stage": "location", "crops": ["peach"]}, facts=[], history=[])
    generator = Generator(answer(outcome="clarify", text="Can you use Share location where you grow?", fact_ids=[], focus="location"))
    result = respond(generator, source)
    assert result["text"] == "Can you use Share location where you grow?"
    assert generator.calls[0]["packet"]["setup"]["stage"] == "location"


def test_model_may_choose_silence_or_an_attached_reaction():
    for outcome, text in (("none", "Thanks"), ("reaction", "Got it")):
        result = respond(Generator(answer(outcome=outcome, text=None, fact_ids=[], focus=None)), packet(text))
        assert result["outcome"] == outcome and result["text"] is None


def test_exact_values_are_inserted_only_from_cited_python_facts():
    def generated(request):
        fact = request["packet"]["facts"][1]
        assert fact["text"] == "The saved orchard temperature estimate was {{f1_n0}}°F at {{f1_n1}}:{{f1_n2}} PST."
        assert fact["fields"]["{{f1_n0}}"]["value"] == "46.5"
        return answer(text="The saved estimate was {{f1_n0}}°F; the incomplete coverage limits an update.",
                      fact_ids=["temperature", "weather_support"], focus="temperature")
    result = respond(Generator(generated), packet())
    assert result["text"] == "The saved estimate was 46.5°F; the incomplete coverage limits an update."


@pytest.mark.parametrize("changes", [
    {"text": "The estimate is 999°F."},
    {"text": "The estimate is forty degrees."},
    {"text": "The estimate is one degree."},
    {"text": "The estimate is {{f1_n0}}°F."},
    {"text": "The estimate is {{f9_n9}}°F."},
    {"text": "The estimate is {{not_owned}}°F."},
    {"fact_ids": ["other-farm-temperature"]},
    {"fact_ids": ["weather_support", "weather_support"]},
    {"focus": "other-farm"},
    {"text": "I have turned off your alerts."},
    {"text": "I've disabled browser notifications."},
    {"text": "You should irrigate the orchard immediately."},
    {"text": "The station is broken."},
    {"text": "Your trees are damaged."},
    {"text": "Tomorrow will freeze."},
    {"text": "This definitely predicts bloom."},
    {"text": "I'll keep watching and text you."},
    {"text": "The estimate is reliable."},
    {"text": "Monitoring is active."},
    {"text": "Open https://example.com to fix it."},
    {"text": "<script>alert('oops')</script>"},
    {"outcome": "clarify", "text": "Which station? Which orchard?"},
    {"outcome": "reaction", "text": "Okay"},
    {"outcome": "none", "text": None},
    {"outcome": "message", "text": None},
    {"fact_ids": []},
    {"extra": "unexpected"},
])
def test_invalid_or_ungrounded_model_outputs_never_become_a_reply(changes):
    generator = Generator(answer(**changes))
    with pytest.raises(EngineUnavailable) as caught:
        respond(generator, packet())
    assert caught.value.reason == "invalid_response"
    assert len(generator.calls) == 1


def test_explaining_unsupported_inferences_as_a_limit_is_allowed():
    result = respond(Generator(answer(text="That comparison doesn't establish crop damage or a failed sensor.")), packet())
    assert "doesn't establish" in result["text"]


def test_proactive_mode_requires_one_answer_all_events_and_no_question():
    source = packet()
    source["capabilities"]["communication"] = "proactive_alert"
    for output in (answer(), answer(text="Do you mean the estimate?", outcome="clarify")):
        with pytest.raises(EngineUnavailable):
            respond(Generator(output), source)
    output = answer(fact_ids=["weather_support", "temperature"])
    assert respond(Generator(output), source)["outcome"] == "message"


def test_history_is_bounded_without_losing_the_latest_turn():
    source = packet()
    source["history"] = [{"id": f"turn-{index}", "role": "user", "text": f"Earlier question {index}"}
                         for index in range(MAX_HISTORY + 6)]
    generator = Generator()
    respond(generator, source)
    sent = generator.calls[0]["packet"]["history"]
    assert len(sent) == MAX_HISTORY and sent[-1]["id"] == f"turn-{MAX_HISTORY + 5}"
    assert sent[0]["id"] == "turn-6"


def test_reference_scope_reaches_the_model_without_replacing_the_saved_note():
    source = packet()
    source["facts"][0].update(scope="reference", source_note_id="old-note", event_ids=["old-event"], evidence_id="old-evidence")
    generator = Generator()
    respond(generator, source)
    sent = generator.calls[0]["packet"]["facts"][0]
    assert sent["scope"] == "reference" and sent["source_note_id"] == "old-note"
    assert sent["event_ids"] == ["old-event"] and sent["evidence_id"] == "old-evidence"


def test_replay_facts_cannot_be_promoted_to_live_conditions():
    source = packet()
    source["orchard"]["mode"] = "replay"
    with pytest.raises(EngineUnavailable):
        respond(Generator(answer(text="Those are live conditions right now.")), source)
    assert respond(Generator(answer(text="During this replay, coverage limits the updated estimate.")), source)["outcome"] == "message"


@pytest.mark.parametrize("text", [
    "Your browser alerts are now enabled.",
    "Browser alerts are enabled.",
    "Browser alerts aren't off.",
    "Browser alerts have been disabled.",
    "The farm location has been updated.",
    "The farm location was updated.",
    "Your crops were successfully saved.",
    "SMS delivered.",
    "The SMS was sent.",
    "Your message is queued.",
    "Delivered.",
])
def test_passive_action_preference_and_delivery_fabrications_are_rejected(text):
    source = packet()
    source["facts"] = [{"id": "profile_notifications", "text": "Browser alerts are off; in-chat updates stay available."}]
    output = answer(text=text, fact_ids=["profile_notifications"], focus="notifications")
    with pytest.raises(EngineUnavailable):
        respond(Generator(output), source)


@pytest.mark.parametrize("text", ["Browser alerts are off; in-chat updates stay available.",
                                  "Browser alerts are disabled in the saved preferences.",
                                  "I haven't changed your alerts.", "I can't confirm the SMS was delivered."])
def test_grounded_saved_preference_and_honest_nonaction_wording_are_retained(text):
    source = packet()
    source["facts"] = [{"id": "profile_notifications", "text": "Browser alerts are off; in-chat updates stay available."}]
    assert respond(Generator(answer(text=text, fact_ids=["profile_notifications"], focus="notifications")), source)["text"] == text


def test_a_natural_which_one_clarification_is_not_an_invented_measurement():
    source = packet()
    output = answer(outcome="clarify", text="Which one do you mean?", fact_ids=[], focus="weather")
    assert respond(Generator(output), source)["text"] == "Which one do you mean?"
    output["text"] = "Which one is one degree?"
    with pytest.raises(EngineUnavailable):
        respond(Generator(output), source)


def test_delivery_requires_a_cited_authoritative_transport_receipt():
    source = packet()
    source["facts"] = [{"id": "sms_receipt", "kind": "transport_receipt", "status": "delivered",
                        "text": "The SMS provider confirmed delivery of the saved notification."}]
    output = answer(text="The SMS was delivered.", fact_ids=["sms_receipt"], focus="notifications")
    assert respond(Generator(output), source)["text"] == "The SMS was delivered."
    source["facts"][0]["status"] = "queued"
    with pytest.raises(EngineUnavailable):
        respond(Generator(output), source)


@pytest.mark.parametrize("scope,text", [
    ("reference", "The orchard temperature is {{f0_n0}}°F right now."),
    ("reference", "The orchard temperature is {{f0_n0}}°F."),
    ("saved_note", "The current orchard temperature is {{f0_n0}}°F."),
])
def test_reference_values_cannot_be_claimed_as_current_even_for_a_live_farm(scope, text):
    source = packet()
    source["facts"] = [{"id": "reference_temperature", "scope": scope,
                        "text": "Saved assessment: the orchard temperature estimate was 40°F."}]
    output = answer(text=text, fact_ids=["reference_temperature"], focus="temperature")
    with pytest.raises(EngineUnavailable):
        respond(Generator(output), source)
    output["text"] = "The saved assessment's orchard estimate was {{f0_n0}}°F."
    assert "was 40°F" in respond(Generator(output), source)["text"]


def test_citing_a_current_unrelated_fact_cannot_make_a_reference_token_current():
    source = packet()
    source["facts"] = [{"id": "reference_temperature", "scope": "reference", "text": "Saved estimate was 40°F."},
                       {"id": "current_humidity", "scope": "current", "text": "Current station humidity was 65%."}]
    output = answer(text="The orchard temperature is {{f0_n0}}°F right now.",
                    fact_ids=["reference_temperature", "current_humidity"], focus="temperature")
    with pytest.raises(EngineUnavailable):
        respond(Generator(output), source)


@pytest.mark.parametrize("text", ["The orchard temperature is {{f0_n0}}°F.",
                                  "The temperature is normal.", "Current temperature is available."])
def test_stale_last_usable_temperature_cannot_be_claimed_as_current(text):
    source = packet()
    source["facts"] = [{"id": "current_temperature", "scope": "current", "current_verified": False,
                        "text": "The last usable estimate was 40°F; it is stale and current weather support is unavailable."}]
    output = answer(text=text, fact_ids=["current_temperature"], focus="temperature")
    with pytest.raises(EngineUnavailable):
        respond(Generator(output), source)
    output["text"] = "The last usable estimate was {{f0_n0}}°F; it doesn't establish current temperature."
    assert "was 40°F" in respond(Generator(output), source)["text"]


def _saved_replay_limitation():
    """Sanitized authoritative facts and wording from the captured false positive."""
    source = packet("Does that represent an anomaly?")
    source["orchard"]["mode"] = "injected"
    source["facts"] = [
        {"id": "profile_monitor", "scope": "current",
         "text": "Saved replay readings aren't live monitoring; the live monitor is off for this farm."},
        {"id": "chill_total", "scope": "current",
         "text": "Demo replay: Demo orchard: estimated chill is 0 hours with incomplete coverage. As of Mar 11, 3:00 AM PST."},
        {"id": "reference_station_findings", "scope": "reference",
         "text": "Replay only: No supporting-station discrepancy was flagged as of Mar 11, 2026, 3:00 AM PST."},
    ]
    text = ("The saved replay shows no supporting-station discrepancy, which is some reassurance about that snapshot, "
            "but the orchard estimate is still based on incomplete coverage and isn’t live-verified. "
            "The saved chill total is {{f1_n0}} hours with incomplete coverage, so it should be treated as provisional "
            "rather than a dependable season total.")
    output = answer(text=text, fact_ids=["reference_station_findings", "chill_total", "profile_monitor"], focus="chill_total")
    return source, output


def test_genuine_saved_replay_coverage_limitation_is_not_a_current_measurement():
    source, output = _saved_replay_limitation()
    generator = Generator(output)
    result = respond(generator, source)
    assert result["text"] == output["text"].replace("{{f1_n0}}", "0")
    assert result["provider"] == "chatgpt_plan" and result["generation_outcome"] == "completed"
    assert len(generator.calls) == 1


def test_incomplete_coverage_cannot_reverse_explicit_complete_source_coverage():
    source, output = _saved_replay_limitation()
    source["facts"][1]["text"] = source["facts"][1]["text"].replace("incomplete coverage", "complete coverage")
    with pytest.raises(EngineUnavailable):
        respond(Generator(output), source)


def test_qualitative_coverage_limitation_is_not_a_present_weather_measurement():
    source, output = _saved_replay_limitation()
    output["text"] = "The orchard estimate is still based on incomplete coverage."
    assert respond(Generator(output), source)["text"] == output["text"]


@pytest.mark.parametrize("claim", [
    "The orchard temperature is {{f3_n0}}°F right now.",
    "The temperature is normal.",
    "Current temperature is available.",
])
def test_grounded_snapshot_limitation_cannot_hide_a_later_current_weather_claim(claim):
    source, output = _saved_replay_limitation()
    source["facts"].append({"id": "reference_temperature", "scope": "reference", "text": "Saved estimate was 40°F."})
    output["fact_ids"].append("reference_temperature")
    output["text"] += " " + claim
    with pytest.raises(EngineUnavailable):
        respond(Generator(output), source)


def test_second_genuine_replay_explanation_keeps_monitor_status_separate_from_weather():
    source, output = _saved_replay_limitation()
    source["facts"][2].update(id="current_station_findings", scope="current")
    source["facts"].append({"id": "chill_history", "text": "A reliable historical comparison isn't available yet."})
    output.update(
        text=("Treat the orchard estimate cautiously: the saved replay shows an estimated chill total of {{f1_n0}} hours "
              "with incomplete coverage, so it may not represent the full season. No supporting-station discrepancy "
              "was flagged in that replay, but readings are not live, the live monitor is off, and the estimate can't "
              "be validated against a reliable historical comparison."),
        fact_ids=["chill_total", "current_station_findings", "profile_monitor", "chill_history"])
    generator = Generator(output)
    result = respond(generator, source)
    assert result["text"] == output["text"].replace("{{f1_n0}}", "0")
    assert result["provider"] == "chatgpt_plan" and len(generator.calls) == 1


@pytest.mark.parametrize("text", [
    "The live monitor is off.",
    "The live monitor is off today.",
    "The current monitor is off.",
    "The estimate is based on patchy weather data.",
    "The orchard estimate remains dependent on partial coverage.",
])
def test_monitor_status_and_qualitative_support_explanations_are_not_weather_values(text):
    source, output = _saved_replay_limitation()
    output["text"] = text
    assert respond(Generator(output), source)["text"] == text


@pytest.mark.parametrize("prefix,subject", [
    ("Saved replay: ", "the orchard temperature"),
    ("In the saved replay, ", "the orchard estimate"),
])
@pytest.mark.parametrize("tense", ["was", "is"])
def test_sentence_historical_frame_survives_colon_and_comma(prefix, subject, tense):
    source = packet()
    source["facts"] = [{"id": "reference_temperature", "scope": "reference", "text": "The saved orchard temperature estimate was 40°F."}]
    text = prefix + subject + " " + tense + " {{f0_n0}}°F."
    result = respond(Generator(answer(text=text, fact_ids=["reference_temperature"], focus="temperature")), source)
    assert result["text"] == text.replace("{{f0_n0}}", "40")


@pytest.mark.parametrize("text", [
    "Saved replay: the orchard temperature is {{f0_n0}}°F right now.",
    "In the saved replay, current orchard temperature is {{f0_n0}}°F.",
    "Saved replay: the temperature is normal right now.",
    "In the saved replay, current temperature is available.",
    "Saved replay: the orchard temperature is {{f0_n0}}°F, right now.",
])
def test_explicit_current_weather_claim_overrides_historical_sentence_frame(text):
    source = packet()
    source["facts"] = [{"id": "reference_temperature", "scope": "reference", "text": "The saved orchard temperature estimate was 40°F."}]
    with pytest.raises(EngineUnavailable):
        respond(Generator(answer(text=text, fact_ids=["reference_temperature"], focus="temperature")), source)


@pytest.mark.parametrize("bad", [
    {"status": "incomplete", "text": "partial"},
    {"status": "completed", "refusal": True, "text": "{}"},
    {"status": "completed", "text": "not JSON"},
    {"status": "completed", "text": "x" * 8001},
    None,
])
def test_refused_partial_or_malformed_provider_results_fail_closed(bad):
    class Bad:
        calls = 0

        def generate(self, **kwargs):
            self.calls += 1
            return bad
    generator = Bad()
    with pytest.raises(EngineUnavailable):
        respond(generator, packet())
    assert generator.calls == 1


def test_provider_failure_never_retries_or_exposes_the_exception():
    class Broken:
        calls = 0

        def generate(self, **kwargs):
            self.calls += 1
            raise RuntimeError("PRIVATE credential and request")
    generator = Broken()
    with pytest.raises(EngineUnavailable) as caught:
        respond(generator, packet())
    assert generator.calls == 1 and "PRIVATE" not in str(caught.value)
    assert caught.value.reason == "provider_unavailable"


def test_no_selected_generator_and_invalid_context_make_no_request():
    with pytest.raises(EngineUnavailable) as caught:
        respond(None, packet())
    assert caught.value.reason == "not_connected"
    generator = Generator()
    for change in ({"facts": [{"id": "bad", "text": "unowned {{number}}"}]},
                   {"facts": [{"id": "x", "text": "first"}, {"id": "x", "text": "second"}]},
                   {"request": {"text": "x" * 2001}}, {"monitoring": {"invalid": float("nan")}}):
        with pytest.raises(EngineUnavailable) as caught:
            respond(generator, {**packet(), **change})
        assert caught.value.reason == "invalid_context"
    assert generator.calls == []


@pytest.mark.parametrize("text", [
    "The saved estimate was {{f0_n0}}°C.",
    "The saved estimate was minus {{f0_n0}}°F.",
    "The saved estimate was -{{f0_n0}}°F.",
    "The saved estimate was {{f0_n0}} km/h.",
])
def test_numeric_binding_cannot_change_unit_or_sign(text):
    source = packet()
    source["facts"] = [{"id": "temperature", "text": "The saved orchard temperature estimate was 46.5°F."}]
    with pytest.raises(EngineUnavailable):
        respond(Generator(answer(text=text, fact_ids=["temperature"], focus="temperature")), source)


@pytest.mark.parametrize("text", [
    "Station {{f0_n0}} observed {{f0_n2}} mph, compared with a peer reference of {{f0_n1}} mph.",
    "Station {{f0_n0}} observed {{f0_n3}} mph, compared with a peer reference of {{f0_n2}} mph.",
    "The saved estimate was {{f0_n0}}°F.",
    "Station {{f0_n0}} observed {{f0_n1}} km/h, versus a peer reference of {{f0_n2}} mph.",
    "The reading was {{f0_n1}} mph.",
])
def test_numeric_observed_peer_departure_and_station_roles_must_be_proven(text):
    source = packet()
    source["facts"] = [{"id": "wind", "text": "Saved station 6 wind speed was 60 mph, compared with a peer reference of 6 mph; departure +54 mph, provisional discrepancy, cause unconfirmed."}]
    with pytest.raises(EngineUnavailable):
        respond(Generator(answer(text=text, fact_ids=["wind"], focus="wind")), source)


@pytest.mark.parametrize("text", [
    "Station {{f0_n0}} observed {{f0_n1}} mph, compared with a peer reference of {{f0_n2}} mph.",
    "The observed wind was {{f0_n1}} mph; the peer reference was {{f0_n2}} mph, with departure {{f0_n3}} mph.",
    "The station read {{f0_n1}} mph against an expected {{f0_n2}} mph; the cause is unconfirmed.",
])
def test_correct_natural_numeric_role_paraphrases_remain_valid(text):
    source = packet()
    source["facts"] = [{"id": "wind", "text": "Saved station 6 wind speed was 60 mph, compared with a peer reference of 6 mph; departure +54 mph, provisional discrepancy, cause unconfirmed."}]
    assert respond(Generator(answer(text=text, fact_ids=["wind"], focus="wind")), source)["provider"] == "chatgpt_plan"


@pytest.mark.parametrize("text", [
    "The cause is unconfirmed, so irrigate immediately.",
    "The cause is unconfirmed, and the SMS was delivered.",
    "The cause is unknown and the estimate is reliable.",
    "The cause is unconfirmed, then monitoring is active.",
    "The cause is unknown but the station is broken.",
    "The cause is insufficient to explain, so spray the orchard.",
    "Irrigate immediately because the cause is unconfirmed.",
    "Without more data irrigate immediately.",
    "Not only was the SMS delivered, monitoring is active.",
])
def test_uncertainty_in_one_assertion_cannot_excuse_another_positive_claim(text):
    with pytest.raises(EngineUnavailable):
        respond(Generator(answer(text=text)), packet())


@pytest.mark.parametrize("text", [
    "The cause is unconfirmed, so do not irrigate from this comparison alone.",
    "The cause is unconfirmed, and the SMS was not delivered.",
    "The cause is unknown, and that doesn't establish a broken sensor.",
    "The cause is unconfirmed; the estimate remains incomplete.",
    "Crop damage is unconfirmed.",
    "There is no evidence of crop damage.",
])
def test_scoped_negative_and_uncertainty_statements_remain_valid(text):
    assert respond(Generator(answer(text=text)), packet())["provider"] == "chatgpt_plan"


@pytest.mark.parametrize("source_text,good,bad", [
    ("Observed humidity was 60%, peer reference 45%; departure 15 percentage points.",
     "Observed humidity was {{f0_n0}}%; peer reference {{f0_n1}}%, departure {{f0_n2}} percentage points.",
     "Observed humidity was {{f0_n0}} percentage points; peer reference {{f0_n1}}%."),
    ("Station 6 solar radiation was 400 W/m².",
     "Observed solar radiation was {{f0_n1}} W/m².",
     "Observed solar radiation was {{f0_n1}} mph."),
    ("The saved orchard temperature estimate was 46.5°F on Oct 24, 2026 at 08:00 AM PST.",
     "The saved estimate was {{f0_n0}}°F as of Oct {{f0_n1}}, {{f0_n2}} at {{f0_n3}}:{{f0_n4}} AM PST.",
     "The saved estimate was {{f0_n3}}°F."),
    ("Estimated chill counts hours in the 32–45°F range.",
     "The saved chill range is {{f0_n0}}–{{f0_n1}}°F.",
     "The saved chill range is {{f0_n1}}–{{f0_n0}}°F."),
])
def test_typed_units_and_native_date_clock_range_groups(source_text, good, bad):
    source = packet()
    source["facts"] = [{"id": "typed", "text": source_text}]
    assert respond(Generator(answer(text=good, fact_ids=["typed"], focus="weather")), source)["provider"] == "chatgpt_plan"
    with pytest.raises(EngineUnavailable):
        respond(Generator(answer(text=bad, fact_ids=["typed"], focus="weather")), source)


def test_copying_a_valid_clock_does_not_allow_reusing_its_token_as_a_measurement():
    source = packet()
    source["facts"] = [{"id": "clock", "text": "The saved reading was recorded at 08:00 PST."}]
    text = "The clock was {{f0_n0}}:{{f0_n1}} PST; the saved estimate was {{f0_n0}}°F."
    with pytest.raises(EngineUnavailable):
        respond(Generator(answer(text=text, fact_ids=["clock"], focus="temperature")), source)
