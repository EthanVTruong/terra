"""Opt-in, offline replay bridge for the actual Terra UI and station lab.

Run this module explicitly; importing it neither opens journals nor starts work.
No live configuration, account vault, weather provider or external transport is
loaded. The normal runtime and the port-8520 demonstration are independent.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import inspect
from pathlib import Path
import sqlite3

import pandas as pd
from fastapi import Body, HTTPException, Request
from fastapi.responses import FileResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ..assessment import assess_location
from ..farm_ops.service import orchard_profile
from ..farm_ops.conversational_writer import ConversationalWriter
from ..farm_ops.store import fingerprint, utc
from ..farm_ops.writer import Writer
from ..server import _json_response, create_app as production_app
from .engine import DemoSimulation, ROOT

STATE_KEY = "full-flow-replay:version"
VERSION = 1


class DisabledSMS:
    """An explicit inert dependency, so native server setup never calls from_env."""

    def status(self):
        return {"configured": False, "enabled": False, "demo_enabled": False,
                "suppressed": True, "recipient": None, "content_mode": "terra"}

    def demo(self, event_id):
        return {"event_id": event_id, "status": "disabled", "provider_delivery": False}

    def receipt(self, event_id):
        return {"event_id": event_id, "status": "not_found", "provider_delivery": False}

    def disable(self):
        return {"suppressed": True}


def _state_directory(value):
    directory = Path(value or ROOT / ".terra-local/full-flow-replay").resolve()
    journal = directory / "demo.sqlite3"
    if journal.exists():
        # Open read-only before constructing an engine. In particular, refuse
        # the existing station-demo journal instead of adopting or changing it.
        try:
            with sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True) as db:
                row = db.execute("SELECT payload FROM state WHERE key=?", (STATE_KEY,)).fetchone()
        except sqlite3.Error as exc:
            raise ValueError("Choose a fresh full-flow replay state directory") from exc
        if row is None or row[0] != str(VERSION):
            raise ValueError("This journal belongs to another workflow; choose a fresh replay directory")
    return directory


class UserFarmStore:
    """Native persistence with core station-demo profiles hidden from growers."""

    def __init__(self, store):
        self._store = store

    def __getattr__(self, name):
        return getattr(self._store, name)

    @staticmethod
    def _guard(orchard_id):
        if str(orchard_id).startswith("sim-"):
            raise KeyError("The station-lab core farm is not a grower profile")

    def orchards(self):
        return [profile for profile in self._store.orchards() if not profile["id"].startswith("sim-")]

    def orchard(self, orchard_id):
        self._guard(orchard_id)
        return self._store.orchard(orchard_id)

    def save_orchard(self, profile, **kwargs):
        self._guard(profile["id"])
        return self._store.save_orchard(profile, **kwargs)

    def messages(self, orchard_id, *args, **kwargs):
        self._guard(orchard_id)
        return self._store.messages(orchard_id, *args, **kwargs)

    def details(self, orchard_id, message_id):
        self._guard(orchard_id)
        return self._store.details(orchard_id, message_id)

    def message(self, message_id):
        message = self._store.message(message_id)
        return None if message and message["orchard_id"].startswith("sim-") else message


class ReplayBridge:
    """Use one real monitor/store, but keep every user-created farm injected."""

    source = None  # No acquisition or worker is configured in this QA process.

    def __init__(self, engine):
        self.engine = engine
        self.monitor = engine.monitor
        self.store = UserFarmStore(engine.store)
        if self.monitor.source is not None or self.monitor.delivery is not None:
            raise ValueError("Full-flow replay requires a monitor without acquisition or delivery")
        if any(profile.get("mode") != "injected" for profile in engine.store.orchards()):
            raise ValueError("The replay journal must contain only injected profiles")

    def __getattr__(self, name):
        return getattr(self.monitor, name)

    def assessment(self, profile):
        with self.engine._lock:
            engine = self.engine
            now = pd.Timestamp(engine.session["clock"])
            snapshot = fingerprint({"source": engine.provenance["sha256"],
                                    "weather_source": engine.weather_provenance,
                                    "frames": engine.session["frames"], "sensors": engine.session.get("sensors", []),
                                    "clock": now.isoformat()})
            return assess_location(**{key: profile[key] for key in ("lat", "lon", "elevation_ft")},
                valid_utc=now, as_of=now, observations=engine._inputs(), stations=engine.stations,
                snapshot_id="full-flow-replay:" + snapshot, data_mode="injected",
                location_id=profile["id"], reference=engine.reference,
                geometry_source="supplied farm coordinates and elevation; controlled historical replay")

    def evaluate(self, profile):
        packet = self.assessment(profile)
        now = utc(self.engine.session["clock"])
        self.monitor.apply(profile["id"], {"latest": packet}, now=now)
        self.monitor.flush(profile["id"], now=now)
        return packet

    def create_orchard(self, name="My farm", *, sms_opt_in=False, **options):
        with self.engine._lock:
            if sms_opt_in or str(options.get("orchard_id", "")).startswith("sim-"):
                raise ValueError("The core station-demo farm and external delivery are not editable")
            options["mode"] = "injected"
            supplied = {key: options.pop(key) for key in ("crops", "crop", "variety", "crop_description") if key in options}
            supported = inspect.signature(orchard_profile).parameters
            if "crops" in supported:
                # The conversational profile owner supplies native validation
                # after integration; preserve that implementation unchanged.
                profile = orchard_profile(name, **options, **supplied)
            else:
                # Older native forms cannot supply crop metadata. Do not carry
                # their historical almond/cultivar defaults into a new QA farm.
                if supplied:
                    raise ValueError("Crop-aware setup requires the integrated Terra profile API")
                profile = orchard_profile(name, **options)
                profile.update(crop=None, crops=[], variety=None,
                    target={"hours": None, "kind": "not_configured", "agronomic_requirement_verified": False,
                            "source": None, "note": "No crop-specific reference was supplied."})
            saved = self.store.save_orchard(profile, sms_opt_in=False, now=self.clock())
            self.evaluate(saved)
            return saved

    def set_sms_opt_in(self, orchard_id, enabled):
        self.store.orchard(orchard_id)
        if enabled:
            raise ValueError("External delivery is disabled in full-flow replay")
        return self.monitor.set_sms_opt_in(orchard_id, False)

    def status(self, orchard_id, **kwargs):
        self.store.orchard(orchard_id)
        return self.monitor.status(orchard_id, **kwargs)

    def ask(self, orchard_id, question, **kwargs):
        self.store.orchard(orchard_id)
        return self.monitor.ask(orchard_id, question, **kwargs)

    def check(self):
        with self.engine._lock:
            return {"mode": "injected", "worker_started": False,
                    "assessments": [self.evaluate(p) for p in self.store.orchards() if not p["id"].startswith("sim-")]}

    def action(self, body):
        with self.engine._lock:
            # Advance one hour at a time so all user farms see the same causal
            # recovery evidence as the station lab, including the recover preset.
            kind = body.get("type") if isinstance(body, dict) else None
            if kind == "advance" and set(body) != {"type", "hours"}:
                raise ValueError("Provide only the supported observation-hour action")
            if kind == "reset" and any(not p["id"].startswith("sim-") for p in self.store.orchards()):
                raise ValueError("Use a fresh QA journal to restart time after saving a user farm")
            hours = body.get("hours") if kind == "advance" else 3 if body == {"type": "preset", "name": "recover"} else None
            if hours is not None:
                if isinstance(hours, bool) or not isinstance(hours, int) or not 1 <= hours <= 6:
                    raise ValueError("Advance between one and six observation hours")
                from datetime import timedelta
                from .engine import END
                if utc(self.engine.session["clock"]) + timedelta(hours=hours) > utc(END):
                    raise ValueError("Not enough saved observation hours remain")
                if kind != "advance":
                    self.engine.action({"type": "preset", "name": "normal"})
                for _ in range(hours):
                    self.engine.action({"type": "advance", "hours": 1})
                    self.check()
                return self.engine.state()
            result = self.engine.action(body)
            # Reset only creates a new core-demo run; user profiles and their
            # immutable notes remain in this QA journal. See the replay runbook.
            self.check()
            return result


class ReplayQuery:
    def __init__(self, bridge):
        self.bridge = bridge

    def context(self):
        return {"schema_version": "terra-live-context-v1", "source": "CIMIS", "configured": False,
                "station_presets": deepcopy(self.bridge.engine.station_metadata), "data_mode": "injected",
                "replay_only": True, "note": "Saved historical weather; current provider access is disabled."}

    def assess(self, query):
        return self.bridge.assessment({"id": query.get("location_id") or "qa-location", **query})


def _asset(directory, path):
    candidate = (directory / path).resolve()
    if not candidate.is_relative_to(directory) or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")
    return candidate


def create_app(*, simulation=None, state_dir=None, web_root=None, demo_root=None, conversation_factory=None):
    """Dependencies are explicit; factories/tests never load live configuration."""
    if simulation is None:
        directory = _state_directory(state_dir)
        simulation = DemoSimulation(state_dir=directory)
        with simulation.store.connect(write=True) as db:
            simulation.store.set_state(db, STATE_KEY, VERSION)
    bridge = ReplayBridge(simulation)
    bridge.monitor.writer = ConversationalWriter(Writer(key="", model="", voice="text"))
    production = Path(web_root or ROOT / "web/dist/client").resolve()
    demo = Path(demo_root or ROOT / "web/demo/dist/client").resolve()
    app = production_app(service=ReplayQuery(bridge), web_root=production,
                         sms_dispatcher=DisabledSMS(), farm_monitor=bridge)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])
    app.state.full_flow_bridge = bridge

    # Insert the bridge and optional native conversation router before the
    # production factory's reserved API fallback and static mount.
    existing = list(app.router.routes)
    app.router.routes.clear()
    # Newer production factories already own the conversation instance/routes.
    # Reuse that instance even when a fallback factory was explicitly supplied;
    # a QA bridge must never replace or shadow native behavior.
    conversation = getattr(app.state, "tara_conversation", None)
    if conversation is None and conversation_factory is None:
        try:
            from ..conversation.service import ConversationService
        except ModuleNotFoundError as exc:
            if exc.name not in {"terra_monitoring.conversation", "terra_monitoring.conversation.service"}:
                raise
        else:
            conversation_factory = ConversationService
    if conversation is None and conversation_factory is not None:
        from ..conversation.api import create_router
        conversation = conversation_factory(bridge, notification_capability="browser_open")
        app.include_router(create_router(conversation, mutation_guard=app.state.local_mutation_guard))
        app.state.tara_conversation = conversation

    @app.middleware("http")
    async def replay_actions_are_local(request: Request, call_next):
        if request.url.path.startswith("/api/demo/") and request.method != "GET":
            from ..server import _sms_local_request
            if not _sms_local_request(request):
                return _json_response({"error": "same_origin_required"}, 403)
        return await call_next(request)

    @app.get("/api/demo/state")
    def demo_state():
        return simulation.state()

    @app.post("/api/demo/action")
    def demo_action(body: dict = Body(...)):
        try:
            return bridge.action(body)
        except (ValueError, KeyError):
            raise HTTPException(status_code=422, detail="Invalid replay action") from None

    @app.get("/api/demo/messages/{message_id}/details")
    def demo_details(message_id: str):
        try:
            return simulation.details(message_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown demo note") from None

    @app.post("/api/demo/point")
    def demo_point(body: dict = Body(...)):
        if set(body) != {"lat", "lon"}:
            raise HTTPException(status_code=422, detail="Provide lat and lon")
        try:
            return simulation.point(body["lat"], body["lon"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/demo/questions")
    def demo_question(body: dict = Body(...)):
        if set(body) - {"text", "request_id"} or "text" not in body:
            raise HTTPException(status_code=422, detail="Invalid replay question")
        try:
            return simulation.question(body["text"], body.get("request_id"))
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid replay question") from None

    @app.get("/stations")
    @app.get("/stations/")
    def station_page():
        index = _asset(demo, "index.html").read_text(encoding="utf-8")
        return Response(index.replace("/assets/", "/demo-assets/").replace("/xvf-reference.woff2", "/demo-static/xvf-reference.woff2"),
                        media_type="text/html")

    @app.get("/demo-assets/{path:path}")
    def demo_asset(path: str):
        candidate = _asset(demo / "assets", path)
        if candidate.suffix == ".css":
            return Response(candidate.read_text(encoding="utf-8").replace("/xvf-reference.woff2", "/demo-static/xvf-reference.woff2"), media_type="text/css")
        return FileResponse(candidate)

    @app.get("/demo-static/{path:path}")
    def demo_static(path: str):
        return FileResponse(_asset(demo, path))

    @app.get("/terra-cube-1080.png")
    def station_brand():
        return FileResponse(_asset(demo, "terra-cube-1080.png"))

    @app.get("/terra")
    @app.get("/terra/")
    def terra_page():
        return FileResponse(_asset(production, "index.html"))

    app.router.routes.extend(existing)
    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description="Opt-in offline QA of production Terra with controlled weather")
    parser.add_argument("--host", choices=["127.0.0.1", "localhost", "::1"], default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8530)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--web-root", type=Path)
    parser.add_argument("--demo-root", type=Path)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    import uvicorn
    uvicorn.run(create_app(state_dir=args.state_dir, web_root=args.web_root, demo_root=args.demo_root),
                host=args.host, port=args.port)


if __name__ == "__main__":
    main()
