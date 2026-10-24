"""First-run boundaries: isolated preview, occupied ports and retained state."""
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("terra_bootstrap", REPO / "run_terra.py")
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)
PREREQUISITES = ([("Test tools", True, "")], "test-pnpm")


class BootstrapTests(unittest.TestCase):
    def test_available_python_interpreter_can_be_probed(self):
        self.assertGreaterEqual(bootstrap.version([sys.executable, "--version"]), (3, 12, 0))

    def test_doctor_on_fresh_checkout_does_not_install_or_write(self):
        with tempfile.TemporaryDirectory(prefix="terra setup ") as folder:
            root = Path(folder)
            output = io.StringIO()
            with patch.object(bootstrap, "prerequisites", return_value=(list(PREREQUISITES[0]), PREREQUISITES[1])), \
                    patch.object(bootstrap, "port_available", return_value=True), \
                    patch.object(bootstrap, "prepare") as prepare, redirect_stdout(output):
                result = bootstrap.main(["--doctor"], root=root)
            self.assertEqual(result, 1)
            self.assertEqual(list(root.iterdir()), [])
            prepare.assert_not_called()
            self.assertIn("Runtime dependencies", output.getvalue())
            self.assertIn("Web build", output.getvalue())
            self.assertIn("sign-in is available", output.getvalue())

    def test_preview_overrides_inherited_and_legacy_dotenv_credentials(self):
        with tempfile.TemporaryDirectory(prefix="terra preview ") as folder:
            root = Path(folder)
            state = root / "separate state"
            (root / ".env").write_text(
                "CIMIS_APP_KEY=should-not-load\nOPENAI_API_KEY=should-not-load\n"
                "TERRA_SMS_ENABLED=true\nTERRA_SMS_DEMO_ENABLED=true\n"
                "TWILIO_ACCOUNT_SID=should-not-load\nTERRA_SMS_DB_PATH=wrong.sqlite3\n",
                encoding="utf-8")
            with patch.dict(os.environ, {"TERRA_SMS_ENABLED": "true", "TERRA_SMS_ENV_FILE": "private.env",
                                        "CIMIS_APP_KEY": "private", "OPENAI_API_KEY": "private"}):
                env = bootstrap.runtime_environment(root, state, live=False)
                self.assertEqual(os.environ["CIMIS_APP_KEY"], "private")
            env["PYTHONPATH"] = str(REPO / "backend")
            code = """
import json, os
from chillwatch import config
from terra_monitoring.sms import SMSConfig
sms = SMSConfig.from_env()
print(json.dumps([bool(os.getenv('CIMIS_APP_KEY')), bool(os.getenv('OPENAI_API_KEY')),
                  sms.enabled, sms.demo_enabled, bool(sms.account_sid), str(sms.db_path),
                  os.getenv('TERRA_SMS_ENV_FILE')]))
"""
            result = subprocess.run([sys.executable, "-c", code], cwd=root, env=env,
                                    capture_output=True, text=True, timeout=20, check=True)
            self.assertEqual(json.loads(result.stdout), [False, False, False, False, False,
                                                        str((state / "sms.sqlite3").resolve()), ""])

    def test_preview_runtime_enables_chatgpt_without_worker_and_uses_absolute_paths(self):
        root = Path(tempfile.gettempdir()).resolve() / "terra checkout with spaces"
        state = root / ".terra-local/preview"
        args = SimpleNamespace(live=False, port=18520, no_chatgpt=False)
        command = bootstrap.runtime_command(root, args, state)
        self.assertIn("--no-worker", command)
        self.assertNotIn("--no-chatgpt", command)
        self.assertEqual(command[0], str(bootstrap.python_path(root)))
        self.assertEqual(command[command.index("--web-root") + 1], str(root / "web/dist/client"))
        self.assertEqual(command[command.index("--state-dir") + 1], str(state))

    def test_preview_no_chatgpt_is_an_explicit_diagnostic_option(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = io.StringIO()
            with patch.object(bootstrap, "prerequisites", return_value=PREREQUISITES), \
                    patch.object(bootstrap, "port_available", return_value=True), \
                    patch.object(bootstrap, "prepare"), \
                    patch.object(bootstrap.subprocess, "call", return_value=0) as launch, redirect_stdout(output):
                result = bootstrap.main(["--no-chatgpt"], root=root)
            self.assertEqual(result, 0)
            self.assertIn("--no-chatgpt", launch.call_args.args[0])
            self.assertIn("--no-worker", launch.call_args.args[0])
            self.assertIn("disabled by --no-chatgpt", output.getvalue())
            self.assertEqual(list(root.iterdir()), [])

    def test_actual_preview_command_has_blank_local_chatgpt_status_without_external_work(self):
        code = """
import json, sys
from unittest.mock import patch
from fastapi.testclient import TestClient
from terra_monitoring import runtime

def forbidden(*args, **kwargs):
    raise AssertionError('Preview startup/status attempted external work or authorization')

def inspect(app, **kwargs):
    origin = 'http://127.0.0.1:18520'
    with TestClient(app, base_url=origin) as client:
        sms = client.get('/api/sms/status').json()
        headers = {'Origin': origin, 'X-Terra-SMS-Control': sms['control_token']}
        response = client.get('/api/chatgpt/status', headers=headers)
        status = response.json()
        assert app.state.runtime_worker_thread is None
        assert not sms['enabled'] and not sms['demo_enabled']
        assert any(getattr(route, 'path', None) == '/api/chatgpt/connect' for route in app.routes)
        assert app.state.chatgpt_auth._server is None
        print(json.dumps({'http_status': response.status_code, 'state': status['state'],
                          'plan_enabled': status['plan_enabled'], 'accounts': status['accounts'],
                          'model': status['writer']['selected_model'],
                          'auth_path': str(app.state.runtime_state_dir / 'chatgpt')}))

with patch('httpx.HTTPTransport.handle_request', forbidden), \\
     patch('httpx.AsyncHTTPTransport.handle_async_request', forbidden), \\
     patch('socket.create_connection', forbidden), \\
     patch('terra_monitoring.chatgpt.auth.AuthManager.start', forbidden), \\
     patch('terra_monitoring.chatgpt.provider.ChatGPTPlanClient.models', forbidden), \\
     patch('terra_monitoring.chatgpt.provider.ChatGPTPlanClient.generate', forbidden), \\
     patch('uvicorn.run', inspect):
    runtime.main(sys.argv[1:])
"""
        with tempfile.TemporaryDirectory(prefix="terra fresh ChatGPT ") as folder:
            state = Path(folder) / "preview"
            command = bootstrap.runtime_command(REPO, SimpleNamespace(live=False, port=18520, no_chatgpt=False), state)
            env = bootstrap.runtime_environment(REPO, state, live=False)
            result = subprocess.run([sys.executable, "-c", code, *command[3:]], cwd=REPO, env=env,
                                    capture_output=True, text=True, timeout=30, check=True)
            self.assertEqual(json.loads(result.stdout), {
                "http_status": 200, "state": "disconnected", "plan_enabled": False,
                "accounts": [], "model": None, "auth_path": str(state.resolve() / "chatgpt")})
            self.assertTrue((state / "chatgpt").is_dir())

    def test_live_launch_preserves_operator_environment_and_passes_explicit_files(self):
        root = REPO
        args = SimpleNamespace(live=True, port=18520, env_file=Path("source.env"),
                               sms_env_file=Path("phone.env"), chatgpt_state_dir=None,
                               no_worker=False, no_chatgpt=False)
        with patch.dict(os.environ, {"CIMIS_APP_KEY": "private-key"}):
            env = bootstrap.runtime_environment(root, root / ".terra-local/runtime", live=True)
        self.assertEqual(env["CIMIS_APP_KEY"], "private-key")
        command = bootstrap.runtime_command(root, args, root / ".terra-local/runtime")
        self.assertNotIn("--no-worker", command)
        self.assertNotIn("--no-chatgpt", command)
        self.assertEqual(command[command.index("--env-file") + 1], str(Path("source.env").resolve()))
        self.assertNotIn("private-key", " ".join(command))

    def test_busy_port_stops_before_setup_and_leaves_other_server_running(self):
        with socket.socket() as listener, tempfile.TemporaryDirectory() as folder:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            with patch.object(bootstrap, "prerequisites", return_value=PREREQUISITES), \
                    patch.object(bootstrap, "prepare") as prepare, redirect_stderr(io.StringIO()):
                result = bootstrap.main(["--port", str(port)], root=Path(folder))
            self.assertEqual(result, 1)
            prepare.assert_not_called()
            self.assertFalse(bootstrap.port_available(port))
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_failed_setup_keeps_existing_state_and_never_starts_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / ".terra-local/runtime/farm.sqlite3"
            state.parent.mkdir(parents=True)
            state.write_bytes(b"existing operator state")
            with patch.object(bootstrap, "prerequisites", return_value=PREREQUISITES), \
                    patch.object(bootstrap, "prepare", side_effect=bootstrap.SetupError("install failed")), \
                    patch.object(bootstrap.subprocess, "call") as launch, redirect_stderr(io.StringIO()):
                result = bootstrap.main(["--setup-only"], root=root)
            self.assertEqual(result, 1)
            launch.assert_not_called()
            self.assertEqual(state.read_bytes(), b"existing operator state")

    def test_missing_private_file_fails_before_install_and_does_not_print_contents(self):
        with tempfile.TemporaryDirectory() as folder:
            output = io.StringIO()
            with patch.object(bootstrap, "prepare") as prepare, redirect_stderr(output), \
                    self.assertRaises(SystemExit) as error:
                bootstrap.main(["--live", "--env-file", str(Path(folder) / "missing.env")], root=Path(folder))
            self.assertEqual(error.exception.code, 2)
            prepare.assert_not_called()
            self.assertIn("configuration file is unavailable", output.getvalue())

    def test_source_change_invalidates_cached_web_build(self):
        for client in ("web", "web/demo"):
            with self.subTest(client=client), tempfile.TemporaryDirectory() as folder:
                self.check_build_cache(Path(folder), client)

    def check_build_cache(self, root, client):
        for name in ("package.json", "pnpm-lock.yaml", "index.html", "vite.config.mjs",
                     "src/main.jsx", "public/assessments/example.json", "dist/client/index.html"):
            path = root / client / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("initial", encoding="utf-8")
        stamp = root / client / "dist/.terra-inputs.sha256"
        stamp.write_text(bootstrap.web_fingerprint(root, client), encoding="utf-8")
        self.assertTrue(bootstrap.web_ready(root, client))
        (root / client / "public/assessments/example.json").write_text("new evidence", encoding="utf-8")
        self.assertFalse(bootstrap.web_ready(root, client))


if __name__ == "__main__":
    unittest.main()
