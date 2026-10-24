"""Source-bound real ASGI/native-store requests; no provider, replay computation or SMS."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from fastapi import Depends
from fastapi.responses import Response
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
import pytest
from starlette.concurrency import run_in_threadpool

from terra_monitoring.demo_simulation.full_flow import DisabledSMS
from terra_monitoring.farm_ops.service import Monitor
from terra_monitoring.farm_ops.store import Store
from terra_monitoring.farm_ops.writer import Writer
from terra_monitoring.runtime import create_runtime_app
from terra_monitoring.server import create_app
from terra_monitoring.simulation_connection import COOKIE, SOURCE_HEADER


ORIGIN = "http://127.0.0.1:8512"
NOW = datetime(2026, 3, 11, tzinfo=timezone.utc)


class Query:
    def context(self):
        return {"configured": False}

    def assess(self, _):
        raise AssertionError("Source safety checks must not fetch readings")


class Bridge:
    """Exercise the connection's native copy/dispatch without computing a grid."""
    source = None

    def __init__(self, store):
        self.store = store
        self.monitor = Monitor(store, writer=Writer(key="", model=""), clock=lambda: NOW)
        self.engine = SimpleNamespace(store=store, monitor=self.monitor)

    def __getattr__(self, name):
        return getattr(self.monitor, name)

    def evaluate(self, _):
        return None


@pytest.fixture
def source_runtime(tmp_path, monkeypatch):
    from terra_monitoring.demo_simulation import full_flow

    monitor = Monitor(Store(tmp_path / "live.sqlite3"), writer=Writer(key="", model=""), clock=lambda: NOW)
    profile = monitor.create_orchard("Walnuts", orchard_id="farm", crops=["walnut"],
                                    lat=38.55, lon=-121.8, elevation_ft=65)
    app = create_runtime_app(live_service=Query(), sms_dispatcher=DisabledSMS(), monitor=monitor,
                             state_dir=tmp_path / "runtime", web_root=tmp_path / "web")
    with monitor.store.connect(write=True) as db:
        monitor.store.set_state(db, "tara:setup", {"stage": "complete", "orchard_id": profile["id"],
            "crops": ["walnut"], "variety": None, "location": {key: profile[key] for key in ("name", "lat", "lon", "elevation_ft")},
            "notification_preference": None})
    made = []

    def replay_factory(*, state_dir, **_):
        bridge = Bridge(Store(Path(state_dir) / "replay.sqlite3"))
        replay = create_app(service=Query(), sms_dispatcher=DisabledSMS(), farm_monitor=bridge)
        replay.state.full_flow_bridge = bridge

        async def cube():
            return Response(b"replay-cube", media_type="image/png")

        replay.router.routes.insert(0, APIRoute("/terra-cube-1080.png", cube, methods=["GET"]))
        made.append(replay)
        return replay

    monkeypatch.setattr(full_flow, "create_app", replay_factory)
    return app, monitor, made


def status(client):
    response = client.get("/api/simulation/connection")
    assert response.status_code == 200
    return response.json()


def headers(client, context=None):
    return {"Origin": ORIGIN, "X-Terra-SMS-Control": client.get("/api/sms/status").json()["control_token"],
            SOURCE_HEADER: context or status(client)["source_context"]}


def restart(client, context, request_id="restart"):
    return client.post("/api/tara/setup", headers=headers(client, context),
                       json={"request_id": request_id, "action": "restart"})


def test_unknown_cookie_never_reads_or_mutates_live_and_explicit_recovery_is_available(source_runtime):
    app, monitor, made = source_runtime
    with TestClient(app, base_url=ORIGIN) as client:
        original = monitor.store.get("tara:setup")
        old_live = status(client)["source_context"]
        client.cookies.set(COOKIE, "cookie-from-a-restarted-or-other-port-runtime", domain="127.0.0.1", path="/")
        expired = status(client)
        assert expired["source_expired"] and expired["data_mode"] is None and not expired["connected"]
        assert expired["source_context"] != old_live
        assert client.get("/api/tara/conversation").status_code == 409
        assert client.get("/api/context").status_code == 409
        rejected = restart(client, expired["source_context"])
        assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "simulation_context_expired"
        assert monitor.store.get("tara:setup") == original and not made
        assert client.delete("/api/simulation/connection", headers=headers(client, old_live)).status_code == 409
        recovered = client.delete("/api/simulation/connection", headers=headers(client, expired["source_context"]))
        assert recovered.status_code == 200 and not recovered.json()["source_expired"]
        assert recovered.json()["source_context"] == old_live
        assert client.get("/api/tara/conversation").status_code == 200


