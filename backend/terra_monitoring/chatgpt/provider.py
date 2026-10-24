"""Account-specific ChatGPT plan catalog and bounded Responses/SSE inference.

Auth owns consent and tokens. The caller owns note identity, factual validation,
and whether a new request is warranted. This module never retries inference.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import httpx

MODELS_URL = "https://api.openai.com/v1/models"
RESPONSES_URL = "https://api.openai.com/v1/responses"
USAGE_URL = "https://chatgpt.com/settings/usage"
MAX_CATALOG_BYTES = 256_000
MAX_MODELS = 256
MAX_STREAM_BYTES = 256_000
MAX_STREAM_EVENTS = 1_024
MAX_STREAM_SECONDS = 45
MAX_OUTPUT_CHARS = 16_000
MAX_INPUT_BYTES = 64_000
MAX_STATE_BYTES = 64_000
MAX_ACCOUNTS = 64
_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
_MACHINE = re.compile(r"[a-zA-Z][a-zA-Z0-9_.\[\]-]{0,127}\Z")
_REQUEST_ID = re.compile(r"[a-zA-Z0-9_-]{1,128}\Z")
_SHAPE_KEYS = {"error", "detail", "message", "code", "param", "type", "status", "response",
               "incomplete_details", "reason", "request_id"}


def _machine(value):
    return value if isinstance(value, str) and _MACHINE.fullmatch(value) else None


def _request_id(value):
    return value if isinstance(value, str) and _REQUEST_ID.fullmatch(value) else None


def _shape(value, depth=0):
    """Retain structure, never provider prose, values, or arbitrary object keys."""
    if depth >= 4:
        return "nested"
    if isinstance(value, dict):
        return {key if key in _SHAPE_KEYS else "<other>": _shape(item, depth + 1)
                for key, item in list(value.items())[:20]}
    if isinstance(value, list):
        return ["array", *[_shape(item, depth + 1) for item in value[:3]]]
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    return "string" if isinstance(value, str) else "unknown"


class PlanError(Exception):
    """Safe diagnostic suitable for a local UI; contains no provider error prose."""

    def __init__(self, code, *, status=None, param=None, request_id=None, error_shape=None):
        self.code = _machine(code) or "provider_error"
        self.status = status if isinstance(status, int) and not isinstance(status, bool) else None
        self.param = _machine(param)
        self.request_id = _request_id(request_id)
        self.error_shape = error_shape
        super().__init__(self.code)

    def to_dict(self):
        return {"code": self.code, "status": self.status, "param": self.param,
                "request_id": self.request_id, "error_shape": self.error_shape}


class ChatGPTPlanClient:
    """Synchronous local provider; ``auth.access_token()`` must enforce consent.

    ``state_dir`` is an explicit absolute application state directory. Only model
    selections and pause/backoff state are stored, under hashed account IDs.
    A supplied HTTPX client is for tests; all requests still use fixed public URLs,
    disable redirects, and explicitly set the bearer from the auth manager.
    """

    def __init__(self, auth, state_dir, client=None, clock=None):
        self.auth = auth
        self.state_dir = Path(state_dir)
        if not self.state_dir.is_absolute():
            raise ValueError("ChatGPT provider state_dir must be absolute")
        self.client = client
        self.clock = clock or time.time
        self._path = self.state_dir / "provider.json"
        self._lock = threading.RLock()
        self._catalog = {}
        self._last_account = None

    def _now(self):
        value = self.clock()
        return value.timestamp() if isinstance(value, datetime) else float(value)

    def _account_id(self):
        try:
            status = self.auth.status()
        except Exception as exc:
            raise self._auth_error(exc) from None
        account = status.get("active_account_id") if isinstance(status, dict) else None
        if not isinstance(account, str) or not account or len(account) > 256:
            raise PlanError("not_connected")
        return account

    @staticmethod
    def _auth_error(exc):
        return PlanError(_machine(getattr(exc, "code", None)) or "auth_unavailable",
                         status=getattr(exc, "status", None),
                         request_id=getattr(exc, "request_id", None))

    def _context(self):
        account = self._account_id()
        try:
            token = self.auth.access_token()
        except Exception as exc:
            raise self._auth_error(exc) from None
        if not isinstance(token, str) or not token or "\n" in token or "\r" in token:
            raise PlanError("not_connected")
        if self._account_id() != account:
            raise PlanError("account_changed")
        if account != self._last_account:
            # Account switches require a fresh server catalog, including a switch back.
            self._catalog.clear()
            self._last_account = account
        return account, {"Authorization": "Bearer " + token, "Accept": "application/json"}

    @staticmethod
    def _key(account):
        return hashlib.sha256(account.encode("utf-8")).hexdigest()

    def _read_state(self):
        if not self._path.exists():
            return {"version": 1, "accounts": {}}
        try:
            if self._path.stat().st_size > MAX_STATE_BYTES:
                raise ValueError
            state = json.loads(self._path.read_text(encoding="utf-8"))
            accounts = state["accounts"]
            if state["version"] != 1 or not isinstance(accounts, dict) or len(accounts) > MAX_ACCOUNTS:
                raise ValueError
            clean = {}
            for key, entry in accounts.items():
                if not re.fullmatch(r"[0-9a-f]{64}", key) or not isinstance(entry, dict):
                    raise ValueError
                model = entry.get("selected_model")
                until, failures = entry.get("cooldown_until", 0), entry.get("failures", 0)
                if model is not None and (not isinstance(model, str) or not _SLUG.fullmatch(model)):
                    raise ValueError
                if (not isinstance(until, (int, float)) or isinstance(until, bool) or not math.isfinite(until)
                        or not isinstance(failures, int) or isinstance(failures, bool) or not 0 <= failures <= 6
                        or not isinstance(entry.get("usage_paused", False), bool)):
                    raise ValueError
                clean[key] = {"selected_model": model, "cooldown_until": until,
                              "failures": failures, "usage_paused": entry.get("usage_paused", False)}
            return {"version": 1, "accounts": clean}
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            # Do not silently lose a persisted usage pause if state is damaged.
            raise PlanError("provider_state_invalid") from None

    def _entry(self, state, account):
        key = self._key(account)
        if key not in state["accounts"]:
            if len(state["accounts"]) >= MAX_ACCOUNTS:
                raise PlanError("provider_state_limit")
            state["accounts"][key] = {"selected_model": None, "usage_paused": False,
                                      "cooldown_until": 0, "failures": 0}
        return state["accounts"][key]

    def _save(self, state):
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(".tmp")
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(state, handle, allow_nan=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self._path)
        except (OSError, ValueError):
            raise PlanError("provider_state_unavailable") from None

    @contextmanager
    def _http(self):
        if self.client is not None:
            yield self.client
        else:
            with httpx.Client(timeout=httpx.Timeout(10), follow_redirects=False, trust_env=False) as client:
                yield client

    def _cooldown(self, account, error):
        temporary = error.code in {"subscription_sharing_usage_unavailable", "subscription_sharing_user_unavailable"}
        if error.code != "subscription_sharing_usage_limit_exceeded" and error.status != 503 and not temporary:
            return
        state = self._read_state()
        entry = self._entry(state, account)
        if error.code == "subscription_sharing_usage_limit_exceeded":
            entry["usage_paused"] = True
        else:
            entry["failures"] = min(entry["failures"] + 1, 6)
            entry["cooldown_until"] = self._now() + min(300, 10 * 2 ** (entry["failures"] - 1))
        self._save(state)

    def _admission(self, response, body):
        error = body.get("error") if isinstance(body, dict) else None
        return PlanError((_machine(error.get("code")) if isinstance(error, dict) else None) or "http_error",
                         status=response.status_code,
                         param=error.get("param") if isinstance(error, dict) else None,
                         request_id=response.headers.get("x-request-id"), error_shape=_shape(body))

    def _bounded_body(self, response, limit):
        data = bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > limit:
                raise PlanError("response_limit_exceeded", status=response.status_code,
                                request_id=response.headers.get("x-request-id"))
        try:
            return json.loads(data)
        except (ValueError, UnicodeError, RecursionError):
            # Preserve that admission returned text, without retaining its prose.
            return ""

    @staticmethod
    def _response_diagnostic(error, response):
        if response is not None:
            if error.status is None:
                error.status = response.status_code
            if error.request_id is None:
                error.request_id = _request_id(response.headers.get("x-request-id"))
        return error

    def models(self, refresh=False):
        """Return only documented display fields, in server order."""
        with self._lock:
            account, headers = self._context()
            if not refresh and account in self._catalog:
                return [dict(model) for model in self._catalog[account]]
            response = None
            try:
                with self._http() as client, client.stream("GET", MODELS_URL, headers=headers,
                                                         follow_redirects=False, timeout=10) as response:
                    body = self._bounded_body(response, MAX_CATALOG_BYTES)
                    if not response.is_success:
                        raise self._admission(response, body)
                    rows = body.get("models") if isinstance(body, dict) else None
                    if not isinstance(rows, list) or len(rows) > MAX_MODELS:
                        raise PlanError("invalid_model_catalog", request_id=response.headers.get("x-request-id"))
                    catalog, seen = [], set()
                    for row in rows:
                        if not isinstance(row, dict):
                            raise PlanError("invalid_model_catalog")
                        if row.get("visibility") != "list":
                            continue
                        slug, name = row.get("slug"), row.get("display_name")
                        if (not isinstance(slug, str) or not _SLUG.fullmatch(slug) or slug in seen
                                or not isinstance(name, str) or not name.strip() or len(name) > 200
                                or any(ord(char) < 32 for char in name)):
                            raise PlanError("invalid_model_catalog")
                        seen.add(slug)
                        catalog.append({"slug": slug, "display_name": name})
                    if self._account_id() != account:
                        raise PlanError("account_changed")
            except httpx.HTTPError:
                raise self._response_diagnostic(PlanError("network_error"), response) from None
            except PlanError as error:
                self._response_diagnostic(error, response)
                self._cooldown(account, error)
                raise
            self._catalog[account] = catalog
            state = self._read_state()
            entry = self._entry(state, account)
            if entry["selected_model"] and entry["selected_model"] not in seen:
                entry["selected_model"] = None
                self._save(state)
            return [dict(model) for model in catalog]

    def select_model(self, slug):
        with self._lock:
            if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
                raise PlanError("model_unavailable")
            previous_account = self._account_id()
            catalog = self.models()
            account = self._account_id()
            if account != previous_account:
                raise PlanError("account_changed")
            if slug not in {row["slug"] for row in catalog}:
                raise PlanError("model_unavailable")
            state = self._read_state()
            self._entry(state, account)["selected_model"] = slug
            self._save(state)
            return self.status()

    def status(self):
        """Local redacted status; no token refresh or network request."""
        with self._lock:
            try:
                account = self._account_id()
            except PlanError as error:
                return {"connected": False, "active_account_id": None, "selected_model": None,
                        "usage_paused": False, "cooldown_until": None, "usage_url": USAGE_URL,
                        "error": error.to_dict()}
            entry = self._entry(self._read_state(), account)
            return {"connected": True, "active_account_id": account,
                    "selected_model": entry["selected_model"], "usage_paused": entry["usage_paused"],
                    "cooldown_until": entry["cooldown_until"] if entry["cooldown_until"] > self._now() else None,
                    "usage_url": USAGE_URL}

    def resume(self):
        """Explicit user action to permit another attempt after a usage pause."""
        with self._lock:
            account = self._account_id()
            state = self._read_state()
            entry = self._entry(state, account)
            entry.update(usage_paused=False, cooldown_until=0, failures=0)
            self._save(state)
            return self.status()

    def generate(self, instructions, packet, *, schema=None):
        """Make at most one POST. Return text only after a completed SSE response.

        ``schema`` is an optional JSON Schema object, wrapped in documented
        ``text.format`` configuration. Factual/schema validation belongs to caller.
        """
        with self._lock:
            try:
                packet_json = json.dumps(packet, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                if not isinstance(instructions, str) or not instructions.strip():
                    raise ValueError
                if len((packet_json + instructions).encode("utf-8")) > MAX_INPUT_BYTES:
                    raise ValueError
                if schema is not None and (not isinstance(schema, dict)
                        or len(json.dumps(schema, allow_nan=False).encode("utf-8")) > MAX_INPUT_BYTES):
                    raise ValueError
            except (ValueError, TypeError, UnicodeError, RecursionError):
                raise PlanError("invalid_input") from None
            account = self._account_id()
            state = self._read_state()
            entry = self._entry(state, account)
            if entry["usage_paused"]:
                raise PlanError("usage_paused")
            if entry["cooldown_until"] > self._now():
                raise PlanError("provider_cooldown")
            # Refresh once per process/account switch before using a persisted selection.
            catalog = self.models()
            model = entry["selected_model"]
            if model is None or model not in {row["slug"] for row in catalog}:
                raise PlanError("model_selection_required")
            current_account, headers = self._context()
            if current_account != account:
                raise PlanError("account_changed")
            body = {"model": model, "instructions": instructions,
                    "input": [{"role": "user", "content": packet_json}], "store": False, "stream": True}
            if schema is not None:
                body["text"] = {"format": {"type": "json_schema", "name": "terra_grower_note",
                                             "strict": True, "schema": schema}}
            headers["Accept"] = "text/event-stream"
            response = None
            try:
                with self._http() as client, client.stream("POST", RESPONSES_URL, headers=headers, json=body,
                                                         follow_redirects=False, timeout=10) as response:
                    if not response.is_success:
                        raise self._admission(response, self._bounded_body(response, MAX_CATALOG_BYTES))
                    result = self._stream(response, model)
            except httpx.HTTPError:
                raise self._response_diagnostic(PlanError("stream_interrupted"), response) from None
            except PlanError as error:
                self._response_diagnostic(error, response)
                self._cooldown(account, error)
                raise
            entry.update(failures=0, cooldown_until=0)
            self._save(state)
            return result

    def _stream(self, response, model):
        request_id = _request_id(response.headers.get("x-request-id"))
        if response.headers.get("content-type", "").split(";", 1)[0].strip() != "text/event-stream":
            raise PlanError("invalid_stream", status=response.status_code, request_id=request_id)
        started, count, deltas, done, delta_chars = self._now(), 0, {}, {}, 0
        for event in self._events(response, started, request_id):
            count += 1
            if count > MAX_STREAM_EVENTS:
                raise PlanError("stream_limit_exceeded", request_id=request_id)
            kind = event.get("type")
            if kind == "response.output_text.delta":
                delta = event.get("delta")
                key = (event.get("output_index"), event.get("content_index"))
                if (not isinstance(delta, str) or not all(isinstance(index, int) and not isinstance(index, bool)
                                                         and 0 <= index < 64 for index in key)):
                    raise PlanError("invalid_stream", request_id=request_id)
                delta_chars += len(delta)
                if delta_chars > MAX_OUTPUT_CHARS:
                    raise PlanError("output_limit_exceeded", request_id=request_id)
                deltas[key] = deltas.get(key, "") + delta
            elif kind == "response.output_text.done":
                key = (event.get("output_index"), event.get("content_index"))
                text = event.get("text")
                if (not isinstance(text, str) or not all(isinstance(index, int) and not isinstance(index, bool)
                                                        and 0 <= index < 64 for index in key)
                        or len(text) > MAX_OUTPUT_CHARS):
                    raise PlanError("invalid_stream", request_id=request_id)
                done[key] = text
            elif kind in {"response.failed", "response.incomplete", "error"}:
                nested = event.get("response")
                error = nested.get("error") if isinstance(nested, dict) else event.get("error", event)
                fallback = "response_incomplete" if kind == "response.incomplete" else "response_failed"
                code = _machine(error.get("code")) if isinstance(error, dict) else None
                raise PlanError(code or fallback, status=response.status_code,
                                param=error.get("param") if isinstance(error, dict) else None,
                                request_id=request_id, error_shape=_shape(event))
            elif kind == "response.completed":
                completed = event.get("response")
                if not isinstance(completed, dict) or completed.get("status") != "completed":
                    raise PlanError("invalid_stream", request_id=request_id)
                text = self._completed_text(completed, deltas, done, request_id)
                return {"text": text, "model": model, "request_id": request_id,
                        "usage": self._usage(completed.get("usage"))}
        raise PlanError("stream_interrupted", request_id=request_id)

    def _events(self, response, started, request_id):
        import codecs

        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        buffer, data, event_name, total = "", [], None, 0
        try:
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_STREAM_BYTES or self._now() - started > MAX_STREAM_SECONDS:
                    raise PlanError("stream_limit_exceeded", request_id=request_id)
                buffer += decoder.decode(chunk)
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.removesuffix("\r")
                    if not line:
                        if data:
                            payload = "\n".join(data)
                            if payload == "[DONE]":
                                return
                            parsed = json.loads(payload)
                            if not isinstance(parsed, dict) or not isinstance(parsed.get("type"), str):
                                raise ValueError
                            if event_name and event_name != parsed["type"]:
                                raise ValueError
                            yield parsed
                        data, event_name = [], None
                    elif line.startswith("data:"):
                        data.append(line[5:].removeprefix(" "))
                    elif line.startswith("event:"):
                        event_name = line[6:].strip()
            decoder.decode(b"", final=True)
        except (ValueError, UnicodeError, RecursionError):
            raise PlanError("invalid_stream", request_id=request_id) from None

    @staticmethod
    def _completed_text(completed, deltas, done, request_id):
        parts = []
        if "output" in completed:
            output = completed["output"]
            if not isinstance(output, list):
                raise PlanError("invalid_stream", request_id=request_id)
            for item in output:
                if not isinstance(item, dict):
                    raise PlanError("invalid_stream", request_id=request_id)
                if item.get("type") != "message":
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    raise PlanError("invalid_stream", request_id=request_id)
                for part in content:
                    if not isinstance(part, dict):
                        raise PlanError("invalid_stream", request_id=request_id)
                    if part.get("type") == "output_text":
                        if not isinstance(part.get("text"), str):
                            raise PlanError("invalid_stream", request_id=request_id)
                        parts.append(part["text"])
            # An explicit empty output is authoritative, including refusals.
            text = "".join(parts)
        else:
            if not deltas or any(done.get(key) != value for key, value in deltas.items()):
                raise PlanError("invalid_stream", request_id=request_id)
            text = "".join(deltas[key] for key in sorted(deltas))
        if not text.strip():
            raise PlanError("empty_output", request_id=request_id)
        if len(text) > MAX_OUTPUT_CHARS:
            raise PlanError("output_limit_exceeded", request_id=request_id)
        return text

    @staticmethod
    def _usage(usage):
        if not isinstance(usage, dict):
            return {}
        return {key: value for key in ("input_tokens", "output_tokens", "total_tokens")
                if isinstance(value := usage.get(key), int) and not isinstance(value, bool) and 0 <= value <= 10**9}
