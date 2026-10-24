"""Additive orchard API. The existing /api/assess remains a pure location query."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .service import Monitor
from ..server import _json_response as response, _error


class OrchardInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    name: str = Field(default="West Orchard", min_length=1, max_length=80)
    orchard_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    lat: float = Field(default=38.6, ge=-90, le=90)
    lon: float = Field(default=-121.84, ge=-180, le=180)
    elevation_ft: float = Field(default=70.0, ge=-1500, le=20000)
    sms_opt_in: bool = False


class QuestionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    question: str = Field(min_length=1, max_length=500)
    request_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,80}$")


class SubscriptionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool


async def _call(function, *args, **kwargs):
    try:
        return response(await run_in_threadpool(function, *args, **kwargs))
    except KeyError:
        return _error("not_found", "Orchard or message not found.", 404)
    except ValueError:
        return _error("invalid_request", "Provide a valid orchard, preference or supported question.", 422)
    except Exception:
        return _error("monitor_unavailable", "The monitor could not complete this request.", 503)


def create_router(monitor: Monitor, *, mutation_guard=None):
    router = APIRouter()
    async def unavailable_guard():
        raise HTTPException(status_code=403, detail="Mutations require the shared local-session guard")
    protected = [Depends(mutation_guard or unavailable_guard)]

    @router.get("/api/orchards")
    async def orchards():
        return await _call(lambda: {"orchards": monitor.store.orchards()})

    @router.post("/api/orchards", dependencies=protected)
    async def create(body: OrchardInput, background_tasks: BackgroundTasks):
        result = await _call(monitor.create_orchard, **body.model_dump())
        if result.status_code == 200 and monitor.source is not None:
            # The process completes this first bounded check after returning setup.
            # Ongoing checks belong to the single restart-safe worker.
            background_tasks.add_task(monitor.check)
        return result

    @router.get("/api/orchards/{orchard_id}")
    async def status(orchard_id: str):
        return await _call(monitor.status, orchard_id)

    @router.get("/api/orchards/{orchard_id}/messages")
    async def messages(orchard_id: str):
        def read():
            monitor.store.orchard(orchard_id)
            return {"schema_version": "terra-farm-feed-v1", "orchard_id": orchard_id,
                    "messages": [{**m, "delivery": monitor.delivery_status(m)}
                                 for m in monitor.store.messages(orchard_id)], "monitor": monitor.status(orchard_id)}
        return await _call(read)

    @router.get("/api/orchards/{orchard_id}/messages/{message_id}/details")
    async def details(orchard_id: str, message_id: str):
        return await _call(monitor.store.details, orchard_id, message_id)

    @router.post("/api/orchards/{orchard_id}/questions", dependencies=protected)
    async def question(orchard_id: str, body: QuestionInput):
        return await _call(monitor.ask, orchard_id, body.question, request_id=body.request_id)

    @router.get("/api/orchards/{orchard_id}/sms-opt-in")
    async def subscription(orchard_id: str):
        def read():
            monitor.store.orchard(orchard_id)
            return monitor.store.get("sms-opt-in:" + orchard_id, {"enabled": False, "channel": "sms"})
        return await _call(read)

    @router.put("/api/orchards/{orchard_id}/sms-opt-in", dependencies=protected)
    async def preference(orchard_id: str, body: SubscriptionInput):
        return await _call(monitor.set_sms_opt_in, orchard_id, body.enabled)

    @router.get("/api/monitor/status")
    async def worker():
        return await _call(lambda: {"worker": monitor.worker_status(), "transport": "local_inbox",
                                   "external_dispatch_configured": monitor.delivery is not None})

    @router.post("/api/monitor/check", dependencies=protected)
    async def check():
        return await _call(monitor.check)

    return router


def create_app(monitor=None, *, location_service=None, web_root: str | Path | None = None, mutation_guard=None):
    """Compatibility factory using the canonical guarded HTTP app, with no loop."""
    if monitor is None:
        from .source import LiveSource
        monitor = Monitor(source=LiveSource())
    from ..server import create_app as create_server
    app = create_server(service=location_service, sms_dispatcher=monitor.delivery,
                        farm_monitor=monitor, web_root=web_root, mutation_guard=mutation_guard)
    app.state.monitor = monitor

    return app
