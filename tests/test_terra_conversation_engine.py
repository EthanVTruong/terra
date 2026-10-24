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
