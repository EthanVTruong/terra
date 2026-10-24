"""Combined runtime lifecycle and route boundaries; no provider calls or sends."""
from __future__ import annotations

from contextlib import asynccontextmanager
import os
from pathlib import Path
import subprocess
import sys
import threading
import types

from fastapi.testclient import TestClient
import pytest

from terra_monitoring import runtime
from terra_monitoring.farm_ops.store import Store

ORIGIN = "http://127.0.0.1:8512"
HEADERS = {"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"}


class Live:
    def __init__(self):
        self.calls = []

    def context(self):
        return {"schema_version": "terra-live-context-v1", "configured": False}

    def assess(self, query):
        self.calls.append(query)
        return {"result": "read-only"}


class SMS:
    def __init__(self):
        self.sends = []

    def status(self):
        return {"configured": False, "enabled": False, "demo_enabled": False}

    def demo(self, event_id):
        self.sends.append(event_id)
        return {"status": "disabled"}


class Source:
    def __init__(self, service):
        self.service = service


class Watch:
    def __init__(self, path, live, sms):
        self.store = Store(path)
        self.source = Source(live)
        self.delivery = sms
        self.calls = 0
        self.checked = threading.Event()

    def check(self):
        self.calls += 1
        self.checked.set()
        return {"status": "checked"}

    def worker_status(self):
        return self.store.get("worker-runtime", {})


@pytest.fixture(autouse=True)
def private_environment_is_not_used(monkeypatch):
    for key in list(os.environ):
        if key.startswith(("TWILIO_", "TERRA_SMS_", "OPENAI_")) or key in {"API_KEY_SID", "API_KEY_SECRET", "CIMIS_APP_KEY", "CIMIS_API_KEY"}:
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def components(tmp_path):
    live, sms = Live(), SMS()
    return live, sms, Watch(tmp_path / "farm.sqlite3", live, sms)


def app_for(components, tmp_path, **kwargs):
    live, sms, monitor = components
    return runtime.create_runtime_app(live_service=live, sms_dispatcher=sms, monitor=monitor,
                                      state_dir=tmp_path / "runtime", web_root=tmp_path / "no-build", **kwargs)


