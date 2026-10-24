"""Loopback-only API and two local SPA entrypoints for the isolated simulation."""
from __future__ import annotations

from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .engine import DemoSimulation, ROOT


def create_app(*, simulation=None, state_dir=None, static_dir=None):
    app = FastAPI(title="Terra local simulation", docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    engine = simulation or DemoSimulation(state_dir=state_dir)
    app.state.simulation = engine

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method == "POST" and origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"error": "same_origin_required"}, status_code=403)
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/demo/state")
    def state():
        return engine.state()

    @app.post("/api/demo/action")
    def action(body: dict = Body(...)):
        try:
            return engine.action(body)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/demo/questions")
    def question(body: dict = Body(...)):
        if set(body) - {"text", "request_id"} or "text" not in body:
            raise HTTPException(status_code=422, detail="Provide text and an optional request_id")
        try:
            return engine.question(body["text"], body.get("request_id"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/demo/messages/{message_id}/details")
    def details(message_id: str):
        try:
            return engine.details(message_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Message not found in this demo run") from exc

    directory = Path(static_dir or ROOT / "web/demo/dist/client").resolve()
    assets = directory / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="demo-assets")

    @app.get("/{path:path}")
    def page(path: str):
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Unknown demo API")
        candidate = (directory / path).resolve()
        if candidate.is_relative_to(directory) and candidate.is_file():
            return FileResponse(candidate)
        if path not in {"", "stations", "terra", "stations/", "terra/"}:
            raise HTTPException(status_code=404, detail="Unknown demonstration app")
        index = directory / "index.html"
        if not index.is_file():
            return JSONResponse({"error": "demo_frontend_not_built",
                "detail": "The local simulation API is ready; build web/demo to open its two apps."}, status_code=503)
        return FileResponse(index)

    return app