def test_missing_and_stale_selected_source_headers_cannot_write_either_store(source_runtime):
    app, monitor, made = source_runtime
    with TestClient(app, base_url=ORIGIN) as client:
        live = status(client)["source_context"]
        auth = headers(client, live)
        without_source = {key: value for key, value in auth.items() if key != SOURCE_HEADER}
        assert client.post("/api/tara/setup", headers=without_source,
                           json={"request_id": "missing", "action": "restart"}).status_code == 409
        connected = client.post("/api/simulation/connect", headers=auth, json={"orchard_id": "farm"})
        assert connected.status_code == 200
        sim = connected.json()["source_context"]
        assert sim != live and len(made) == 1
        assert client.get("/terra-cube-1080.png").content == b"replay-cube"
        assert client.get("/api/context", headers={SOURCE_HEADER: live}).status_code == 409
        assert restart(client, live, "late-live-callback").status_code == 409
        assert monitor.store.get("tara:setup")["stage"] == "complete"
        replay_store = made[0].state.full_flow_bridge.store
        assert replay_store.get("tara:setup")["stage"] == "complete"
        assert restart(client, sim, "sim-only").status_code == 200
        assert replay_store.get("tara:setup")["stage"] == "crop"
        disconnected = client.delete("/api/simulation/connection", headers=headers(client, sim))
        assert disconnected.status_code == 200
        assert restart(client, sim, "late-sim-callback").status_code == 409
        assert client.get("/api/tara/conversation", headers={SOURCE_HEADER: sim}).status_code == 409
        assert monitor.store.get("tara:setup")["stage"] == "complete"
        assert restart(client, live, "live-only").status_code == 200


def test_shared_chatgpt_mutations_are_source_checked_but_never_replay_routed(source_runtime):
    app, _, made = source_runtime
    calls = []

    async def configure():
        calls.append("live-account-controls")
        return {"saved": True}

    app.router.routes.insert(0, APIRoute("/api/chatgpt/test-control", configure, methods=["POST"],
        dependencies=[Depends(app.state.local_mutation_guard)]))
    with TestClient(app, base_url=ORIGIN) as client:
        live = status(client)["source_context"]
        connected = client.post("/api/simulation/connect", headers=headers(client, live), json={})
        sim = connected.json()["source_context"]
        path = "/api/chatgpt/test-control"
        assert client.post(path, headers=headers(client, live)).status_code == 409
        assert not calls
        assert client.post(path, headers=headers(client, sim)).status_code == 200
        assert calls == ["live-account-controls"] and len(made) == 1


def test_signed_out_restart_recovers_expired_simulation_before_account_controls(source_runtime, tmp_path):
    app, monitor, made = source_runtime
    original_setup = monitor.store.get("tara:setup")
    with TestClient(app, base_url=ORIGIN) as previous:
        old_live = status(previous)["source_context"]
        connected = previous.post("/api/simulation/connect", headers=headers(previous, old_live), json={})
        assert connected.status_code == 200
        old_sim = connected.json()["source_context"]
        old_cookie = previous.cookies.get(COOKIE)

    replacement = create_runtime_app(monitor=monitor, state_dir=tmp_path / "replacement",
                                     web_root=tmp_path / "web", chatgpt_enabled=False)
    account_calls = []

    # These outer routes stand in for signed-out account capabilities. The real
    # runtime guard and dispatch execute; no OAuth/account provider is invoked.
    async def account_status():
        return {"state": "disconnected", "plan_enabled": False}

    async def connect_capability():
        account_calls.append("local-connect-capability")
        return {"available": True, "oauth_started": False}

    protected = [Depends(replacement.state.local_mutation_guard)]
    replacement.router.routes.insert(0, APIRoute("/api/chatgpt/status", account_status,
                                                methods=["GET"], dependencies=protected))
    replacement.router.routes.insert(0, APIRoute("/api/chatgpt/connect", connect_capability,
                                                methods=["POST"], dependencies=protected))
    with TestClient(replacement, base_url=ORIGIN) as client:
        client.cookies.set(COOKIE, old_cookie, domain="127.0.0.1", path="/")
        expired = status(client)
        assert expired["source_expired"] and not expired["connected"] and expired["data_mode"] is None
        assert expired["source_context"] not in {old_live, old_sim}
        auth = headers(client, expired["source_context"])
        assert client.post("/api/chatgpt/connect", json={}).status_code == 403
        blocked = client.post("/api/chatgpt/connect", headers=auth, json={})
        assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "simulation_context_expired"
        assert not account_calls

        assert client.delete("/api/simulation/connection").status_code == 403
        recovered = client.delete("/api/simulation/connection", headers=auth)
        assert recovered.status_code == 200 and not recovered.json()["source_expired"]
        assert client.cookies.get(COOKIE) is None
        assert "Max-Age=0" in recovered.headers["set-cookie"]

        # A document reload captures the new live context. The previous expired
        # document still cannot initiate account work after cookie recovery.
        live = status(client)
        assert live["data_mode"] == "current" and live["source_context"] == recovered.json()["source_context"]
        assert client.post("/api/chatgpt/connect", headers=auth, json={}).status_code == 409
        live_auth = {**auth, SOURCE_HEADER: live["source_context"]}
        account = client.get("/api/chatgpt/status", headers=live_auth)
        assert account.status_code == 200 and account.json() == {"state": "disconnected", "plan_enabled": False}
        ready = client.post("/api/chatgpt/connect", headers=live_auth, json={})
        assert ready.status_code == 200 and ready.json() == {"available": True, "oauth_started": False}
        assert client.post("/api/chatgpt/connect", headers={**live_auth, "Origin": "https://other.invalid"}, json={}).status_code == 403
        assert account_calls == ["local-connect-capability"]
    assert monitor.store.get("tara:setup") == original_setup and len(made) == 1


