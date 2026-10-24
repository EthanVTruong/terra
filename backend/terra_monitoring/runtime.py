"""Serve Terra's local UI/API and one persistent farm-monitor loop.

Importing this module never reads credentials, fetches observations or starts a
worker. The CLI starts monitoring explicitly; app factories default to no loop
so tests and embedding callers retain control over lifecycle.
"""
from __future__ import annotations

import argparse
import asyncio
from collections.abc import MutableMapping
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
import re
import threading
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_DIR = PACKAGE_ROOT / ".terra-local" / "runtime"
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def load_environment_file(path: str | Path,
                          environment: MutableMapping[str, str] | None = None) -> Path:
    """Load an explicitly chosen local file without shell evaluation.

    Existing process values win. Parse the whole bounded file before applying
    anything, and keep errors free of filenames, values and credential text.
    This is a local operator option, never browser input.
    """
    destination = os.environ if environment is None else environment
    try:
        resolved = Path(path).expanduser().resolve()
        with resolved.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("oversized")
        values: dict[str, str] = {}
        for raw_line in raw.decode("utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            key, separator, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not separator or not _KEY.fullmatch(key) or "\x00" in value:
                raise ValueError("invalid assignment")
            if value.startswith(("'", '"')):
                if len(value) < 2 or value[-1] != value[0]:
                    raise ValueError("invalid quote")
                value = value[1:-1]
            if len(key) + len(value) + 1 > 32766:
                # Windows has a separate per-environment-variable limit.
                raise ValueError("oversized value")
            values[key] = value
    except (OSError, TypeError, ValueError, UnicodeError):
        raise ValueError("The private environment file is unavailable or invalid.") from None
    for key, value in values.items():
        destination.setdefault(key, value)
    return resolved


def _interval(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 3600:
        raise ValueError("Polling interval must be an integer from 1 to 3600 seconds.")
    return value


def create_runtime_app(*, live_service: Any = None, sms_dispatcher: Any = None,
                       monitor: Any = None, state_dir: str | Path | None = None,
                       start_worker: bool = False, poll_seconds: int = 300,
                       web_root: str | Path | None = None, clock: Any = None,
                       shutdown_timeout: float = 30.0):
    """Assemble one shared source, monitor and transport with explicit lifecycle.

    Initializing the app may initialize its local journals, but never polls,
    creates orchards, enables subscriptions or sends messages. ``start_worker``
    only takes effect when the ASGI lifespan starts. Injected monitor dependencies
    are reused and incompatible explicit service/transport injections are rejected.
    """
    interval = _interval(poll_seconds)
    if not isinstance(start_worker, bool):
        raise ValueError("start_worker must be boolean.")
    if (isinstance(shutdown_timeout, bool) or not isinstance(shutdown_timeout, (int, float))
            or not 0 < shutdown_timeout <= 120):
        raise ValueError("Shutdown timeout must be between 0 and 120 seconds.")
    resolved = Path(state_dir if state_dir is not None else DEFAULT_STATE_DIR).expanduser().resolve()

    from .farm_ops.service import Monitor
    from .farm_ops.source import LiveSource
    from .farm_ops.store import Store
    from .live import LiveQueryService
    from .server import create_app
    from .sms import SMSDispatcher

    existing_source = getattr(monitor, "source", None)
    existing_live = getattr(existing_source, "service", None)
    existing_sms = getattr(monitor, "delivery", None)
    if existing_source is not None and existing_live is None:
        raise ValueError("The monitor source must expose its shared live service.")
    if live_service is not None and existing_live is not None and live_service is not existing_live:
        raise ValueError("The monitor and API must share one live service.")
    if sms_dispatcher is not None and existing_sms is not None and sms_dispatcher is not existing_sms:
        raise ValueError("The monitor and API must share one SMS dispatcher.")
    if live_service is None:
        live_service = existing_live if existing_live is not None else LiveQueryService(data_dir=resolved / "live-source")
    if sms_dispatcher is None:
        sms_dispatcher = existing_sms if existing_sms is not None else SMSDispatcher.from_env({
            **os.environ, "TERRA_SMS_DB_PATH": str(resolved / "sms.sqlite3")})
    if monitor is None:
        monitor = Monitor(Store(resolved / "farm.sqlite3"), source=LiveSource(live_service),
                          delivery=sms_dispatcher, clock=clock)
    else:
        if getattr(monitor, "source", None) is None:
            monitor.source = LiveSource(live_service)
        if getattr(monitor, "delivery", None) is None:
            monitor.delivery = sms_dispatcher

    app = create_app(service=live_service, sms_dispatcher=sms_dispatcher,
                     farm_monitor=monitor, web_root=web_root)
    app.state.runtime_state_dir = resolved
    app.state.runtime_monitor = monitor
    app.state.runtime_worker_thread = None
    app.state.runtime_worker_error = None
    app.state.runtime_worker_stop = None
    lock = threading.Lock()
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with original_lifespan(application):
            stop = threading.Event()
            thread = None
            if start_worker:
                from .farm_ops.__main__ import run_loop

                def work():
                    try:
                        run_loop(monitor, interval, stop=stop)
                    except Exception:
                        # Never print exception/provider bodies that can contain
                        # credentials. Persisted heartbeat exposes a stopped loop.
                        application.state.runtime_worker_error = "worker_stopped"
                        logging.getLogger("terra.runtime").error("Terra monitor worker stopped.")

                with lock:
                    previous = application.state.runtime_worker_thread
                    if previous is not None and previous.is_alive():
                        raise RuntimeError("This runtime already has an active monitor loop.")
                    thread = threading.Thread(target=work, name="terra-monitor", daemon=True)
                    application.state.runtime_worker_thread = thread
                    application.state.runtime_worker_stop = stop
                    application.state.runtime_worker_error = None
                    thread.start()
            try:
                yield
            finally:
                stop.set()
                if thread is not None:
                    await asyncio.to_thread(thread.join, shutdown_timeout)
                    if thread.is_alive():
                        application.state.runtime_worker_error = "worker_shutdown_pending"
                        logging.getLogger("terra.runtime").error(
                            "Terra monitor is finishing an in-flight operation after shutdown timeout.")

    app.router.lifespan_context = lifespan
    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=["127.0.0.1", "localhost", "::1"], default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8512)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--env-file", type=Path, help="Explicit private operator environment file")
    parser.add_argument("--sms-env-file", type=Path, help="Explicit private SMS configuration file")
    parser.add_argument("--interval", type=int, default=300, help="Monitor polling seconds (default: 300)")
    parser.add_argument("--no-worker", action="store_true", help="Serve the API/UI without starting polling")
    parser.add_argument("--web-root", type=Path, help="Built web client directory")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    try:
        _interval(args.interval)
        if args.env_file is not None:
            load_environment_file(args.env_file)
        if args.sms_env_file is not None:
            # The SMS module parses only its allowlisted keys. Keep this path
            # explicit; do not expose the file contents or enable SMS here.
            path = args.sms_env_file.expanduser().resolve()
            if not path.is_file():
                raise ValueError("The private SMS environment file is unavailable.")
            os.environ["TERRA_SMS_ENV_FILE"] = str(path)
    except (OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    try:
        app = create_runtime_app(state_dir=args.state_dir, start_worker=not args.no_worker,
                                 poll_seconds=args.interval, web_root=args.web_root)
    except (OSError, TypeError, ValueError):
        parser.error("Unable to initialize the local runtime. Check its configuration and state directory.")
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
