"""First saved weather checks must not announce an unverified live worker."""
from copy import deepcopy

import pytest

from terra_monitoring.farm_ops.conversational_writer import ConversationalWriter
from terra_monitoring.farm_ops.writer import Writer, message_input


@pytest.mark.parametrize("mode", ["current", "injected"])
def test_first_weather_note_confirms_saved_farm_without_claiming_live_monitor(mode):
    profile = {"name": "QA farm", "crop": "peach", "variety": None,
               "demo_location": False, "mode": mode}
    event = {"id": "first-check", "type": "welcome", "transition": "initial",
             "severity": "info", "occurred_at": "2026-01-14T16:00:00+00:00",
             "facts": {"hours": 180.0, "complete": True,
                       "data_through": "2026-01-14T16:00:00+00:00",
                       "comparison_available": True}}
    packet = message_input(profile, [event], "2026-01-14T16:00:00+00:00")
    original = deepcopy(packet)
    result = ConversationalWriter(Writer(key="", model="")).write(packet)
    assert "Your farm is saved" in result["text"]
    assert "monitoring is connected" not in result["text"].lower()
    assert "180 hours" in result["text"]
    assert "weather support is usable" in result["text"].lower()
    assert (result["text"].startswith("Demo replay:")) == (mode == "injected")
    assert packet == original
