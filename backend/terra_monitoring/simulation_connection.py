"""Explicit browser-session connection to an isolated station replay.

The normal monitor keeps its source, journals and worker. Only requests from a
connected browser are dispatched to the replay's native conversation and API.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import secrets
from threading import RLock

from fastapi import Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

COOKIE = "terra_simulation"
SOURCE_HEADER = "X-Terra-Source-Context"
API_PREFIXES = ("/api/tara", "/api/orchards", "/api/monitor", "/api/sms", "/api/demo")
CONTROLS = {"/api/simulation/connect", "/api/simulation/connection"}


class SourceConflict(Exception):
    def __init__(self, code):
        self.code = code


def _source_error(code):
    from .server import _error
    message = ("Wait for the current request to finish before changing the source."
               if code == "source_context_busy" else
               "The simulation session expired. Reload to explicitly choose the source."
               if code == "simulation_context_expired" else
               "The chat source changed. Reload before continuing; this request was not applied.")
    return _error(code, message, 409)


def _namespace(path, prefix):
    return path == prefix or path.startswith(prefix + "/")


class ConnectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    orchard_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")


class SimulationConnection:
    def __init__(self, app, monitor, state_dir, web_root):
        self.app, self.monitor = app, monitor
        self.directory, self.web_root = Path(state_dir) / "simulator-sessions", web_root
        self.sessions = {}
        self.lock = RLock()
        self.live_context = "live-" + secrets.token_urlsafe(24)
        self.active = {}
        self.switching = set()

    def session(self, token):
        with self.lock:
            return self.sessions.get(token)

    def status(self, token):
        replay = self.session(token)
        setup = replay.state.full_flow_bridge.store.get("tara:setup", {}) if replay else {}
        expired = bool(token) and replay is None
        return {"connected": replay is not None, "data_mode": None if expired else "injected" if replay else "current",
                "station_url": "/stations" if replay else None, "orchard_id": setup.get("orchard_id"),
                "source_context": self.context(token), "source_expired": expired}

    def context(self, token):
        if not token:
            return self.live_context
        # This public identity is not the HttpOnly routing cookie or an auth
        # token. Runtime restart changes it, including for expired cookies.
        digest = hashlib.sha256((self.live_context + ":" + token).encode("utf-8")).hexdigest()
        return ("simulation-" if self.session(token) is not None else "expired-") + digest

    def validate_context(self, token, expected, *, recovery=False):
        context = self.context(token)
        if not expected or expected != context:
            raise SourceConflict("source_context_mismatch")
        if token and self.session(token) is None and not recovery:
            raise SourceConflict("simulation_context_expired")
        return context

    def begin(self, token, expected, *, switching=False):
        with self.lock:
            context = self.validate_context(token, expected, recovery=switching)
            if context in self.switching or switching and self.active.get(context, 0):
                raise SourceConflict("source_context_busy")
            if switching:
                self.switching.add(context)
            else:
                self.active[context] = self.active.get(context, 0) + 1
            return context

    def finish(self, context, *, switching=False):
        with self.lock:
            if switching:
                self.switching.discard(context)
            elif self.active.get(context, 0) <= 1:
                self.active.pop(context, None)
            else:
                self.active[context] -= 1

    def pending(self, token):
        replay = self.session(token)
        if token and replay is None:
            return False  # Explicit recovery of an expired routing cookie.
        store = replay.state.full_flow_bridge.store if replay else self.monitor.store
        with store.connect() as db:
            return db.execute("SELECT 1 FROM tara_requests WHERE status='pending' LIMIT 1").fetchone() is not None

    def disconnect(self, token):
        with self.lock:
            self.sessions.pop(token, None)
        return self.status(None)

    def _copy_conversation(self, bridge, orchard_id):
        """Copy immutable context, never live readings, subscriptions or watch state."""
        source, target = self.monitor.store, bridge.store
        setup = source.get("tara:setup")
        selected = orchard_id or (setup or {}).get("orchard_id")
        profile = deepcopy(source.orchard(selected)) if selected else None
        if selected and str(selected).startswith("sim-"):
            raise ValueError("Choose a grower profile")
        thread = selected or "onboarding"
        with source.connect() as origin:
            origin.execute("BEGIN")  # One coherent snapshot while the live worker may publish.
            if origin.execute("SELECT 1 FROM tara_requests WHERE thread=? AND status='pending'", (thread,)).fetchone():
                raise HTTPException(status_code=409, detail="Wait for the current conversation request to finish")
            turns = origin.execute("SELECT * FROM tara_turns WHERE thread=? ORDER BY rowid", (thread,)).fetchall()
            requests = origin.execute("SELECT * FROM tara_requests WHERE thread=? AND status!='pending'", (thread,)).fetchall()
            # Retain old native notes and their evidence so the same visible
            # conversation can still open its original readings after connection.
            messages = origin.execute("SELECT * FROM messages WHERE orchard=?", (selected,)).fetchall() if selected else []
            events = origin.execute("SELECT * FROM events WHERE orchard=? AND published=1", (selected,)).fetchall() if selected else []
            evidence = {}
            for row in events:
                identifier = json.loads(row["payload"]).get("evidence_id")
                if identifier:
                    packet = origin.execute("SELECT * FROM evidence WHERE id=?", (identifier,)).fetchone()
                    if packet:
                        evidence[identifier] = packet
        if profile:
            profile["mode"] = "injected"
            target.save_orchard(profile, sms_opt_in=False, now=bridge.clock())
            if not setup or setup.get("orchard_id") != selected:
                setup = {"stage": "complete", "orchard_id": selected, "crops": profile.get("crops", [profile["crop"]]),
                         "variety": profile.get("variety"), "notification_preference": None,
                         "location": {key: profile[key] for key in ("name", "lat", "lon", "elevation_ft")}}
        with target.connect(write=True) as destination:
            for table, rows in (("tara_turns", turns), ("tara_requests", requests), ("messages", messages),
                                ("events", events), ("evidence", list(evidence.values()))):
                for row in rows:
                    columns = ",".join(row.keys())
                    placeholders = ",".join("?" for _ in row)
                    destination.execute(f"INSERT OR IGNORE INTO {table}({columns}) VALUES({placeholders})", tuple(row))
            if setup is not None:
                target.set_state(destination, "tara:setup", setup)
            preference = source.get("tara:notifications:" + thread)
            if preference is not None:
                target.set_state(destination, "tara:notifications:" + thread, preference)
        if profile:
            # The replay's inert writer initializes its baseline. Connecting
            # alone never invokes the configured account/provider.
            bridge.evaluate(profile)

    def connect(self, token, orchard_id):
        with self.lock:
            if token in self.sessions:
                return token, self.status(token)
        # Validate selected context before making a new replay directory. The
        # computational setup stays outside the registry lock so other browser
        # requests never wait on a synchronous lock held by this worker thread.
        setup = self.monitor.store.get("tara:setup", {})
        selected = orchard_id or setup.get("orchard_id")
        if selected:
            self.monitor.store.orchard(selected)
        from .demo_simulation.full_flow import create_app
        token = secrets.token_urlsafe(32)
        replay = create_app(state_dir=self.directory / token, web_root=self.web_root)
        replay.state.sms_control_token = self.app.state.sms_control_token
        bridge = replay.state.full_flow_bridge
        self._copy_conversation(bridge, orchard_id)
        writer = getattr(self.monitor, "writer", None)
        if writer is not None:
            from .farm_ops.service import Monitor
            # The lab's internal core farm stays on its inert writer. Only
            # grower notes/questions reuse the existing connected plan.
            bridge.monitor = Monitor(bridge.engine.store, writer=writer, clock=bridge.engine.monitor.clock)
        with self.lock:
            self.sessions[token] = replay
            return token, self.status(token)


class ReplayDispatch:
    def __init__(self, app, connection):
        self.app, self.connection = app, connection

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope["path"]
            routed = path in {"/api/context", "/api/assess", "/stations", "/stations/", "/terra", "/terra/", "/terra-cube-1080.png"} or any(
                _namespace(path, prefix) for prefix in (*API_PREFIXES, "/demo-assets", "/demo-static"))
            request = Request(scope)
            token = request.cookies.get(COOKIE)
            expected = request.headers.get(SOURCE_HEADER)
            mutation = scope.get("method", "GET") not in {"GET", "HEAD", "OPTIONS"}
            api = _namespace(path, "/api")
            held = None
            try:
                if path not in CONTROLS:
                    if api and mutation:
                        # Preserve existing authorization/origin rejection
                        # before introducing a source-conflict response.
                        if any(_namespace(path, prefix) for prefix in (*API_PREFIXES[:-1], "/api/chatgpt")):
                            self.connection.app.state.local_mutation_guard(request)
                        elif _namespace(path, "/api/demo"):
                            from .server import _sms_local_request
                            if not _sms_local_request(request):
                                raise HTTPException(status_code=403)
                        # This also protects shared ChatGPT configuration APIs,
                        # but never dispatches their account/provider to replay.
                        held = self.connection.begin(token, expected)
                # Resolve once under the same registry lock as read validation.
                # A disconnect after this snapshot must never turn a validated
                # replay read into a live-store read between two lookups.
                with self.connection.lock:
                    if path not in CONTROLS and api and not mutation and expected:
                        self.connection.validate_context(token, expected,
                            recovery=path == "/api/sms/status")
                    replay = self.connection.session(token) if routed else None
                    if path not in CONTROLS and routed and token and replay is None and path != "/api/sms/status":
                        raise SourceConflict("simulation_context_expired")
                await (replay if replay is not None else self.app)(dict(scope), receive, send)
            except SourceConflict as exc:
                await _source_error(exc.code)(scope, receive, send)
            except HTTPException as exc:
                from .server import _error
                await _error("http_error", "The request could not be completed.", exc.status_code)(scope, receive, send)
            finally:
                if held is not None:
                    self.connection.finish(held)
            return
        await self.app(scope, receive, send)


def attach_simulation_connection(app, *, monitor, state_dir, web_root=None):
    """Register controls before the API fallback; constructing this is inert."""
    from .server import _json_response, _error, _sms_local_request

    connection = SimulationConnection(app, monitor, state_dir, web_root)
    app.state.simulation_connection = connection
    existing = list(app.router.routes)
    app.router.routes.clear()
    guard = getattr(app.state, "local_mutation_guard", None)

    async def unavailable_guard():
        raise HTTPException(status_code=403, detail="Local authorization required")

    protected = [Depends(guard or unavailable_guard)]

    @app.get("/api/simulation/connection")
    async def status(request: Request):
        if not _sms_local_request(request):
            return _error("simulation_local_only", "Open the local Terra page to connect the simulator.", 403)
        return _json_response(connection.status(request.cookies.get(COOKIE)))

    @app.post("/api/simulation/connect", dependencies=protected)
    async def connect(body: ConnectionInput, request: Request):
        held = None
        try:
            held = connection.begin(request.cookies.get(COOKIE), request.headers.get(SOURCE_HEADER), switching=True)
            if await run_in_threadpool(connection.pending, request.cookies.get(COOKIE)):
                raise SourceConflict("source_context_busy")
            token, result = await run_in_threadpool(connection.connect, request.cookies.get(COOKIE), body.orchard_id)
        except SourceConflict as exc:
            return _source_error(exc.code)
        except KeyError:
            return _error("not_found", "Choose an existing farm to connect.", 404)
        except (OSError, ValueError):
            return _error("simulation_unavailable", "The simulator could not connect. Your live farm is unchanged.", 503)
        finally:
            if held is not None:
                connection.finish(held, switching=True)
        response = _json_response(result)
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict", path="/")
        return response

    @app.delete("/api/simulation/connection", dependencies=protected)
    async def disconnect(request: Request):
        held = None
        try:
            token = request.cookies.get(COOKIE)
            held = connection.begin(token, request.headers.get(SOURCE_HEADER), switching=True)
            if await run_in_threadpool(connection.pending, token):
                raise SourceConflict("source_context_busy")
            result = connection.disconnect(token)
        except SourceConflict as exc:
            return _source_error(exc.code)
        finally:
            if held is not None:
                connection.finish(held, switching=True)
        response = _json_response(result)
        response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict")
        return response

    app.router.routes.extend(existing)
    app.add_middleware(ReplayDispatch, connection=connection)
    return connection
