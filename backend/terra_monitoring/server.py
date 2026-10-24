"""Local HTTP boundary for current location queries and the built JSON preview."""
from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.routing import Match
from starlette.staticfiles import StaticFiles


class QueryService(Protocol):
    def context(self) -> dict: ...

    def assess(self, query: dict) -> dict: ...


class LocationQuery(BaseModel):
    """Browser input only; clocks, observations and provenance belong to the service."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    elevation_ft: float
    target_station_id: int | None = Field(default=None, gt=0)
    location_id: str | None = None
    refresh: bool = False

    @field_validator("location_id")
    @classmethod
    def nonempty_location(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("location_id must be nonempty")
        return value


class SMSDemoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    event_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")


def _sms_local_request(request: Request) -> bool:
    """Host checks block DNS rebinding; Origin checks block cross-site requests."""
    try:
        host = urlsplit("http://" + request.headers.get("host", ""))
        if host.hostname not in {"127.0.0.1", "localhost", "::1"} or host.username or host.password:
            return False
        if host.path or host.query or host.fragment:
            return False
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.headers['host']}":
            return False
        return request.headers.get("sec-fetch-site") not in {"cross-site"}
    except (ValueError, KeyError):
        return False


def _json_response(payload: dict, status_code: int = 200) -> Response:
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return Response(body, status_code=status_code, media_type="application/json",
                    headers={"Cache-Control": "no-store"})


def _error(code: str, message: str, status: int, *, retryable: bool = False) -> Response:
    return _json_response({"error": {"code": code, "message": message, "retryable": retryable}}, status)


def _service_error(exc: Exception) -> Response:
    # Import lazily: injected services and basic server tests do not need the
    # provider implementation, and module import never starts a source fetch.
    try:
        from .live import LiveQueryError
    except ImportError:
        pass
    else:
        if isinstance(exc, LiveQueryError):
            return _error(exc.code, exc.message, exc.status_code, retryable=exc.retryable)
    return _error("internal_error", "Unable to complete the location query.", 500)


def install_error_handlers(app: FastAPI) -> None:
    """One safe HTTP/validation policy for location, SMS and orchard routes."""
    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> Response:
        # Validation exceptions contain raw submitted values. Never echo them.
        if request.url.path.startswith("/api/sms/"):
            return _error("invalid_sms_request", "Provide only the supported SMS request fields.", 422)
        if request.url.path == "/api/assess":
            return _error("invalid_query", "Provide valid coordinates, elevation and supported query fields.", 422)
        return _error("invalid_request", "Provide supported fields with valid values.", 422)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> Response:
        if exc.status_code == 404:
            return _error("not_found", "The requested resource was not found.", 404)
        if exc.status_code == 405:
            return _error("method_not_allowed", "This request method is not supported.", 405)
        return _error("http_error", "The request could not be completed.", exc.status_code)


def create_app(service: QueryService | None = None, web_root: str | Path | None = None,
               sms_dispatcher: Any = None, farm_monitor: Any = None,
               mutation_guard: Any = None) -> FastAPI:
    """Create a local app with an injectable synchronous query service.

    Service calls run outside the event loop. The service owns provider keys,
    server clocks, observations, snapshot identity and cache/freshness policy.
    """
    if service is None:
        from .live import LiveQueryService
        service = LiveQueryService()
    app = FastAPI(title="Terra monitoring", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.query_service = service
    if sms_dispatcher is None:
        from .sms import SMSDispatcher
        sms_dispatcher = SMSDispatcher.from_env()
    app.state.sms_dispatcher = sms_dispatcher
    app.state.sms_control_token = secrets.token_urlsafe(32)
    app.state.farm_monitor = farm_monitor

    @app.middleware("http")
    async def local_page_headers(request: Request, call_next):
        response = await call_next(request)
        # Local SMS authorization must not be usable through a framed page.
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'; base-uri 'self'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    install_error_handlers(app)

    @app.get("/api/health")
    async def health() -> Response:
        return _json_response({"status": "ok"})

    async def invoke(operation: Any, *args: Any) -> Response:
        try:
            payload = await run_in_threadpool(operation, *args)
        except Exception as exc:
            return _service_error(exc)
        try:
            if not isinstance(payload, dict):
                raise TypeError("Service response must be an object")
            return _json_response(payload)
        except (TypeError, ValueError, OverflowError):
            return _error("invalid_service_response", "The location service returned an unavailable response.", 502)

    @app.get("/api/context")
    async def context() -> Response:
        return await invoke(app.state.query_service.context)

    @app.post("/api/assess")
    async def assess(query: LocationQuery) -> Response:
        return await invoke(app.state.query_service.assess, query.model_dump(exclude_none=True))

    def sms_authorized(request: Request) -> bool:
        supplied = request.headers.get("x-terra-sms-control", "")
        return _sms_local_request(request) and secrets.compare_digest(supplied.encode("utf-8"), app.state.sms_control_token.encode("ascii"))

    def local_mutation_guard(request: Request) -> None:
        """Shared raising dependency for local farm/monitor mutation routes."""
        if not sms_authorized(request):
            raise HTTPException(status_code=403, detail="Local authorization required")

    app.state.local_mutation_guard = local_mutation_guard

    async def sms_invoke(operation: Any, *args: Any) -> Response:
        try:
            payload = await run_in_threadpool(operation, *args)
            return _json_response(payload)
        except Exception:
            # Provider errors can include credentials, recipients or response bodies.
            return _error("sms_unavailable", "SMS could not be completed. Check delivery status before trying again.", 503)

    @app.get("/api/sms/status")
    async def sms_status(request: Request) -> Response:
        if not _sms_local_request(request):
            return _error("sms_local_only", "SMS controls require the local Terra page.", 403)
        try:
            status = await run_in_threadpool(app.state.sms_dispatcher.status)
            return _json_response({**status, "control_token": app.state.sms_control_token})
        except Exception:
            return _error("sms_unavailable", "SMS configuration is unavailable.", 503)

    @app.post("/api/sms/demo")
    async def sms_demo(body: SMSDemoRequest, request: Request) -> Response:
        if not sms_authorized(request):
            return _error("sms_unauthorized", "Reload the local Terra page to authorize SMS controls.", 403)
        return await sms_invoke(app.state.sms_dispatcher.demo, body.event_id)

    @app.get("/api/sms/receipt/{event_id}")
    async def sms_receipt(event_id: str, request: Request) -> Response:
        if not sms_authorized(request):
            return _error("sms_unauthorized", "Reload the local Terra page to authorize SMS controls.", 403)
        if len(event_id) > 80 or not event_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in event_id):
            return _error("invalid_sms_request", "Provide a valid demo event identifier.", 422)
        return await sms_invoke(app.state.sms_dispatcher.receipt, event_id)

    @app.post("/api/sms/disable")
    async def sms_disable(request: Request) -> Response:
        if not sms_authorized(request):
            return _error("sms_unauthorized", "Reload the local Terra page to authorize SMS controls.", 403)
        return await sms_invoke(app.state.sms_dispatcher.disable)

    if farm_monitor is not None:
        # The integration runtime supplies the one shared monitor. Register its
        # routes before the API namespace fallback and static app; never create
        # a worker or import farm storage on the basic query/demo path.
        from .farm_ops.api import create_router
        app.include_router(create_router(farm_monitor, mutation_guard=mutation_guard or app.state.local_mutation_guard))
        if getattr(farm_monitor, "store", None) is not None:
            # Storage-backed monitors support the durable conversation. Custom
            # embedded routers keep their existing lightweight dependency seam.
            from .conversation.api import create_router as create_conversation_router
            from .conversation.service import ConversationService

            app.state.tara_conversation = ConversationService(farm_monitor, notification_capability="browser_open")
            app.include_router(create_conversation_router(app.state.tara_conversation,
                               mutation_guard=mutation_guard or app.state.local_mutation_guard))

    # Reserve the API namespace ahead of static serving. An unknown API path
    # must never turn into the UI's index page or a success response.
    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE"])
    async def unknown_api(path: str, request: Request) -> Response:
        # Let the actual registered routes establish known-path/wrong-method
        # semantics, including optional orchard routes. No shadow endpoint list.
        if any(route.matches(request.scope)[0] == Match.PARTIAL for route in app.router.routes):
            return _error("method_not_allowed", "This request method is not supported.", 405)
        return _error("not_found", "The requested API endpoint was not found.", 404)

    @app.api_route("/api", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE"])
    async def api_root() -> Response:
        return _error("not_found", "The requested API endpoint was not found.", 404)

    build = Path(web_root).resolve() if web_root is not None else Path(__file__).resolve().parents[2] / "web" / "dist" / "client"
    if build.is_dir() and (build / "index.html").is_file():
        app.mount("/", StaticFiles(directory=build, html=True), name="web-preview")
    else:
        @app.get("/")
        async def missing_preview() -> Response:
            return _error("web_build_missing", "The web preview build is unavailable.", 503)
    return app


def main(argv: list[str] | None = None) -> None:
    """Compatibility query server: canonical runtime, still no implicit worker."""
    from .runtime import main as runtime_main
    runtime_main([*(sys.argv[1:] if argv is None else argv), "--no-worker"])


if __name__ == "__main__":
    main()