def test_import_does_not_read_credentials_poll_or_start_threads():
    import terra_monitoring
    backend = str(Path(terra_monitoring.__file__).resolve().parents[1])
    code = """
import sys, socket, threading
def forbidden(*a, **k): raise AssertionError('import started external work')
socket.create_connection = forbidden
threading.Thread.start = forbidden
import terra_monitoring.runtime
assert 'terra_monitoring.sms' not in sys.modules
assert 'terra_monitoring.live' not in sys.modules
assert 'terra_monitoring.farm_ops.service' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-B", "-c", code],
                            env={**os.environ, "PYTHONPATH": backend}, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_factory_and_default_lifespan_do_not_poll_or_send(components, tmp_path):
    live, sms, monitor = components
    app = app_for(components, tmp_path)
    assert monitor.calls == 0 and sms.sends == []
    assert app.state.query_service is live and app.state.sms_dispatcher is sms
    assert app.state.runtime_monitor is monitor
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/orchards").json() == {"orchards": []}
        assert client.get("/api/monitor/status").status_code == 200
        assert client.post("/api/assess", headers={"X-Terra-Source-Context": client.get("/api/simulation/connection").json()["source_context"]}, json={"lat": 38.6, "lon": -121.8, "elevation_ft": 70.0}).json() == {"result": "read-only"}
        assert app.state.runtime_worker_thread is None
    assert len(live.calls) == 1 and monitor.calls == 0 and sms.sends == []


def test_real_combined_route_order_and_raising_shared_guard(components, tmp_path):
    _, sms, monitor = components
    app = app_for(components, tmp_path)
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/api/orchards").status_code == 200  # before catch-all/static
        assert client.post("/api/monitor/check", headers=HEADERS).status_code == 403
        assert client.post("/api/monitor/check", headers={**HEADERS, "x-terra-sms-control": "wrong"}).status_code == 403
        assert monitor.calls == 0
        status = client.get("/api/sms/status", headers=HEADERS).json()
        token = status["control_token"]
        assert status["enabled"] is False
        allowed = {**HEADERS, "x-terra-sms-control": token}
        assert client.post("/api/monitor/check", headers={**allowed, "X-Terra-Source-Context": client.get("/api/simulation/connection").json()["source_context"]}).json() == {"status": "checked"}
        assert client.post("/api/monitor/check", headers={**allowed, "Origin": "https://elsewhere.example"}).status_code == 403
        assert client.get("/api/not-a-route").status_code == 404
    assert monitor.calls == 1 and sms.sends == []


def test_explicit_worker_uses_existing_loop_and_stops_with_lifespan(components, tmp_path):
    _, sms, monitor = components
    app = app_for(components, tmp_path, start_worker=True, poll_seconds=3600)
    assert monitor.calls == 0
    with TestClient(app, base_url=ORIGIN):
        assert monitor.checked.wait(2)
        thread = app.state.runtime_worker_thread
        assert thread.is_alive() and monitor.worker_status()["running"] is True
    assert not thread.is_alive()
    assert app.state.runtime_worker_stop.is_set()
    assert monitor.worker_status()["running"] is False
    assert monitor.calls == 1 and sms.sends == []


def test_worker_can_restart_without_adding_a_second_loop(components, tmp_path):
    monitor = components[2]
    app = app_for(components, tmp_path, start_worker=True, poll_seconds=3600)
    for expected in (1, 2):
        monitor.checked.clear()
        with TestClient(app, base_url=ORIGIN):
            assert monitor.checked.wait(2)
            assert monitor.calls == expected
        assert not app.state.runtime_worker_thread.is_alive()


def test_worker_failure_logs_no_exception_or_credential_text(components, tmp_path, monkeypatch, caplog):
    from terra_monitoring.farm_ops import __main__ as worker
    def fail(*args, **kwargs):
        raise RuntimeError("private-provider-token-should-not-log")
    monkeypatch.setattr(worker, "run_loop", fail)
    app = app_for(components, tmp_path, start_worker=True)
    with TestClient(app, base_url=ORIGIN):
        app.state.runtime_worker_thread.join(2)
        assert app.state.runtime_worker_error == "worker_stopped"
    assert "private-provider-token" not in caplog.text
    assert "Terra monitor worker stopped" in caplog.text


def test_worker_shutdown_is_bounded_and_rejects_a_second_live_loop(components, tmp_path, monkeypatch):
    from terra_monitoring.farm_ops import __main__ as worker
    entered, release = threading.Event(), threading.Event()
    def busy(*args, **kwargs):
        entered.set()
        release.wait(5)
    monkeypatch.setattr(worker, "run_loop", busy)
    app = app_for(components, tmp_path, start_worker=True, shutdown_timeout=0.01)
    try:
        with TestClient(app, base_url=ORIGIN):
            assert entered.wait(2)
        thread = app.state.runtime_worker_thread
        assert app.state.runtime_worker_stop.is_set() and thread.is_alive()
        assert app.state.runtime_worker_error == "worker_shutdown_pending"
        with pytest.raises(RuntimeError, match="already has an active monitor loop"):
            with TestClient(app, base_url=ORIGIN):
                pass
        assert app.state.runtime_worker_thread is thread
    finally:
        release.set()
        app.state.runtime_worker_thread.join(2)
    assert not thread.is_alive()


def test_runtime_chains_existing_application_lifespan(components, tmp_path, monkeypatch):
    from fastapi import FastAPI
    import terra_monitoring.server as server
    order = []
    @asynccontextmanager
    async def existing_lifespan(app):
        order.append("existing-start")
        yield
        assert not app.state.runtime_worker_thread.is_alive()
        order.append("existing-stop")
    monkeypatch.setattr(server, "create_app", lambda **kwargs: FastAPI(lifespan=existing_lifespan))
    app = app_for(components, tmp_path, start_worker=True, poll_seconds=3600)
    with TestClient(app, base_url=ORIGIN):
        assert components[2].checked.wait(2)
        assert order == ["existing-start"]
    assert order == ["existing-start", "existing-stop"]


def test_default_construction_shares_services_and_absolute_journals(tmp_path, monkeypatch):
    import terra_monitoring.live as live_module
    import terra_monitoring.sms as sms_module
    seen = {}
    live, sms = Live(), SMS()
    def live_factory(**kwargs):
        seen["source"] = kwargs
        return live
    def sms_factory(*, state_dir):
        seen["sms_path"] = str(state_dir / "sms.sqlite3")
        return sms
    monkeypatch.setattr(live_module, "LiveQueryService", live_factory)
    monkeypatch.setattr(sms_module.SMSDispatcher, "from_env", sms_factory)
    monkeypatch.chdir(tmp_path)
    app = runtime.create_runtime_app(state_dir="relative-state")
    root = tmp_path / "relative-state"
    monitor = app.state.runtime_monitor
    assert monitor.store.path == root / "farm.sqlite3"
    assert monitor.source.service is live and monitor.delivery is sms
    assert seen["source"]["data_dir"] == root / "live-source"
    assert seen["sms_path"] == str(root / "sms.sqlite3")
    assert app.state.runtime_state_dir.is_absolute()
    assert monitor.store.orchards() == []


def test_reuses_injected_monitor_dependencies_and_rejects_split_services(components, tmp_path):
    live, sms, monitor = components
    app = runtime.create_runtime_app(monitor=monitor, state_dir=tmp_path)
    assert app.state.query_service is live and app.state.sms_dispatcher is sms
    with pytest.raises(ValueError, match="share one live service"):
        runtime.create_runtime_app(monitor=monitor, live_service=Live(), state_dir=tmp_path)
    with pytest.raises(ValueError, match="share one SMS dispatcher"):
        runtime.create_runtime_app(monitor=monitor, sms_dispatcher=SMS(), state_dir=tmp_path)
    monitor.source = object()
    with pytest.raises(ValueError, match="source must expose"):
        runtime.create_runtime_app(monitor=monitor, live_service=live, sms_dispatcher=sms, state_dir=tmp_path)


@pytest.mark.parametrize("value", [0, 3601, True, 1.5, "300"])
def test_invalid_polling_interval_cannot_start_work(components, tmp_path, value):
    with pytest.raises(ValueError, match="Polling interval"):
        app_for(components, tmp_path, start_worker=True, poll_seconds=value)
    assert components[2].calls == 0 and components[1].sends == []


@pytest.mark.parametrize("kwargs", [{"start_worker": "yes"}, {"shutdown_timeout": 0},
                                    {"shutdown_timeout": 121}, {"shutdown_timeout": float("nan")}])
def test_invalid_lifecycle_arguments_cannot_start_work(components, tmp_path, kwargs):
    with pytest.raises(ValueError):
        app_for(components, tmp_path, **kwargs)
    assert components[2].calls == 0 and components[1].sends == []


def test_private_env_parser_process_precedence_literal_values_and_atomic_failure(tmp_path):
    path = tmp_path / ".env"
    path.write_text('# local\nexport CIMIS_APP_KEY="file-key"\nPLAIN=$(never-execute)\nSINGLE=\'literal\'\n', encoding="utf-8")
    values = {"CIMIS_APP_KEY": "process-key"}
    assert runtime.load_environment_file(path, values) == path.resolve()
    assert values == {"CIMIS_APP_KEY": "process-key", "PLAIN": "$(never-execute)", "SINGLE": "literal"}
    path.write_text('FIRST=new\nINVALID SECRET VALUE\n', encoding="utf-8")
    with pytest.raises(ValueError, match="unavailable or invalid") as exc:
        runtime.load_environment_file(path, values)
    assert "FIRST" not in values and "SECRET" not in str(exc.value)


@pytest.mark.parametrize("contents", [b"X=" + b"a" * 65536, b"X=\xff", b'X="unclosed', b"X=bad\x00value",
                                      b"EARLIER=value\nX=" + b"a" * 32767],
                         ids=["over-size-limit", "invalid-utf8", "unclosed-quote", "null-byte", "windows-value-limit"])
def test_private_env_bounds_fail_without_disclosing_values(tmp_path, contents):
    path = tmp_path / "private.env"
    path.write_bytes(contents)
    with pytest.raises(ValueError, match="unavailable or invalid"):
        runtime.load_environment_file(path, {})


def test_cli_has_explicit_private_paths_loopback_and_no_enable_side_effects(tmp_path, monkeypatch):
    env_file, sms_file = tmp_path / ".env", tmp_path / "sms.env"
    env_file.write_text("CIMIS_APP_KEY=not-a-real-key\n", encoding="utf-8")
    sms_file.write_text("TERRA_SMS_ENABLED=false\n", encoding="utf-8")
    seen = {}
    marker = object()
    def build(**kwargs):
        seen["build"] = kwargs
        return marker
    monkeypatch.setattr(runtime, "create_runtime_app", build)
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=lambda app, **kwargs: seen.update(app=app, run=kwargs)))
    runtime.main(["--state-dir", str(tmp_path / "state"), "--env-file", str(env_file),
                  "--sms-env-file", str(sms_file), "--no-worker", "--interval", "300"])
    assert seen["app"] is marker and seen["run"] == {"host": "127.0.0.1", "port": 8512}
    assert seen["build"]["start_worker"] is False and seen["build"]["poll_seconds"] == 300
    assert os.environ["TERRA_SMS_ENV_FILE"] == str(sms_file.resolve())
    assert os.environ.get("TERRA_SMS_ENABLED") is None
    assert os.environ["CIMIS_APP_KEY"] == "not-a-real-key"
    monkeypatch.delenv("CIMIS_APP_KEY")


@pytest.mark.parametrize("args", [["--host", "0.0.0.0"], ["--port", "0"], ["--port", "65536"],
                                   ["--interval", "0"], ["--interval", "3601"]])
def test_cli_rejects_public_host_and_invalid_bounds_before_factory(args, monkeypatch):
    monkeypatch.setattr(runtime, "create_runtime_app", lambda **kwargs: pytest.fail("factory must not run"))
    with pytest.raises(SystemExit) as exc:
        runtime.main(args)
    assert exc.value.code == 2


def test_cli_rejects_invalid_private_files_without_echoing_contents(tmp_path, monkeypatch, capsys):
    path = tmp_path / "private.env"
    path.write_text("PRIVATE SECRET CONTENT\n", encoding="utf-8")
    monkeypatch.setattr(runtime, "create_runtime_app", lambda **kwargs: pytest.fail("factory must not run"))
    for option in ("--env-file", "--sms-env-file"):
        selected = path if option == "--env-file" else tmp_path / "missing.env"
        with pytest.raises(SystemExit) as exc:
            runtime.main([option, str(selected)])
        assert exc.value.code == 2
        assert "SECRET CONTENT" not in capsys.readouterr().err


def test_cli_configuration_failure_does_not_echo_provider_exception(monkeypatch, capsys):
    def fail(**kwargs):
        raise ValueError("private-provider-secret")
    monkeypatch.setattr(runtime, "create_runtime_app", fail)
    with pytest.raises(SystemExit) as exc:
        runtime.main([])
    assert exc.value.code == 2
    error = capsys.readouterr().err
    assert "Unable to initialize the local runtime" in error
    assert "private-provider-secret" not in error


def test_cli_defaults_explicitly_start_worker(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(runtime, "create_runtime_app", lambda **kwargs: seen.update(kwargs) or object())
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=lambda *args, **kwargs: None))
    runtime.main(["--state-dir", str(tmp_path)])
    assert seen["start_worker"] is True and seen["poll_seconds"] == 300


def configure_sms(monkeypatch):
    for key, value in {"TWILIO_ACCOUNT_SID": "AC" + "1" * 32, "TWILIO_AUTH_TOKEN": "2" * 32,
                       "TWILIO_FROM_NUMBER": "+15555550101", "TERRA_SMS_TO_NUMBER": "+15555550102",
                       "TERRA_SMS_ENABLED": "true"}.items():
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize("selection", ["process", "file", "state-dir"])
def test_runtime_and_sms_operator_share_pause_resume_journal(tmp_path, monkeypatch, capsys, selection):
    from terra_monitoring.sms import main as sms_main
    configure_sms(monkeypatch)
    state = tmp_path / "state"
    chosen = tmp_path / "explicit.sqlite3"
    if selection == "process":
        monkeypatch.setenv("TERRA_SMS_DB_PATH", str(chosen))
    elif selection == "file":
        path = tmp_path / "private.env"
        path.write_text("TERRA_SMS_DB_PATH=" + str(chosen), encoding="utf-8")
        monkeypatch.setenv("TERRA_SMS_ENV_FILE", str(path))
    else:
        chosen = state / "sms.sqlite3"
    app = runtime.create_runtime_app(live_service=Live(), state_dir=state, web_root=tmp_path / "no-web")
    actual = app.state.sms_dispatcher
    assert actual.config.db_path == chosen
    assert actual.disable()["suppressed"] is True
    sms_main(["enable", "--state-dir", str(state)])
    assert actual.status()["enabled"] is True and actual.status()["suppressed"] is False
    assert "AC" + "1" * 32 not in capsys.readouterr().out


def test_default_runtime_and_operator_use_the_same_canonical_journal(tmp_path, monkeypatch):
    from terra_monitoring import configuration
    from terra_monitoring.sms import SMSDispatcher
    monkeypatch.setattr(configuration, "DEFAULT_STATE_DIR", tmp_path)
    monkeypatch.setattr(runtime, "DEFAULT_STATE_DIR", tmp_path)
    app = runtime.create_runtime_app(live_service=Live(), web_root=tmp_path / "no-web")
    assert app.state.sms_dispatcher.config.db_path == SMSDispatcher.from_env({}).config.db_path == tmp_path / "sms.sqlite3"


@pytest.mark.parametrize("has_history", [False, True])
def test_explicit_journal_switch_cannot_abandon_legacy_runtime_history(tmp_path, monkeypatch, has_history):
    import sqlite3
    from terra_monitoring.configuration import JournalSelectionError
    state = tmp_path / "old-runtime"
    state.mkdir()
    previous = state / "sms.sqlite3"
    with sqlite3.connect(previous) as db:
        db.execute("CREATE TABLE sms_delivery(event_id TEXT)")
        if has_history:
            db.execute("INSERT INTO sms_delivery VALUES ('existing-no-resend-evidence')")
    selected = tmp_path / "new-location.sqlite3"
    monkeypatch.setenv("TERRA_SMS_DB_PATH", str(selected))
    if has_history:
        with pytest.raises(JournalSelectionError, match="existing delivery history") as exc:
            runtime.create_runtime_app(live_service=Live(), state_dir=state)
        assert str(previous) not in str(exc.value) and str(selected) not in str(exc.value)
        assert not selected.exists()
    else:
        assert runtime.create_runtime_app(live_service=Live(), state_dir=state).state.sms_dispatcher.config.db_path == selected
    with sqlite3.connect(previous) as db:
        assert db.execute("SELECT COUNT(*) FROM sms_delivery").fetchone()[0] == int(has_history)


def test_guarded_farm_routes_share_validation_and_wrong_method_errors(components, tmp_path):
    app = app_for(components, tmp_path)
    with TestClient(app, base_url=ORIGIN) as client:
        token = client.get("/api/sms/status").json()["control_token"]
        bad = client.post("/api/orchards", json={"sms_opt_in": "yes"},
                          headers={"X-Terra-SMS-Control": token, "X-Terra-Source-Context": client.get("/api/simulation/connection").json()["source_context"]})
        assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_request"
        assert "coordinates" not in bad.json()["error"]["message"]
        for method, path in [("GET", "/api/monitor/check"), ("DELETE", "/api/orchards"),
                             ("POST", "/api/orchards/west"), ("DELETE", "/api/orchards/west/sms-opt-in")]:
            result = client.request(method, path, headers={"X-Terra-Source-Context": client.get("/api/simulation/connection").json()["source_context"]})
            assert result.status_code == 405 and result.json()["error"]["code"] == "method_not_allowed"
        assert client.get("/api/unknown").status_code == 404
    assert components[0].calls == [] and components[2].calls == 0


def test_legacy_query_server_delegates_to_runtime_without_starting_worker(monkeypatch):
    from terra_monitoring import server
    seen = []
    monkeypatch.setattr(runtime, "main", lambda args: seen.append(args))
    server.main(["--port", "8519"])
    assert seen == [["--port", "8519", "--no-worker"]]


def test_legacy_farm_serve_uses_canonical_runtime_without_implicit_worker(tmp_path, monkeypatch):
    from terra_monitoring.farm_ops import __main__ as farm_cli
    seen, monitor = {}, object()
    monkeypatch.setattr(sys, "argv", ["farm", "--state-dir", str(tmp_path), "serve", "--port", "8519"])
    monkeypatch.setattr(farm_cli, "_monitor", lambda database, state_dir: monitor)
    monkeypatch.setattr(runtime, "create_runtime_app", lambda **kwargs: seen.update(kwargs) or "canonical-app")
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=lambda app, **kw: seen.update(app=app, **kw)))
    farm_cli.main()
    assert seen["app"] == "canonical-app" and seen["monitor"] is monitor
    assert seen["start_worker"] is False and seen["port"] == 8519


@pytest.mark.parametrize("paused", [False, True])
def test_journal_selection_preserves_saved_pause_even_without_delivery_history(tmp_path, monkeypatch, paused):
    import sqlite3
    from terra_monitoring.configuration import JournalSelectionError
    state = tmp_path / "old-runtime"
    state.mkdir()
    with sqlite3.connect(state / "sms.sqlite3") as db:
        db.execute("CREATE TABLE sms_settings(key TEXT PRIMARY KEY,value TEXT)")
        db.execute("INSERT INTO sms_settings VALUES ('disabled',?)", ("true" if paused else "false",))
    selected = tmp_path / "new.sqlite3"
    monkeypatch.setenv("TERRA_SMS_DB_PATH", str(selected))
    if paused:
        with pytest.raises(JournalSelectionError, match="saved pause"):
            runtime.create_runtime_app(live_service=Live(), state_dir=state)
    else:
        assert runtime.create_runtime_app(live_service=Live(), state_dir=state).state.sms_dispatcher.config.db_path == selected
    assert not selected.exists()


def test_unreadable_previous_journal_cannot_silently_switch_location(tmp_path, monkeypatch):
    from terra_monitoring.configuration import JournalSelectionError
    state = tmp_path / "old-runtime"
    state.mkdir()
    (state / "sms.sqlite3").write_bytes(b"invalid database")
    monkeypatch.setenv("TERRA_SMS_DB_PATH", str(tmp_path / "new.sqlite3"))
    with pytest.raises(JournalSelectionError, match="needs review"):
        runtime.create_runtime_app(live_service=Live(), state_dir=state)


def test_relative_operator_state_directory_matches_runtime_cwd_resolution(tmp_path, monkeypatch, capsys):
    from terra_monitoring.sms import main as sms_main
    configure_sms(monkeypatch)
    monkeypatch.chdir(tmp_path)
    app = runtime.create_runtime_app(live_service=Live(), state_dir="custom-state", web_root=tmp_path / "no-web")
    actual = app.state.sms_dispatcher
    assert actual.config.db_path == tmp_path / "custom-state" / "sms.sqlite3"
    actual.disable()
    sms_main(["enable", "--state-dir", "custom-state"])
    assert actual.status()["enabled"] is True
    capsys.readouterr()


def test_default_journal_migration_does_not_abandon_legacy_operator_history(tmp_path, monkeypatch):
    import sqlite3
    from terra_monitoring import configuration
    from terra_monitoring.configuration import JournalSelectionError
    from terra_monitoring.sms import SMSDispatcher
    monkeypatch.setattr(configuration, "PACKAGE_ROOT", tmp_path)
    monkeypatch.setattr(configuration, "DEFAULT_STATE_DIR", tmp_path / ".terra-local" / "runtime")
    legacy = tmp_path / ".terra-local" / "sms.sqlite3"
    legacy.parent.mkdir()
    with sqlite3.connect(legacy) as db:
        db.execute("CREATE TABLE sms_delivery(event_id TEXT)")
        db.execute("INSERT INTO sms_delivery VALUES ('old-no-resend-evidence')")
    with pytest.raises(JournalSelectionError, match="existing delivery history"):
        SMSDispatcher.from_env({})
    assert SMSDispatcher.from_env({"TERRA_SMS_DB_PATH": str(legacy)}).config.db_path == legacy


@pytest.fixture
def simulation_runtime(tmp_path, monkeypatch):
    from copy import deepcopy
    from terra_monitoring.demo_simulation import full_flow
    from terra_monitoring.demo_simulation.engine import DemoSimulation, ORCHARD, START
    from terra_monitoring.farm_ops.service import Monitor
    from terra_monitoring.farm_ops.store import utc
    from terra_monitoring.farm_ops.writer import Writer

    class CountWriter:
        def __init__(self):
            self.calls = 0
            self.local = Writer(key="", model="")

        def write(self, packet):
            self.calls += 1
            return self.local.write(packet)

    live, sms, writer = Live(), SMS(), CountWriter()
    monitor = Monitor(Store(tmp_path / "live.sqlite3"), source=Source(live), delivery=sms,
                      writer=writer, clock=lambda: utc(START))
    profile = monitor.create_orchard("My walnuts", orchard_id="my-farm", crops=["walnut"], **ORCHARD)
    sample = DemoSimulation(state_dir=tmp_path / "sample", grid_size=2)
    packet = deepcopy(sample.packet)
    packet["data_mode"] = "current"
    monitor.apply(profile["id"], {"latest": packet}, now=utc(START))
    monitor.flush(profile["id"], now=utc(START))
    app = runtime.create_runtime_app(monitor=monitor, state_dir=tmp_path / "runtime", web_root=tmp_path / "web")
    with monitor.store.connect(write=True) as db:
        monitor.store.set_state(db, "tara:setup", {"stage": "complete", "orchard_id": profile["id"],
            "crops": ["walnut"], "variety": None, "location": {**ORCHARD, "name": profile["name"]},
            "notification_preference": None})
    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / "index.html").write_text('<script src="/assets/lab.js"></script>station-lab', encoding="utf-8")
    factory = full_flow.create_app
    monkeypatch.setattr(full_flow, "DemoSimulation", lambda **kwargs: DemoSimulation(grid_size=2, **kwargs))
    monkeypatch.setattr(full_flow, "create_app", lambda **kwargs: factory(demo_root=lab, **kwargs))
    return app, monitor, live, sms, writer


def test_simulation_connection_is_lazy_guarded_and_rejects_unknown_farms(simulation_runtime, tmp_path):
    app, monitor, live, sms, writer = simulation_runtime
    before = writer.calls
    sessions = tmp_path / "runtime/simulator-sessions"
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/api/simulation/connection").json()["connected"] is False
        assert not sessions.exists()
        token = client.get("/api/sms/status").json()["control_token"]
        headers = {**HEADERS, "X-Terra-SMS-Control": token, "X-Terra-Source-Context": client.get("/api/simulation/connection").json()["source_context"]}
        assert client.post("/api/simulation/connect", json={}).status_code == 403
        assert client.post("/api/simulation/connect", headers={**headers, "Origin": "https://other.invalid"}, json={}).status_code == 403
        assert client.post("/api/simulation/connect", headers=headers, json={"orchard_id": "unknown"}).status_code == 404
        assert client.post("/api/simulation/connect", headers=headers, json={"extra": True}).status_code == 422
        assert not sessions.exists()
    assert writer.calls == before and live.calls == [] and sms.sends == []


def test_connected_browser_replays_its_native_chat_and_station_changes_without_touching_live_state(simulation_runtime):
    app, monitor, live, sms, writer = simulation_runtime
    before = monitor.store.path.read_bytes()
    original_messages = monitor.store.messages("my-farm")
    original_evidence = monitor.store.details("my-farm", original_messages[0]["id"])
    before_calls = writer.calls
    with TestClient(app, base_url=ORIGIN) as connected, TestClient(app, base_url=ORIGIN) as ordinary:
        token = connected.get("/api/sms/status").json()["control_token"]
        headers = {**HEADERS, "X-Terra-SMS-Control": token, "X-Terra-Source-Context": connected.get("/api/simulation/connection").json()["source_context"]}
        result = connected.post("/api/simulation/connect", headers=headers, json={"orchard_id": "my-farm"})
        assert result.status_code == 200, result.text
        assert {key: result.json()[key] for key in ("connected", "data_mode", "station_url", "orchard_id")} == {"connected": True, "data_mode": "injected", "station_url": "/stations", "orchard_id": "my-farm"}
        assert result.json()["source_expired"] is False
        headers["X-Terra-Source-Context"] = result.json()["source_context"]
        assert "HttpOnly" in result.headers["set-cookie"] and "SameSite=strict" in result.headers["set-cookie"]
        assert writer.calls == before_calls  # Connecting never spends inference allowance.
        assert connected.get("/api/context").json()["replay_only"] is True
        assert ordinary.get("/api/simulation/connection").json()["connected"] is False
        assert "data_mode" not in ordinary.get("/api/context").json()
        assert connected.get("/api/orchards").json()["orchards"][0]["mode"] == "injected"
        assert ordinary.get("/api/orchards").json()["orchards"][0]["mode"] == "current"
        assert "station-lab" in connected.get("/stations").text
        assert connected.get("/api/sms/status").json()["control_token"] == token
        assert connected.get("/api/sms/status").json()["enabled"] is False
        conversation = connected.get("/api/tara/conversation").json()
        assert conversation["orchard_id"] == "my-farm" and conversation["setup"]["crops"] == ["walnut"]
        assert connected.get(f'/api/orchards/my-farm/messages/{original_messages[0]["id"]}/details').json() == original_evidence
        changed = connected.post("/api/demo/action", headers=headers, json={"type": "preset", "name": "support_loss"})
        assert changed.status_code == 200, changed.text
        feed = connected.get("/api/orchards/my-farm/messages").json()
        assert feed["monitor"]["comparison_available"] is False
        assert len(feed["messages"]) > len(original_messages)
        assert writer.calls == before_calls + 1  # Internal lab farm uses only its local writer.
        newest = next(message for message in feed["messages"] if message["id"] not in {m["id"] for m in original_messages})
        evidence = connected.get(f'/api/orchards/my-farm/messages/{newest["id"]}/details').json()
        assert evidence["assessments"][0]["data_mode"] == "injected"
        assert connected.delete("/api/simulation/connection").status_code == 403
        assert connected.delete("/api/simulation/connection", headers=headers).json()["connected"] is False
        assert connected.get("/api/orchards").json()["orchards"][0]["mode"] == "current"
    assert monitor.store.path.read_bytes() == before
    assert monitor.store.messages("my-farm") == original_messages and live.calls == [] and sms.sends == []


def test_simulation_connection_refuses_an_in_flight_conversation(simulation_runtime):
    app, monitor, _, _, writer = simulation_runtime
    with monitor.store.connect(write=True) as db:
        db.execute("INSERT INTO tara_requests(id,thread,signature,created,status) VALUES(?,?,?,?,?)",
                   ("still-working", "my-farm", "signature", "2026-03-11T11:00:00+00:00", "pending"))
    before = writer.calls
    with TestClient(app, base_url=ORIGIN) as client:
        token = client.get("/api/sms/status").json()["control_token"]
        result = client.post("/api/simulation/connect", headers={**HEADERS, "X-Terra-SMS-Control": token, "X-Terra-Source-Context": client.get("/api/simulation/connection").json()["source_context"]}, json={})
        assert result.status_code == 409
        assert client.get("/api/simulation/connection").json()["connected"] is False
    assert writer.calls == before
