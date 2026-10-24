"""Saved-weather replay reuses the production writer without activating SMS."""
from terra_monitoring.farm_ops.demo import run_demo
from terra_monitoring.farm_ops.store import Store
from terra_monitoring.farm_ops.writer import fallback


def test_explicit_replay_writer_is_used_once_per_immutable_note(tmp_path):
    class PreviewWriter:
        def __init__(self):
            self.packets = []

        def write(self, packet):
            self.packets.append(packet)
            return {"text": fallback(packet), "provider": "controlled_test", "outcome": "completed"}

    store, writer = Store(tmp_path / "replay.sqlite3"), PreviewWriter()
    result = run_demo(store, "writer-replay", writer=writer)
    assert result["status"] == "completed"
    assert [p["estimate_status"] for p in result["phases"]] == ["ok", "insufficient_data", "ok", "ok", "ok"]
    assert len(result["messages"]) == len(writer.packets) == 3
    assert all(m["generation"] == "controlled_test" and m["text"].startswith("Demo replay:")
               and m["transport"] == "local_inbox" for m in result["messages"])
    assert "explicitly supplied writer" in result["note"]
    ids = {m["id"] for m in result["messages"]}
    again = run_demo(Store(store.path), "writer-replay", writer=writer)
    assert again["status"] == "already_saved" and len(writer.packets) == 3
    assert {m["id"] for m in again["messages"]} == ids
    for message in result["messages"]:
        details = store.details("writer-replay", message["id"])
        assert details["message"]["llm_input"] in writer.packets
        assert details["events"] and details["assessments"]
        assert details["transport_receipt"] is None
