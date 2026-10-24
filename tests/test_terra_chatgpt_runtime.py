"""Plan writer wiring through the real local runtime, with no external work."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import threading
import types

from fastapi.testclient import TestClient
import pytest

from terra_monitoring import runtime
from terra_monitoring.chatgpt import local
from terra_monitoring.farm_ops.service import Monitor
from terra_monitoring.farm_ops.store import Store
from terra_monitoring.farm_ops.writer import Writer

ORIGIN = "http://127.0.0.1:8512"
NOW = datetime(2026, 1, 12, 12, tzinfo=timezone.utc)


class Live:
    def __init__(self):
        self.calls = []

    def context(self):
        return {"schema_version": "terra-live-context-v1", "configured": False}

    def assess(self, query):
        self.calls.append(query)
        raise AssertionError("This test must not fetch weather")


class SMS:
    def __init__(self):
        self.sends = []

    def status(self):
        return {"configured": False, "enabled": False, "demo_enabled": False}


class Auth:
    def __init__(self):
        self.closed = False
        self.on_close = None

    def status(self):
        return {"state": "connected", "plan_enabled": True,
                "active_account_id": "test-account", "accounts": [], "attempt": None}

    def close(self):
        if self.on_close:
            self.on_close()
        self.closed = True


class Provider:
    def __init__(self):
        self.calls = []
        self.fail = False

    def status(self):
        return {"connected": True, "selected_model": "test-plan-model",
                "usage_paused": False, "cooldown_until": None}

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("PRIVATE provider error must not enter a saved note")
        return {"text": json.dumps({key: "weather coverage remains limited and the orchard estimate is incomplete"
                                    for key in kwargs["packet"]["events"]})}


@pytest.fixture(autouse=True)
def no_private_environment(monkeypatch):
    for key in list(os.environ):
        if key.startswith(("TWILIO_", "TERRA_SMS_", "OPENAI_")) or key in {"CIMIS_APP_KEY", "CIMIS_API_KEY"}:
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def plan(monkeypatch):
    auth, provider, paths = Auth(), Provider(), []
    original = local.attach_controls

    def attach(app, state_dir):
        paths.append(Path(state_dir))
        return original(app, state_dir, auth=auth, provider=provider)

    monkeypatch.setattr(local, "attach_controls", attach)
    return auth, provider, paths


def make_app(tmp_path, **kwargs):
    return runtime.create_runtime_app(live_service=Live(), sms_dispatcher=SMS(),
        state_dir=tmp_path, web_root=tmp_path / "no-web", clock=lambda: NOW, **kwargs)


def pending_limitation(monitor, orchard="test-orchard"):
    monitor.create_orchard("West orchard", orchard_id=orchard)
    event = {"id": "event-" + orchard, "type": "monitoring_limited", "transition": "opening",
             "severity": "moderate", "occurred_at": NOW.isoformat(),
             "facts": {"reason": "insufficient_usable_neighbors"}}
    with monitor.store.connect(write=True) as db:
        monitor.store.add_event(db, orchard, event, NOW)
        monitor.store.set_state(db, "watch:" + orchard, {"health_incident": True})
    return orchard


def test_default_factory_does_not_initialize_plan_connection(tmp_path, plan):
    app = make_app(tmp_path)
    auth, provider, paths = plan
    assert paths == [] and provider.calls == [] and not auth.closed
    assert not hasattr(app.state, "chatgpt_provider")
    assert not (tmp_path / "chatgpt").exists()


def test_enabled_factory_shares_provider_and_uses_existing_route_guard(tmp_path, plan):
    app = make_app(tmp_path, chatgpt_enabled=True)
    auth, provider, paths = plan
    monitor = app.state.runtime_monitor
    assert paths == [tmp_path / "chatgpt"]
    assert monitor.writer.generator is app.state.chatgpt_provider is provider
    assert monitor.source.service is app.state.query_service
    assert monitor.delivery is app.state.sms_dispatcher
    assert monitor.store.path == tmp_path / "farm.sqlite3"
    assert provider.calls == [] and app.state.query_service.calls == []
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/api/chatgpt/status").status_code == 403
        token = client.get("/api/sms/status").json()["control_token"]
        headers = {"Origin": ORIGIN, "X-Terra-SMS-Control": token}
        status = client.get("/api/chatgpt/status", headers=headers)
        assert status.status_code == 200 and status.json()["plan_enabled"] is True
        assert client.get("/api/chatgpt/status", headers={**headers, "Origin": "https://outside.example"}).status_code == 403
    assert auth.closed and provider.calls == [] and app.state.sms_dispatcher.sends == []


@pytest.mark.parametrize("fail", [False, True])
def test_plan_writer_publishes_validated_note_or_template_with_immutable_evidence(tmp_path, plan, fail):
    app = make_app(tmp_path, chatgpt_enabled=True)
    _, provider, _ = plan
    provider.fail = fail
    monitor = app.state.runtime_monitor
    orchard = pending_limitation(monitor)
    note = monitor.flush(orchard, now=NOW)
    assert len(provider.calls) == 1
    assert note["generation"] == ("template" if fail else "chatgpt_plan")
    assert note["generation_outcome"] == ("llm_failed_or_invalid" if fail else "completed")
    assert "incomplete" in note["text"] and "PRIVATE" not in json.dumps(note)
    details = monitor.store.details(orchard, note["id"])
    assert details["message"] == note
    assert details["message"]["llm_input"]["events"][0]["facts"]["reason"] == "insufficient_usable_neighbors"
    assert details["events"][0]["id"] == "event-" + orchard
    provider.fail = not fail
    assert monitor.flush(orchard, now=NOW) is None
    assert len(provider.calls) == 1 and monitor.store.message(note["id"]) == note
    assert app.state.sms_dispatcher.sends == []


def test_explicit_protected_state_path_and_injected_writer_are_preserved(tmp_path, plan):
    custom = Writer(key="", model="")
    monitor = Monitor(Store(tmp_path / "provided.sqlite3"), writer=custom)
    auth_path = tmp_path / "separate-protected-installation"
    app = make_app(tmp_path, monitor=monitor, chatgpt_enabled=True, chatgpt_state_dir=auth_path)
    assert app.state.runtime_monitor is monitor and monitor.writer is custom
    assert plan[2] == [auth_path] and plan[1].calls == []


def test_auth_manager_closes_after_monitor_worker_stops(tmp_path, plan, monkeypatch):
    from terra_monitoring.farm_ops import __main__ as worker
    started = threading.Event()

    def run(monitor, interval, *, stop):
        started.set()
        stop.wait(3)

    monkeypatch.setattr(worker, "run_loop", run)
    app = make_app(tmp_path, chatgpt_enabled=True, start_worker=True)
    auth = plan[0]
    auth.on_close = lambda: pytest.fail("Credentials closed before worker stop") if app.state.runtime_worker_thread.is_alive() else None
    with TestClient(app, base_url=ORIGIN):
        assert started.wait(2) and not auth.closed
    assert auth.closed and not app.state.runtime_worker_thread.is_alive()


def test_enabled_app_requires_new_instance_after_shutdown(tmp_path, plan):
    app = make_app(tmp_path, chatgpt_enabled=True)
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/api/health").status_code == 200
    assert plan[0].closed
    with pytest.raises(RuntimeError, match="create a new app instance"):
        with TestClient(app, base_url=ORIGIN):
            pytest.fail("A server must not run with the already closed OAuth manager")
    assert plan[1].calls == []


@pytest.mark.parametrize("kwargs", [{"chatgpt_enabled": 1},
    {"chatgpt_state_dir": "relative"}, {"chatgpt_enabled": True, "chatgpt_state_dir": "relative"}])
def test_invalid_connection_configuration_fails_before_initialization(tmp_path, plan, kwargs):
    with pytest.raises(ValueError, match="chatgpt_"):
        make_app(tmp_path, **kwargs)
    assert plan[2] == [] and plan[1].calls == []


@pytest.mark.parametrize("opt_out", [False, True])
def test_cli_enables_plan_controls_unless_explicitly_disabled(tmp_path, monkeypatch, opt_out):
    calls = []
    monkeypatch.setattr(runtime, "create_runtime_app", lambda **kwargs: calls.append(kwargs) or object())
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=lambda *args, **kwargs: None))
    args = ["--state-dir", str(tmp_path), "--no-worker"] + (["--no-chatgpt"] if opt_out else [])
    runtime.main(args)
    assert calls[0]["chatgpt_enabled"] is (not opt_out)
    assert calls[0]["start_worker"] is False
