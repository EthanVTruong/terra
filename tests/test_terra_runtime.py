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
        assert client.post("/api/assess", json={"lat": 38.6, "lon": -121.8, "elevation_ft": 70.0}).json() == {"result": "read-only"}
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
        assert client.post("/api/monitor/check", headers=allowed).json() == {"status": "checked"}
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
    def sms_factory(environment):
        seen["sms_path"] = environment["TERRA_SMS_DB_PATH"]
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
