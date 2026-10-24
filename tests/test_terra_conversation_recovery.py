"""Explicit recovery proves the old process exited; no inference is replayed."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from terra_monitoring.conversation.ownership import active, current_owner, owner_status
from terra_monitoring.conversation.service import RequestAlreadyCompleted, RequestInProgress
from test_terra_conversation import conversation, setup_farm


CHILD = r'''
import os, sys
from datetime import datetime
from terra_monitoring.conversation import ConversationService
from terra_monitoring.farm_ops.service import Monitor
from terra_monitoring.farm_ops.store import Store
class ExitWriter:
    def write(self, packet):
        print('reserved', flush=True)
        if sys.argv[3] == 'wait':
            sys.stdin.readline()
        os._exit(23)
monitor = Monitor(Store(sys.argv[1]), writer=ExitWriter(), clock=lambda: datetime.fromisoformat('2026-01-11T09:00:00+00:00'))
ConversationService(monitor).message(request_id='crashed-question', text='How much chill?', orchard_id=sys.argv[2])
'''


def spawn_owner(service, orchard, *, wait=False):
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "backend")}
    return subprocess.Popen([sys.executable, "-B", "-c", CHILD, str(service.store.path), orchard, "wait" if wait else "exit"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)


def test_real_exited_owner_can_be_acknowledged_without_replaying_request(conversation):
    service, monitor, writer = conversation
    orchard = setup_farm(service)
    child = spawn_owner(service, orchard)
    output, error = child.communicate(timeout=15)
    assert child.returncode == 23 and output.strip() == "reserved", error
    pending = service.state(orchard)["pending_requests"]
    assert pending[0]["owner_status"] == "exited" and pending[0]["reconcilable"]
    assert "pid" not in json.dumps(pending) and "started" not in json.dumps(pending)
    kwargs = {"request_id": "ack-exited", "target_request_id": "crashed-question", "confirm_uncertain": True, "orchard_id": orchard}
    result = service.reconcile(**kwargs)
    assert result["request_id"] == "ack-exited" and result["outcome"] == "none"
    assert result["event"]["kind"] == "notice" and result["event"]["data"]["type"] == "usage_uncertain"
    assert not result["state"]["pending_requests"]
    assert service.reconcile(**kwargs)["event"] == result["event"]
    old = service.message(request_id="crashed-question", text="How much chill?", orchard_id=orchard)
    assert old["request_id"] == "crashed-question" and old["resolution"] == "acknowledged_unknown"
    assert old["uncertain_usage"] and old["outcome"] == "none" and not writer.packets
    assert len([t for t in service.state(orchard)["turns"] if t["kind"] == "notice"]) == 1
    fresh = service.message(request_id="new-independent-question", text="How much chill?", orchard_id=orchard)
    assert fresh["outcome"] == "message" and len(writer.packets) == 1


def test_actual_live_owner_refuses_recovery_until_process_exits(conversation):
    service, _, writer = conversation
    orchard = setup_farm(service)
    child = spawn_owner(service, orchard, wait=True)
    try:
        assert child.stdout.readline().strip() == "reserved"
        pending = service.state(orchard)["pending_requests"][0]
        assert pending["owner_status"] == "active" and not pending["reconcilable"]
        kwargs = {"request_id": "ack-live", "target_request_id": "crashed-question", "confirm_uncertain": True, "orchard_id": orchard}
        with pytest.raises(RequestInProgress):
            service.reconcile(**kwargs)
        assert not [t for t in service.state(orchard)["turns"] if t["kind"] == "notice"]
    finally:
        child.communicate("\n", timeout=15)
    assert child.returncode == 23
    assert service.state(orchard)["pending_requests"][0]["reconcilable"]
    service.reconcile(**kwargs)
    assert not writer.packets


def test_owner_without_reliable_identity_never_expires(conversation):
    service, _, writer = conversation
    orchard = setup_farm(service)
    body = {"text": "How much chill?", "orchard_id": orchard, "reply_to": None}
    service._reserve("legacy-pending", orchard, body, "How much chill?")
    active(service.store.path, "legacy-pending", False)
    with service.store.connect(write=True) as db:
        db.execute("UPDATE tara_requests SET owner=NULL,created='2000-01-01T00:00:00+00:00' WHERE id='legacy-pending'")
    row = service.state(orchard)["pending_requests"][0]
    assert row["owner_status"] == "unknown" and not row["reconcilable"]
    with pytest.raises(RequestInProgress):
        service.reconcile(request_id="ack-unknown", target_request_id="legacy-pending", confirm_uncertain=True, orchard_id=orchard)
    assert not writer.packets


def test_completed_target_race_is_distinct_and_late_actual_completion_wins(conversation):
    service, _, writer = conversation
    orchard = setup_farm(service)
    actual = service.message(request_id="already-complete", text="How much chill?", orchard_id=orchard)
    with pytest.raises(RequestAlreadyCompleted):
        service.reconcile(request_id="unused-ack", target_request_id="already-complete", confirm_uncertain=True, orchard_id=orchard)
    assert len(writer.packets) == 1
    child = spawn_owner(service, orchard)
    child.communicate(timeout=15)
    ack = service.reconcile(request_id="ack-then-late", target_request_id="crashed-question", confirm_uncertain=True, orchard_id=orchard)
    user = service.message(request_id="crashed-question", text="How much chill?", orchard_id=orchard)["user_turn"]
    # A genuine eventual completion is authoritative, never replaced by an ack.
    service._finish("crashed-question", orchard, user, text="Actual saved response.", data={"intent": "chill_total"})
    original = service.message(request_id="crashed-question", text="How much chill?", orchard_id=orchard)
    assert original["assistant_turn"]["text"] == "Actual saved response."
    assert service.reconcile(request_id="ack-then-late", target_request_id="crashed-question", confirm_uncertain=True, orchard_id=orchard)["event"] == ack["event"]
    assert len(writer.packets) == 1


def test_pid_reuse_is_distinguished_by_real_process_start_identity():
    owner = current_owner()
    assert owner and owner_status(owner) == "active"
    old_start = str(int(owner["started"]) - 1) if sys.platform == "win32" else owner["started"].rsplit(":", 1)[0] + ":0" if sys.platform.startswith("linux") else "Mon Jan 01 00:00:00 2001"
    assert owner_status({**owner, "started": old_start}) == "exited"
    assert owner_status({**owner, "started": "unreliable-old-record"}) == "unknown"
    assert owner_status(None) == "unknown"
