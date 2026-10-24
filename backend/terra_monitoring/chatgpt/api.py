"""Local, session-guarded ChatGPT controls. OAuth credentials never reach the UI."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, parse_qs

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

USAGE_URL = "https://chatgpt.com/settings/usage"
STATES = {"disconnected", "connecting", "connected", "plan_disabled", "reauth_required", "error"}
ID = r"^[A-Za-z0-9_.:-]{1,160}$"


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Connect(Input):
    account_id: str | None = Field(default=None, pattern=ID)
    reconsent: bool = False


class Account(Input):
    account_id: str = Field(pattern=ID)


class Disconnect(Input):
    account_id: str | None = Field(default=None, pattern=ID)


class Cancel(Input):
    attempt_id: str | None = Field(default=None, pattern=ID)


class Model(Input):
    model: str = Field(min_length=1, max_length=160)


def _response(payload, status=200):
    return Response(json.dumps(payload, ensure_ascii=False, allow_nan=False), status_code=status,
                    media_type="application/json", headers={"Cache-Control": "no-store"})


def _code(value):
    return value if isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,95}", value) else None


def _timestamp(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        stamp = datetime.fromtimestamp(value, timezone.utc)
    elif isinstance(value, str):
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("An aware expiry is required")
    else:
        raise ValueError("Invalid expiry")
    return stamp.astimezone(timezone.utc).isoformat()


def _failure(exc):
    code = _code(getattr(exc, "code", None)) or "chatgpt_unavailable"
    status = getattr(exc, "status", None)
    if not isinstance(status, int) or isinstance(status, bool) or status not in {400, 401, 403, 404, 409, 422, 429, 503}:
        status = 422 if isinstance(exc, ValueError) else 409 if code in {
            "account_not_found", "attempt_not_found", "reauth_required", "plan_not_enabled",
            "model_not_selected", "model_selection_required", "not_connected"} else 503
    message = {
        "plan_not_enabled": "Sign in and allow ChatGPT plan usage to write notes.",
        "reauth_required": "Reconnect your ChatGPT account to write notes.",
        "model_not_selected": "Choose a model available to your ChatGPT account.",
        "model_selection_required": "Choose a model available to your ChatGPT account.",
        "subscription_sharing_usage_limit_exceeded": "Review your plan or app limit in ChatGPT usage settings.",
        "subscription_sharing_user_not_eligible": "ChatGPT plan usage is unavailable for this account or workspace.",
        "subscription_sharing_unsupported_capability": "This request is not supported by your ChatGPT plan connection.",
    }.get(code, "The ChatGPT connection could not complete this request. Your saved notes are unchanged.")
    error = {"code": code, "message": message}
    request_id = getattr(exc, "request_id", None)
    if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", request_id):
        error["request_id"] = request_id
    return _response({"error": error, "usage_url": USAGE_URL}, status)


def connection_status(auth, provider):
    """Whitelist public fields even if an internal implementation later expands."""
    raw = auth.status()
    result = {"schema_version": "terra-chatgpt-connection-v1",
              "state": raw.get("state") if raw.get("state") in STATES else "error",
              "plan_enabled": raw.get("plan_enabled") is True,
              "active_account_id": raw.get("active_account_id"), "accounts": [],
              "usage_url": USAGE_URL}
    for account in raw.get("accounts", []):
        public = {k: account[k] for k in ("label", "email", "state", "plan_enabled", "revocation_unconfirmed") if k in account}
        public["id"] = account.get("id", account.get("account_id"))
        result["accounts"].append(public)
    attempt = raw.get("attempt")
    if isinstance(attempt, dict):
        result["attempt"] = {k: attempt[k] for k in ("attempt_id", "state", "expires_at", "error") if k in attempt}
        result["attempt"]["id"] = attempt.get("attempt_id")
        if "expires_at" in attempt:
            result["attempt"]["expires_at"] = _timestamp(attempt["expires_at"])
    writer = provider.status()
    result["writer"] = {k: writer[k] for k in ("connected", "active_account_id", "selected_model", "usage_paused", "cooldown_until") if k in writer}
    result["writer"]["connected"] = result["plan_enabled"] and writer.get("connected") is True
    result["writer"]["usage_url"] = USAGE_URL
    return result


def create_router(auth, provider, *, mutation_guard=None, preview_only=False):
    async def deny():
        raise HTTPException(status_code=403, detail="Local session authorization required")

    router = APIRouter(dependencies=[Depends(mutation_guard or deny)])

    async def invoke(operation, *args, **kwargs):
        try:
            return _response(await run_in_threadpool(operation, *args, **kwargs))
        except Exception as exc:
            return _failure(exc)

    def status():
        return {**connection_status(auth, provider), "preview_only": preview_only}

    @router.get("/api/chatgpt/status")
    async def get_status():
        return await invoke(status)

    @router.post("/api/chatgpt/connect")
    async def connect(body: Connect):
        def start():
            result = auth.start(account_id=body.account_id, reconsent=body.reconsent)
            address = urlsplit(result.get("authorization_url", ""))
            params = parse_qs(address.query)
            if (address.scheme != "https" or address.netloc != "auth.openai.com"
                    or address.path != "/api/accounts/authorize" or address.fragment
                    or {"access_token", "refresh_token", "id_token_hint"} & params.keys()):
                auth.cancel(result.get("attempt_id"))
                raise ValueError("Invalid authorization destination")
            public = {k: result[k] for k in ("authorization_url", "attempt_id") if k in result}
            public["expires_at"] = _timestamp(result.get("expires_at"))
            return public
        return await invoke(start)

    @router.post("/api/chatgpt/cancel")
    async def cancel(body: Cancel):
        def stop():
            auth.cancel(body.attempt_id)
            return status()
        return await invoke(stop)

    @router.post("/api/chatgpt/select-account")
    async def select(body: Account):
        def choose():
            auth.select_account(body.account_id)
            return status()
        return await invoke(choose)

    @router.post("/api/chatgpt/disconnect")
    async def disconnect(body: Disconnect):
        def sign_out():
            auth.sign_out(body.account_id)
            return status()
        return await invoke(sign_out)

    @router.get("/api/chatgpt/models")
    async def models(refresh: bool = False):
        def catalog():
            return {"models": provider.models(refresh=refresh), "selected_model": provider.status().get("selected_model")}
        return await invoke(catalog)

    @router.put("/api/chatgpt/model")
    async def model(body: Model):
        def choose_model():
            provider.select_model(body.model)
            return status()
        return await invoke(choose_model)

    @router.post("/api/chatgpt/resume")
    async def resume(body: Input):
        def clear_pause():
            provider.resume()
            return status()
        return await invoke(clear_pause)

    return router
