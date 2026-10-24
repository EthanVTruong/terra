"""Guarded local conversation endpoints; no endpoint triggers weather acquisition."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from ..server import _error, _json_response
from .service import RequestAlreadyCompleted, RequestInProgress, SetupChanged
from .location import LocationLookupError


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class MessageInput(StrictBody):
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    text: str = Field(min_length=1, max_length=1000)
    orchard_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    reply_to: str | None = Field(default=None, min_length=1, max_length=120)


class LocationInput(StrictBody):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    elevation_ft: float = Field(ge=-1500, le=20000)
    name: str = Field(default="My farm", min_length=1, max_length=80)
    source: Literal["device", "manual", "preset"]
    confirmed: bool


class NotificationInput(StrictBody):
    enabled: bool
    permission: Literal["granted", "denied", "default", "unsupported"]


class DeviceLocationInput(StrictBody):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0)


class SetupInput(StrictBody):
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    action: Literal["restart", "location", "notifications", "device_location"]
    orchard_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    location: LocationInput | None = None
    notifications: NotificationInput | None = None
    device_location: DeviceLocationInput | None = None


class ReconcileInput(StrictBody):
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    target_request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    confirm_uncertain: bool
    orchard_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")


async def _call(operation, **kwargs):
    try:
        return _json_response(await run_in_threadpool(operation, **kwargs))
    except RequestAlreadyCompleted:
        return _error("request_already_completed", "This request already has a saved outcome. Refresh to read it.", 409)
    except SetupChanged:
        return _error("setup_changed", "Setup changed while location was loading. Refresh to continue.", 409)
    except LocationLookupError:
        return _error("location_lookup_failed", "Couldn’t get your location details. Try again.", 503, retryable=True)
    except RequestInProgress:
        return _error("request_in_progress", "This request is pending. Refresh its status; don't send it as a new request.", 409)
    except KeyError:
        return _error("not_found", "Choose an existing farm or message.", 404)
    except ValueError:
        return _error("invalid_request", "Check the farm, confirmed setup choice or request identifier.", 422)
    except Exception:
        return _error("conversation_unavailable", "Your saved conversation is retained. Try refreshing it.", 503)


def create_router(service, *, mutation_guard=None):
    router = APIRouter()

    async def unavailable_guard():
        raise HTTPException(status_code=403, detail="Conversation mutations require the local-session guard")

    protected = [Depends(mutation_guard or unavailable_guard)]

    @router.get("/api/tara/conversation")
    async def conversation(orchard_id: str | None = None):
        return await _call(service.state, orchard_id=orchard_id)

    @router.post("/api/tara/messages", dependencies=protected)
    async def message(body: MessageInput):
        return await _call(service.message, **body.model_dump())

    @router.post("/api/tara/setup", dependencies=protected)
    async def setup(body: SetupInput):
        return await _call(service.action, **body.model_dump())

    @router.post("/api/tara/reconcile", dependencies=protected)
    async def reconcile(body: ReconcileInput):
        return await _call(service.reconcile, **body.model_dump())

    return router
