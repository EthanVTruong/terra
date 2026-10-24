"""Crop setup must preserve the complete grower's list without paid calls."""
import pytest

from terra_monitoring.conversation.routing import crops_from
from test_terra_conversation import LOCATION, conversation


@pytest.mark.parametrize("text,crops", [
    ("I grow peaches and lychees", ["peach", "lychees"]),
    ("I grow lychees and peaches", ["lychees", "peach"]),
    ("We mainly grow apples, lychees, and walnuts.", ["apple", "lychees", "walnut"]),
    ("peaches & lychees", ["peach", "lychees"]),
    ("peaches/lychees", ["peach", "lychees"]),
    ("I grow passion fruit and lychees", ["passion fruit", "lychees"]),
    ("Gala apples and Nonpareil almonds", ["apple", "almond"]),
    ("Gala apples and lychees", ["apple", "lychees"]),
    ("I grow lychees and apples variety Gala", ["lychees", "apple"]),
    ("I grow peaches and I grow lychees", ["peach", "lychees"]),
    ("almonds are what im growing", ["almond"]),
    ("Almonds are what I'm growing.", ["almond"]),
    ("Lychees are what I am growing", ["lychees"]),
    ("Apples and lychees are what we're growing", ["apple", "lychees"]),
])
def test_explicit_crop_lists_preserve_each_member(text, crops):
    parsed = crops_from(text)
    assert parsed["crops"] == crops
    assert parsed["variety"] is None
    assert parsed["crop_description"] == text


@pytest.mark.parametrize("text,crops", [
    ("I grow peaches and lychees", ["peach", "lychees"]),
    ("I grow lychees and peaches", ["lychees", "peach"]),
    ("I grow apples, lychees, and walnuts", ["apple", "lychees", "walnut"]),
    ("I grow passion fruit and lychees", ["passion fruit", "lychees"]),
    ("almonds are what im growing", ["almond"]),
    ("lychees and apples are what we are growing", ["lychees", "apple"]),
])
def test_complete_crop_list_survives_profile_save_without_model_usage(conversation, text, crops):
    service, monitor, writer = conversation
    state = service.message(request_id="crops", text=text)["state"]
    assert state["setup"]["crops"] == crops
    assert state["setup"]["stage"] == "location"
    result = service.action(request_id="pin", action="location", location=LOCATION)
    profile = monitor.store.orchard(result["orchard_id"])
    assert profile["crops"] == crops
    assert profile["crop_description"] == text and profile["variety"] is None
    assert profile["target"]["hours"] is None and not writer.packets


@pytest.mark.parametrize("text", [
    "we grow and", "I grow peaches and", "I grow peaches,,lychees",
    "I grow peaches and and lychees",
    "I grow peaches,a,b,c,d,e,f,g,h,i,j,k,l",
    "I grow a,b,c,d,e,f,g,h,i,j,k,l,peaches",
])
def test_invalid_or_overlong_list_cannot_partially_save(conversation, text):
    service, monitor, writer = conversation
    with pytest.raises(ValueError):
        service.message(request_id="invalid-crops", text=text)
    state = service.state()
    assert state["setup"]["stage"] == "crop"
    assert not state["turns"] and not state["pending_requests"]
    assert not monitor.store.orchards() and not writer.packets


@pytest.mark.parametrize("text", [
    "I grow peaches with lychees",
    "I grow peaches or lychees",
    "I grow peaches + lychees",
    "I grow peaches; lychees",
    "I grow peaches variety Redhaven and lychees",
    "I grow peaches and lychees; please add both",
    "I grow lychees and don't grow peaches",
    "I grow lychees and peaches are next year",
    "I grow peaches and lychees and nothing else",
    "I grow peaches and not lychees",
    "I grow peaches and lychees next season",
    "I grow peaches and other crops",
    "I grow peaches and lychees in Stardew Valley",
    "I grow lychees and peaches in Minecraft",
    "Are almonds what I'm growing?",
    "Almonds are what I'm growing?",
    "Almonds are what I'm growing next season",
    "Almonds are what I'm planning on growing",
    "Almonds are what I'm growing and peaches",
    "Almonds and nothing else are what I'm growing",
])
def test_ambiguous_crop_descriptions_do_not_partially_advance_setup(conversation, text):
    service, monitor, writer = conversation
    assert crops_from(text) is None
    state = service.message(request_id="ambiguous-crops", text=text)["state"]
    assert state["setup"]["stage"] == "crop"
    assert not monitor.store.orchards() and not writer.packets


@pytest.mark.parametrize("text,variety", [
    ("Gala apples", "Gala"),
    ("Nonpareil almonds", "Nonpareil"),
    ("I grow almonds variety Nonpareil", "Nonpareil"),
    ("I grow apples, cultivar Gala", "Gala"),
])
def test_single_crop_variety_metadata_is_preserved(text, variety):
    parsed = crops_from(text)
    assert len(parsed["crops"]) == 1 and parsed["variety"] == variety
    assert parsed["crop_description"] == text