@pytest.mark.parametrize("connected", [False, True])
def test_switch_refuses_a_held_mutation_and_pending_native_requests(source_runtime, connected):
    app, monitor, made = source_runtime
    entered, release = Event(), Event()

    async def hold():
        entered.set()
        assert await run_in_threadpool(release.wait, 5)
        return {"saved": True}

    app.router.routes.insert(0, APIRoute("/api/chatgpt/held", hold, methods=["POST"]))
    with TestClient(app, base_url=ORIGIN) as client:
        current = status(client)["source_context"]
        if connected:
            result = client.post("/api/simulation/connect", headers=headers(client, current), json={})
            current = result.json()["source_context"]
        auth = headers(client, current)
        with ThreadPoolExecutor(max_workers=1) as pool:
            held = pool.submit(client.post, "/api/chatgpt/held", headers=auth)
            try:
                assert entered.wait(3)
                result = (client.delete("/api/simulation/connection", headers=auth) if connected else
                          client.post("/api/simulation/connect", headers=auth, json={}))
                assert result.status_code == 409 and result.json()["error"]["code"] == "source_context_busy"
            finally:
                release.set()
            assert held.result(timeout=3).status_code == 200
        store = made[0].state.full_flow_bridge.store if connected else monitor.store
        with store.connect(write=True) as db:
            db.execute("INSERT INTO tara_requests(id,thread,signature,created,status) VALUES(?,?,?,?,?)",
                       ("pending", "onboarding", "signature", NOW.isoformat(), "pending"))
        result = (client.delete("/api/simulation/connection", headers=auth) if connected else
                  client.post("/api/simulation/connect", headers=auth, json={}))
        assert result.status_code == 409 and result.json()["error"]["code"] == "source_context_busy"


def test_restart_changes_live_source_identity_even_with_no_simulation_cookie(source_runtime, tmp_path):
    app, monitor, _ = source_runtime
    with TestClient(app, base_url=ORIGIN) as client:
        original_context = status(client)["source_context"]
    replacement = create_runtime_app(monitor=monitor,
                                     state_dir=tmp_path / "replacement", web_root=tmp_path / "web")
    with TestClient(replacement, base_url=ORIGIN) as client:
        assert status(client)["source_context"] != original_context
        assert restart(client, original_context, "old-document").status_code == 409
        assert monitor.store.get("tara:setup")["stage"] == "complete"


def test_new_mutations_cannot_enter_a_source_while_it_is_switching(source_runtime, monkeypatch):
    app, monitor, _ = source_runtime
    entered, release = Event(), Event()
    connection = app.state.simulation_connection
    copy_conversation = connection._copy_conversation

    def hold_copy(*args):
        entered.set()
        assert release.wait(5)
        return copy_conversation(*args)

    monkeypatch.setattr(connection, "_copy_conversation", hold_copy)
    with TestClient(app, base_url=ORIGIN) as client:
        live = status(client)["source_context"]
        auth = headers(client, live)
        with ThreadPoolExecutor(max_workers=1) as pool:
            connecting = pool.submit(client.post, "/api/simulation/connect", headers=auth, json={})
            try:
                assert entered.wait(3)
                rejected = client.post("/api/tara/setup", headers=auth,
                    json={"request_id": "racing-write", "action": "restart"})
                assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "source_context_busy"
                assert monitor.store.get("tara:setup")["stage"] == "complete"
            finally:
                release.set()
            assert connecting.result(timeout=3).status_code == 200
        with monitor.store.connect() as db:
            assert db.execute("SELECT 1 FROM tara_requests WHERE id='racing-write'").fetchone() is None


def test_existing_local_authorization_and_origin_rejections_keep_403_precedence(source_runtime):
    app, monitor, _ = source_runtime
    with TestClient(app, base_url=ORIGIN) as client:
        current = status(client)["source_context"]
        auth = headers(client, current)
        for path in ("/api/tara/setup", "/api/orchards", "/api/monitor/check", "/api/sms/disable", "/api/chatgpt/control"):
            assert client.post(path, json={}).status_code == 403
            assert client.post(path, headers={**auth, "Origin": "https://other.invalid", SOURCE_HEADER: "stale"}, json={}).status_code == 403
        assert client.get("/api/sms/status", headers={"Origin": "https://other.invalid"}).status_code == 403
        assert client.get("/api/simulation/connection", headers={"Origin": "https://other.invalid"}).status_code == 403
        assert client.post("/api/simulation/connect", json={}).status_code == 403
        assert monitor.store.get("tara:setup")["stage"] == "complete"
