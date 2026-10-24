"""Local HTTP boundary for current location queries and the built JSON preview."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Protocol

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.responses import Response
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


def create_app(service: QueryService | None = None, web_root: str | Path | None = None) -> FastAPI:
    """Create a local app with an injectable synchronous query service.

    Service calls run outside the event loop. The service owns provider keys,
    server clocks, observations, snapshot identity and cache/freshness policy.
    """
    if service is None:
        from .live import LiveQueryService
        service = LiveQueryService()
    app = FastAPI(title="Terra monitoring", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.query_service = service

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> Response:
        # Pydantic errors contain raw submitted values, possibly NaN or private
        # fields. Do not serialize that exception or echo its input.
        return _error("invalid_query", "Provide valid coordinates, elevation and supported query fields.", 422)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> Response:
        if exc.status_code == 404:
            return _error("not_found", "The requested resource was not found.", 404)
        if exc.status_code == 405:
            return _error("method_not_allowed", "This request method is not supported.", 405)
        return _error("http_error", "The request could not be completed.", exc.status_code)

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

    # Reserve the API namespace ahead of static serving. An unknown API path
    # must never turn into the UI's index page or a success response.
    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE"])
    async def unknown_api(path: str) -> Response:
        if path in {"context", "assess", "health"}:
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=["127.0.0.1", "localhost", "::1"], default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8512)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    import uvicorn
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
