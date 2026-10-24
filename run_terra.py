#!/usr/bin/env python3
"""Prepare and run Terra locally, without shell activation or provider keys."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PNPM_VERSION = "11.25.0"
NODE_MIN = (22, 13, 0)
PYTHON_MIN = (3, 12)
PROBE = """
import importlib.metadata, json, sys
if sys.version_info < (3, 12): sys.exit(2)
for name, version in json.loads(sys.argv[1]):
    try:
        if importlib.metadata.version(name) != version: sys.exit(1)
    except importlib.metadata.PackageNotFoundError: sys.exit(1)
"""


class SetupError(Exception):
    pass


def python_path(root: Path) -> Path:
    return root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def version(command: list[str]) -> tuple[int, int, int] | None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
        match = re.match(r"^(?:v|Python )?(\d+)\.(\d+)\.(\d+)", result.stdout.strip())
        if result.returncode == 0 and match:
            return tuple(map(int, match.groups()))
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def prerequisites() -> tuple[list[tuple[str, bool, str]], str | None]:
    node, pnpm = shutil.which("node"), shutil.which("pnpm")
    node_version = version([node, "--version"]) if node else None
    pnpm_version = version([pnpm, "--version"]) if pnpm else None
    checks = [
        ("Python", sys.version_info >= PYTHON_MIN, "Use Python 3.12 or newer (3.12 is tested in CI)."),
        ("Node.js", bool(node_version and node_version >= NODE_MIN), "Install Node.js 24 LTS (minimum 22.13)."),
        ("pnpm", pnpm_version == tuple(map(int, PNPM_VERSION.split("."))),
         f"Install pnpm {PNPM_VERSION}: npm install --global pnpm@{PNPM_VERSION}"),
    ]
    return checks, pnpm


def runtime_ready(root: Path) -> bool:
    python = python_path(root)
    if not python.is_file():
        return False
    pins = re.findall(r"^([A-Za-z0-9_.-]+)==([^\s#]+)",
                      (root / "requirements-terra.txt").read_text(encoding="utf-8"), re.M)
    try:
        result = subprocess.run([str(python), "-c", PROBE, json.dumps(pins)],
                                capture_output=True, timeout=20, check=False)
        return bool(pins) and result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def web_fingerprint(root: Path, client: str = "web") -> str:
    web = root / client
    paths = [web / name for name in ("package.json", "pnpm-lock.yaml", "index.html", "vite.config.mjs")]
    for name in ("src", "public", "scripts", "worker", ".openai"):
        paths.extend(path for path in (web / name).rglob("*") if path.is_file())
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(web).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def web_ready(root: Path, client: str = "web") -> bool:
    stamp = root / client / "dist/.terra-inputs.sha256"
    return ((root / client / "dist/client/index.html").is_file() and stamp.is_file()
            and stamp.read_text(encoding="utf-8").strip() == web_fingerprint(root, client))


def port_available(port: int) -> bool:
    try:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def run_step(command: list[str], root: Path, label: str) -> None:
    print(label, flush=True)
    result = subprocess.run(command, cwd=root, check=False)
    if result.returncode:
        raise SetupError(f"{label} failed (exit {result.returncode}). Fix the error above and rerun; state is retained.")


def prepare(root: Path, pnpm: str) -> None:
    python = python_path(root)
    if not python.is_file():
        if (root / ".venv").exists():
            raise SetupError("An incomplete .venv already exists. Repair it or choose a fresh checkout; it was not replaced.")
        print("Creating .venv...", flush=True)
        venv.EnvBuilder(with_pip=True).create(root / ".venv")
    interpreter = version([str(python), "--version"])
    if not interpreter or interpreter[:2] < PYTHON_MIN:
        raise SetupError("The existing .venv needs Python 3.12 or newer. Recreate it explicitly; it was not replaced.")
    if not runtime_ready(root):
        run_step([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r",
                  str(root / "requirements-terra.txt")], root, "Installing Terra runtime dependencies...")
    for client, label in (("web", "Terra"), ("web/demo", "Station lab")):
        if not web_ready(root, client):
            run_step([pnpm, "--dir", str(root / client), "install", "--frozen-lockfile"], root,
                     f"Installing {label} dependencies...")
            run_step([pnpm, "--dir", str(root / client), "build"], root, f"Building {label}...")
            (root / client / "dist/.terra-inputs.sha256").write_text(web_fingerprint(root, client), encoding="utf-8")


def runtime_environment(root: Path, state: Path, *, live: bool) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "backend")
    if not live:
        # Explicit empty values also override chillwatch.config's legacy .env
        # setdefault loader. A preview must not inherit a sender, key or journal.
        for key in ("CIMIS_APP_KEY", "CIMIS_API_KEY", "OPENAI_API_KEY", "TERRA_LLM_MODEL",
                    "TERRA_SMS_ENV_FILE", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN",
                    "TWILIO_API_KEY_SID", "TWILIO_API_KEY_SECRET", "API_KEY_SID", "API_KEY_SECRET",
                    "TWILIO_FROM_NUMBER", "TERRA_SMS_TO_NUMBER", "TERRA_SMS_CONTENT_MODE"):
            env[key] = ""
        env.update({"CHILLWATCH_HOME": str(root), "CHILLWATCH_CONFIG_DIR": str(root / "config"),
                    "CHILLWATCH_DATA_DIR": str(root / "data"), "TERRA_SMS_ENABLED": "false",
                    "TERRA_SMS_DEMO_ENABLED": "false", "TERRA_SMS_MAX_PER_DAY": "10",
                    "TERRA_SMS_CONTENT_MODE": "terra",
                    "TERRA_SMS_DB_PATH": str(state / "sms.sqlite3")})
    return env


def runtime_command(root: Path, args, state: Path) -> list[str]:
    command = [str(python_path(root)), "-m", "terra_monitoring.runtime", "--host", "127.0.0.1",
               "--port", str(args.port), "--state-dir", str(state),
               "--web-root", str(root / "web/dist/client")]
    if not args.live:
        command.append("--no-worker")
    else:
        for name in ("env_file", "sms_env_file", "chatgpt_state_dir"):
            value = getattr(args, name)
            if value is not None:
                command.extend(["--" + name.replace("_", "-"), str(value.expanduser().resolve())])
        if args.no_worker:
            command.append("--no-worker")
    if args.no_chatgpt:
        command.append("--no-chatgpt")
    return command


def main(argv=None, *, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doctor", action="store_true", help="Check setup without installing or contacting providers")
    parser.add_argument("--setup-only", action="store_true", help="Install/build without starting a server")
    parser.add_argument("--live", action="store_true", help="Use operator configuration and enable the monitor")
    parser.add_argument("--port", type=int, default=8512)
    parser.add_argument("--state-dir", type=Path, help="Retained state; preview and live have separate defaults")
    parser.add_argument("--env-file", type=Path, help="Private CIMIS/operator file; requires --live")
    parser.add_argument("--sms-env-file", type=Path, help="Private SMS file; requires --live")
    parser.add_argument("--chatgpt-state-dir", type=Path, help="Stable protected ChatGPT directory; requires --live")
    parser.add_argument("--no-worker", action="store_true", help="Use --live for on-demand queries without polling")
    parser.add_argument("--no-chatgpt", action="store_true", help="Disable ChatGPT sign-in controls for a diagnostic session")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if not args.live and (args.env_file or args.sms_env_file or args.chatgpt_state_dir or args.no_worker):
        parser.error("operator configuration and --no-worker require --live; preview already disables live weather and SMS")
    if args.chatgpt_state_dir and args.no_chatgpt:
        parser.error("--chatgpt-state-dir cannot be combined with --no-chatgpt")
    for path in (args.env_file, args.sms_env_file):
        if path is not None and not path.expanduser().is_file():
            parser.error("The supplied private configuration file is unavailable. Check its path.")
    state = (args.state_dir or root / ".terra-local" / ("runtime" if args.live else "preview")).expanduser().resolve()
    checks, pnpm = prerequisites()
    if args.doctor:
        checks.extend([
            ("Runtime dependencies", runtime_ready(root), "Run python run_terra.py --setup-only."),
            ("Web build", web_ready(root), "Run python run_terra.py --setup-only."),
            ("Station lab build", web_ready(root, "web/demo"), "Run python run_terra.py --setup-only."),
            (f"Port {args.port}", port_available(args.port), "Already in use; choose --port 8520 or use the existing server."),
        ])
        for label, ok, advice in checks:
            print(f"{'OK' if ok else 'NEEDS SETUP'}  {label}" + ("" if ok else f": {advice}"))
        print("ChatGPT sign-in is disabled by --no-chatgpt." if args.no_chatgpt else
              "ChatGPT sign-in is available in Terra and starts only when you choose to connect.")
        print("Provider readiness is checked separately in Terra; this diagnostic does not validate credentials or delivery.")
        return int(any(not ok for _, ok, _ in checks))
    try:
        for _, ok, advice in checks:
            if not ok:
                raise SetupError(advice)
        if not args.setup_only and not port_available(args.port):
            raise SetupError(f"Port {args.port} is already in use. Choose --port 8520 or use the existing server; nothing was stopped.")
        prepare(root, pnpm)
        if args.setup_only:
            print("Local setup complete. Run python run_terra.py to open the preview.")
            return 0
        mode = "Live runtime: confirm your location and provider readiness in Terra." if args.live else (
            "Saved-example preview: live weather, the worker and SMS are disabled. "
            + ("ChatGPT sign-in is disabled by --no-chatgpt." if args.no_chatgpt else
               "Choose Continue with ChatGPT in Terra to connect your own account."))
        print(f"{mode}\nOpen http://127.0.0.1:{args.port} after server startup. Stop with Ctrl+C.", flush=True)
        return subprocess.call(runtime_command(root, args, state), cwd=root,
                               env=runtime_environment(root, state, live=args.live))
    except SetupError as exc:
        print(f"Terra setup: {exc}", file=sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError):
        print("Terra setup could not complete. Check Python venv/pip support, executable paths, network access and directory permissions; state is retained.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
