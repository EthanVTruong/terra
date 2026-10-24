"""Run Terra's farm monitor independently of the browser; no external sends by default."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading

from .service import Monitor
from .source import LiveSource
from .store import Store, encoded
from ..configuration import DEFAULT_STATE_DIR


def _monitor(database, state_dir=None):
    state_dir = Path(state_dir or DEFAULT_STATE_DIR).expanduser().resolve()
    store = Store(database or os.environ.get("TERRA_FARM_DB") or state_dir / "farm.sqlite3")
    delivery = None
    try:
        from terra_monitoring.sms import SMSDispatcher
    except ImportError:
        pass
    else:
        delivery = SMSDispatcher.from_env(state_dir=state_dir)
    return Monitor(store, source=LiveSource(), delivery=delivery)


def run_loop(monitor, interval=60, *, stop=None, max_checks=None):
    if not 1 <= interval <= 3600:
        raise ValueError("Polling interval must be 1-3600 seconds")
    stop = stop or threading.Event()
    runtime = {"running": True, "pid": os.getpid(), "interval_seconds": interval,
               "started_at": datetime.now(timezone.utc).isoformat()}
    count = 0
    try:
        while not stop.is_set():
            runtime["heartbeat"] = datetime.now(timezone.utc).isoformat()
            with monitor.store.connect(write=True) as db:
                monitor.store.set_state(db, "worker-runtime", runtime)
            try:
                monitor.check()
                runtime["last_cycle_result"] = "completed"
            except Exception:
                # Pending events/messages survive an interrupted cycle. Never log provider credentials.
                runtime["last_cycle_result"] = "cycle_failed_will_retry"
            count += 1
            if max_checks is not None and count >= max_checks:
                break
            remaining = interval
            while remaining > 0 and not stop.is_set():
                duration = min(remaining, 20)
                stop.wait(duration)
                remaining -= duration
                runtime["heartbeat"] = datetime.now(timezone.utc).isoformat()
                with monitor.store.connect(write=True) as db:
                    monitor.store.set_state(db, "worker-runtime", runtime)
    finally:
        runtime.update(running=False, heartbeat=datetime.now(timezone.utc).isoformat())
        with monitor.store.connect(write=True) as db:
            monitor.store.set_state(db, "worker-runtime", runtime)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, help="Shared absolute SQLite path; defaults to package-root .terra-local or TERRA_FARM_DB")
    parser.add_argument("--state-dir", type=Path, help="Shared canonical runtime state directory")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add-orchard")
    add.add_argument("--name", default="West Orchard")
    add.add_argument("--id", dest="orchard_id")
    add.add_argument("--lat", type=float, default=38.6)
    add.add_argument("--lon", type=float, default=-121.84)
    add.add_argument("--elevation-ft", type=float, default=70)
    sub.add_parser("once")
    run = sub.add_parser("run")
    run.add_argument("--interval", type=int, default=60)
    run.add_argument("--max-checks", type=int, help="Bound a smoke test; omit for continuous operation")
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8514)
    serve.add_argument("--host", choices=["127.0.0.1", "localhost", "::1"], default="127.0.0.1")
    status = sub.add_parser("status")
    status.add_argument("orchard_id")
    messages = sub.add_parser("messages")
    messages.add_argument("orchard_id")
    ask = sub.add_parser("ask")
    ask.add_argument("orchard_id")
    ask.add_argument("question")
    subscribe = sub.add_parser("sms-opt-in")
    subscribe.add_argument("orchard_id")
    subscribe.add_argument("--enable", action="store_true", help="Explicit opt-in for the separately configured private test recipient")
    history = sub.add_parser("import-history")
    history.add_argument("orchard_id")
    history.add_argument("path", type=Path)
    fnric = sub.add_parser("import-fnric-details")
    fnric.add_argument("orchard_id")
    fnric.add_argument("path", type=Path)
    fnric.add_argument("--station-id", type=int, default=6)
    demo = sub.add_parser("demo")
    demo.add_argument("--orchard-id", default="demo-west")
    args = parser.parse_args()
    if args.command == "demo":
        from .demo import run_demo
        store = Store(args.database or Path(__file__).resolve().parents[3] / ".terra-local" / "farm-chill-ops-demo.sqlite3")
        print(encoded(run_demo(store, args.orchard_id)))
        return
    monitor = _monitor(args.database, args.state_dir)
    if args.command == "add-orchard":
        result = monitor.create_orchard(args.name, orchard_id=args.orchard_id, lat=args.lat, lon=args.lon, elevation_ft=args.elevation_ft)
    elif args.command == "once":
        result = monitor.check()
    elif args.command == "run":
        if args.max_checks is not None and args.max_checks < 1:
            parser.error("max-checks must be positive")
        try:
            run_loop(monitor, args.interval, max_checks=args.max_checks)
        except KeyboardInterrupt:
            pass
        result = monitor.worker_status()
    elif args.command == "serve":
        if not 1 <= args.port <= 65535:
            parser.error("port must be 1-65535")
        import uvicorn
        from ..runtime import create_runtime_app
        uvicorn.run(create_runtime_app(monitor=monitor, state_dir=args.state_dir, start_worker=False),
                    host=args.host, port=args.port)
        return
    elif args.command == "status":
        result = monitor.status(args.orchard_id)
    elif args.command == "messages":
        result = monitor.store.messages(args.orchard_id)
    elif args.command == "ask":
        result = monitor.ask(args.orchard_id, args.question)
    elif args.command == "sms-opt-in":
        result = monitor.set_sms_opt_in(args.orchard_id, args.enable)
    else:
        if args.path.stat().st_size > 64 * 1024 * 1024:
            parser.error("Import exceeds the bounded archive size")
        data = json.loads(args.path.read_text(encoding="utf-8"))
        result = (monitor.import_estimates(args.orchard_id, data) if args.command == "import-history"
                  else monitor.retain_fnric(args.orchard_id, args.station_id, data))
    print(encoded(result))


if __name__ == "__main__":
    main()
