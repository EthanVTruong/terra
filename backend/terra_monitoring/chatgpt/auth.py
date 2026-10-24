"""Public-client OAuth for the documented open-source ChatGPT plan flow.

All credentials belong to Terra's own vault. Public status and errors contain no
tokens, authorization codes, callback URLs, nonces, or upstream response bodies.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit
import uuid

import httpx
import jwt

from .vault import Vault, VaultError


ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = ISSUER + "/api/accounts/authorize"
TOKEN_URL = ISSUER + "/api/accounts/oauth/token"
JWKS_URL = ISSUER + "/.well-known/jwks.json"
REVOCATION_URL = ISSUER + "/api/accounts/oauth/revoke"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
_TERMINAL_REFRESH = {"invalid_grant", "invalid_refresh_token", "token_expired",
                     "refresh_token_expired", "refresh_token_invalidated", "refresh_token_reused"}
_SAFE_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_CALLBACK_HTML = b"<!doctype html><meta charset=utf-8><title>Terra</title><p>Return to Terra to view your ChatGPT connection.</p>"


class AuthError(Exception):
    """A fixed diagnostic code and optional sanitized HTTP metadata."""

    def __init__(self, code: str, status: int | None = None, request_id: str | None = None):
        self.code = code if isinstance(code, str) and _SAFE_ID.fullmatch(code) else "authentication_failed"
        self.status = status if isinstance(status, int) and 100 <= status <= 599 else None
        self.request_id = request_id if isinstance(request_id, str) and _SAFE_ID.fullmatch(request_id) else None
        super().__init__(self.code)


class _CallbackServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False

    def handle_error(self, request, client_address):
        # BaseServer otherwise prints an exception and callback request context.
        pass


class AuthManager:
    def __init__(self, state_dir: str | Path, client=None, clock=None, callback_timeout=300):
        self._clock = clock or time.time
        self._client = client or httpx.Client(timeout=10, follow_redirects=False)
        self._owns_client = client is None
        self._lock = threading.RLock()
        self._pending = None
        self._attempt = None
        self._server = None
        self._timer = None
        self._closed = False
        self._jwks = None
        self._jwks_saved = 0
        self._callback_timeout = min(600.0, max(0.05, float(callback_timeout)))
        try:
            self._vault = Vault(state_dir)
            with self._storage():
                data = self._vault.load()
                if not data.get("ext_agent_host_id"):
                    data["ext_agent_host_id"] = "urn:uuid:" + str(uuid.uuid4())
                    self._vault.save(data)
        except VaultError as exc:
            raise AuthError(str(exc)) from None

    @contextmanager
    def _storage(self):
        try:
            with self._vault.locked():
                yield
        except VaultError as exc:
            raise AuthError(str(exc)) from None

    @staticmethod
    def _account_state(account):
        if not account.get("access_token"):
            return "reauth_required"
        return "connected" if PLAN_SCOPE in account.get("scopes", []) else "plan_disabled"

    def status(self) -> dict:
        with self._lock, self._storage():
            data = self._vault.load()
            accounts = []
            for account_id, account in data["accounts"].items():
                state = self._account_state(account)
                accounts.append({"account_id": account_id, "email": account.get("email"),
                                 "label": account["label"], "state": state,
                                 "plan_enabled": state == "connected",
                                 "revocation_unconfirmed": bool(account.get("revocation_unconfirmed"))})
            active = data["accounts"].get(data.get("active_account_id"))
            state = self._account_state(active) if active else "disconnected"
            if self._pending:
                state = "connecting"
            elif not active and self._attempt and self._attempt["state"] == "error":
                state = "error"
            return {"schema": "terra-chatgpt-connection-v1", "state": state,
                    "plan_enabled": bool(active and self._account_state(active) == "connected"),
                    "active_account_id": data.get("active_account_id"), "accounts": accounts,
                    "attempt": dict(self._attempt) if self._attempt else None}

    def start(self, account_id=None, reconsent=False) -> dict:
        with self._lock:
            if self._closed:
                raise AuthError("manager_closed")
            self._stop_attempt("cancelled")
            with self._storage():
                data = self._vault.load()
                account = data["accounts"].get(account_id) if account_id is not None else None
                if account_id is not None and account is None:
                    raise AuthError("account_not_found")
                issued = account["client_id"] if account else data.get("pending_client_id")
                client_id = issued or "dynamic_agent_client"
                pending = {"attempt_id": secrets.token_urlsafe(24), "state": secrets.token_urlsafe(32),
                           "nonce": secrets.token_urlsafe(32), "verifier": secrets.token_urlsafe(64),
                           "expires_at": self._clock() + self._callback_timeout,
                           "account_id": account_id, "client_id": client_id}
                manager = self

                class Handler(BaseHTTPRequestHandler):
                    def setup(self):
                        super().setup()
                        self.connection.settimeout(2)

                    def log_message(self, *args):
                        pass

                    def do_GET(self):
                        self.connection.settimeout(2)
                        status = 400
                        try:
                            expected_host = f"127.0.0.1:{self.server.server_port}"
                            if (len(self.path) > 8192 or self.headers.get_all("Host") != [expected_host]
                                    or self.headers.get("Transfer-Encoding")
                                    or self.headers.get_all("Content-Length", ["0"]) != ["0"]):
                                raise AuthError("invalid_callback")
                            parsed = urlsplit(self.path)
                            if parsed.scheme or parsed.netloc or parsed.path != "/auth/callback" or parsed.fragment:
                                raise AuthError("invalid_callback")
                            values = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=12)
                            if any(len(items) != 1 for items in values.values()):
                                raise AuthError("invalid_callback")
                            manager._callback(pending["attempt_id"], {key: items[0] for key, items in values.items()})
                            status = 200
                        except (AuthError, ValueError):
                            pass
                        self.send_response(status)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.send_header("Content-Length", str(len(_CALLBACK_HTML)))
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Referrer-Policy", "no-referrer")
                        self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
                        self.end_headers()
                        self.wfile.write(_CALLBACK_HTML)

                try:
                    server = _CallbackServer(("127.0.0.1", 0), Handler)
                except OSError:
                    raise AuthError("callback_listener_failed") from None
                pending["redirect_uri"] = f"http://127.0.0.1:{server.server_port}/auth/callback"
                self._pending = pending
                self._attempt = {"attempt_id": pending["attempt_id"], "state": "pending",
                                 "expires_at": pending["expires_at"], "error": None}
                self._server = server
                threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
                self._timer = threading.Timer(self._callback_timeout, self._expire, args=(pending["attempt_id"],))
                self._timer.daemon = True
                self._timer.start()
                parameters = {"client_id": client_id, "ext_agent_host_id": data["ext_agent_host_id"],
                              "response_type": "code", "redirect_uri": pending["redirect_uri"],
                              "scope": SCOPES, "resource": RESOURCE, "state": pending["state"],
                              "nonce": pending["nonce"], "code_challenge_method": "S256",
                              "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(pending["verifier"].encode()).digest()).rstrip(b"=").decode()}
                if client_id == "dynamic_agent_client":
                    parameters["agent_name_hint"] = "Terra"
                if reconsent:
                    parameters["prompt"] = "consent"
                return {"authorization_url": AUTHORIZE_URL + "?" + urlencode(parameters),
                        "attempt_id": pending["attempt_id"], "expires_at": pending["expires_at"]}

    def _stop_attempt(self, state, error=None):
        self._pending = None
        if self._attempt and self._attempt["state"] == "pending":
            self._attempt.update(state=state, error=error)
        if self._timer:
            self._timer.cancel()
            self._timer = None
        if self._server:
            server, self._server = self._server, None
            server.shutdown()
            server.server_close()

    def _expire(self, attempt_id):
        with self._lock:
            if self._pending and self._pending["attempt_id"] == attempt_id:
                self._stop_attempt("timed_out", "authorization_timeout")

    def cancel(self, attempt_id=None):
        with self._lock:
            if attempt_id is not None and (not self._pending or self._pending["attempt_id"] != attempt_id):
                raise AuthError("attempt_not_found")
            self._stop_attempt("cancelled")
        return self.status()

    def _callback(self, attempt_id, params):
        with self._lock:
            pending = self._pending
            if not pending or pending["attempt_id"] != attempt_id:
                raise AuthError("attempt_not_found")
            if self._clock() >= pending["expires_at"]:
                self._stop_attempt("timed_out", "authorization_timeout")
                raise AuthError("authorization_timeout")
            if not secrets.compare_digest(params.get("state", "").encode(), pending["state"].encode()):
                raise AuthError("invalid_state")
            # A matching state is consumed exactly once, even if exchange fails.
            self._stop_attempt("processing")
            try:
                if "error" in params:
                    code = "access_denied" if params["error"] == "access_denied" else "authorization_failed"
                    self._attempt.update(state="denied" if code == "access_denied" else "error", error=code)
                    return
                issued = params.get("client_id", pending["client_id"])
                if not _SAFE_ID.fullmatch(issued) or issued == "dynamic_agent_client":
                    raise AuthError("registration_incomplete")
                if pending["client_id"] != "dynamic_agent_client" and issued != pending["client_id"]:
                    raise AuthError("client_mismatch")
                code = params.get("code", "")
                if not code or len(code) > 4096:
                    raise AuthError("invalid_callback")
                with self._storage():
                    data = self._vault.load()
                    if pending["account_id"] is None:
                        data["pending_client_id"] = issued
                        self._vault.save(data)
                    response = self._request("POST", TOKEN_URL, data={"grant_type": "authorization_code",
                        "client_id": issued, "code": code, "code_verifier": pending["verifier"],
                        "redirect_uri": pending["redirect_uri"], "resource": RESOURCE})
                    claims = self._verify_id_token(response.get("id_token"), issued, pending["nonce"])
                    account_id = hashlib.sha256((issued + "\0" + claims["sub"]).encode()).hexdigest()[:32]
                    if pending["account_id"] is not None:
                        original = data["accounts"].get(pending["account_id"])
                        if not original or original["client_id"] != issued or original["subject"] != claims["sub"]:
                            raise AuthError("account_mismatch")
                        account_id = pending["account_id"]
                    previous = data["accounts"].get(account_id, {})
                    account = {"client_id": issued, "subject": claims["sub"], "issuer": ISSUER,
                               "email": claims.get("email") if isinstance(claims.get("email"), str) and len(claims["email"]) <= 254 else None,
                               "label": previous.get("label", "ChatGPT " + account_id[:8]),
                               "nonce": pending["nonce"], "revocation_unconfirmed": False}
                    account.update(self._tokens(response))
                    data["accounts"][account_id] = account
                    data["active_account_id"] = account_id
                    if data.get("pending_client_id") == issued:
                        data.pop("pending_client_id", None)
                    self._vault.save(data)
                self._attempt.update(state="succeeded", error=None)
            except AuthError as exc:
                self._attempt.update(state="error", error=exc.code)
                raise

    def _request(self, method, url, *, data=None, empty=False):
        try:
            deadline = time.monotonic() + 10
            with self._client.stream(method, url, data=data, timeout=10, follow_redirects=False) as response:
                metadata = {"status": response.status_code, "request_id": response.headers.get("x-request-id")}
                if time.monotonic() >= deadline:
                    raise AuthError("provider_unavailable", **metadata)
                body = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() >= deadline:
                        raise AuthError("provider_unavailable", **metadata)
                    body.extend(chunk)
                    if len(body) > 131072:
                        raise AuthError("invalid_provider_response", **metadata)
                if 300 <= response.status_code < 400:
                    raise AuthError("provider_redirect_rejected", **metadata)
                if empty and response.status_code == 200:
                    return {}
                try:
                    payload = json.loads(body)
                except (ValueError, UnicodeError, RecursionError):
                    raise AuthError("invalid_provider_response", **metadata) from None
                if not isinstance(payload, dict):
                    raise AuthError("invalid_provider_response", **metadata)
                if response.status_code >= 400:
                    error = payload.get("error")
                    code = error.get("code") if isinstance(error, dict) else error
                    allowed = _TERMINAL_REFRESH | {"invalid_client", "access_denied"}
                    raise AuthError(code if isinstance(code, str) and code in allowed else "provider_request_failed", **metadata)
                if response.status_code != 200:
                    raise AuthError("provider_request_failed", **metadata)
                return payload
        except httpx.HTTPError:
            raise AuthError("provider_unavailable") from None

    def _verify_id_token(self, token, client_id, nonce, *, refreshing=False):
        try:
            if not isinstance(token, str) or len(token) > 32768:
                raise ValueError()
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str) or header.get("crit"):
                raise ValueError()
            cached = self._jwks is not None and self._clock() - self._jwks_saved <= 300
            if not cached:
                self._jwks = self._request("GET", JWKS_URL)
                self._jwks_saved = self._clock()

            def matching_keys():
                return [key for key in self._jwks.get("keys", [])
                        if isinstance(key, dict) and key.get("kid") == header["kid"]
                        and key.get("kty") == "RSA" and key.get("use", "sig") == "sig"
                        and key.get("alg", "RS256") == "RS256"]

            candidates = matching_keys()
            if not candidates and cached:
                self._jwks = self._request("GET", JWKS_URL)
                self._jwks_saved = self._clock()
                candidates = matching_keys()
            if len(candidates) != 1:
                raise ValueError()
            key = jwt.PyJWK.from_dict(candidates[0], algorithm="RS256").key
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=client_id, issuer=ISSUER,
                options={"require": ["exp", "iss", "aud", "sub"], "verify_exp": False,
                         "verify_nbf": False, "verify_iat": False})
            audience = claims["aud"]
            if ((isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != client_id)
                    or ("azp" in claims and claims["azp"] != client_id)):
                raise ValueError()
            now = self._clock()
            for field in ("exp", "nbf", "iat"):
                value = claims.get(field)
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                    raise ValueError()
            if claims["exp"] <= now or claims.get("nbf", now) > now or claims.get("iat", now) > now + 60:
                raise ValueError()
            if not isinstance(claims["sub"], str) or not 1 <= len(claims["sub"]) <= 256:
                raise ValueError()
            if not refreshing or "nonce" in claims:
                actual_nonce = claims.get("nonce")
                if not isinstance(actual_nonce, str) or not secrets.compare_digest(actual_nonce, nonce):
                    raise ValueError()
            return claims
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
            raise AuthError("invalid_id_token") from None

    def _tokens(self, response, previous=None):
        previous = previous or {}
        access = response.get("access_token")
        refresh = response.get("refresh_token", previous.get("refresh_token"))
        ttl = response.get("expires_in")
        scope = response.get("scope")
        if (not isinstance(access, str) or not 1 <= len(access) <= 32768
                or str(response.get("token_type", "")).lower() != "bearer"
                or isinstance(ttl, bool) or not isinstance(ttl, (int, float))
                or not math.isfinite(ttl) or not 0 < ttl <= 31536000
                or (refresh is not None and (not isinstance(refresh, str) or not 1 <= len(refresh) <= 32768))
                or (scope is None and not previous) or (scope is not None and not isinstance(scope, str))):
            raise AuthError("invalid_provider_response")
        scopes = sorted(set(scope.split())) if scope is not None else previous.get("scopes", [])
        if len(scopes) > 64 or any(len(value) > 256 for value in scopes):
            raise AuthError("invalid_provider_response")
        return {"access_token": access, "refresh_token": refresh,
                "id_token": response.get("id_token", previous.get("id_token")), "token_type": "Bearer",
                "expires_at": self._clock() + ttl, "saved_at": self._clock(), "scopes": scopes}

    def select_account(self, account_id):
        with self._lock, self._storage():
            data = self._vault.load()
            if account_id not in data["accounts"]:
                raise AuthError("account_not_found")
            data["active_account_id"] = account_id
            self._vault.save(data)
        return self.status()

    @staticmethod
    def _clear_tokens(account):
        for key in ("access_token", "refresh_token", "id_token", "nonce", "expires_at", "saved_at", "token_type"):
            account.pop(key, None)
        account["scopes"] = []

    def access_token(self) -> str:
        """Internal provider boundary only; callers must never expose this value."""
        with self._lock, self._storage():
            data = self._vault.load()
            account = data["accounts"].get(data.get("active_account_id"))
            if not account or not account.get("access_token"):
                raise AuthError("reauth_required")
            if PLAN_SCOPE not in account.get("scopes", []):
                raise AuthError("plan_not_enabled")
            if account.get("expires_at", 0) > self._clock() + 60:
                return account["access_token"]
            if not account.get("refresh_token"):
                self._clear_tokens(account)
                self._vault.save(data)
                raise AuthError("reauth_required")
            try:
                response = self._request("POST", TOKEN_URL, data={"grant_type": "refresh_token",
                    "client_id": account["client_id"], "refresh_token": account["refresh_token"], "resource": RESOURCE})
                if "id_token" in response:
                    claims = self._verify_id_token(response["id_token"], account["client_id"], account["nonce"], refreshing=True)
                    if claims["sub"] != account["subject"]:
                        raise AuthError("account_mismatch")
                account.update(self._tokens(response, account))
                self._vault.save(data)
            except AuthError as exc:
                if exc.code in _TERMINAL_REFRESH:
                    self._clear_tokens(account)
                    self._vault.save(data)
                    raise AuthError("reauth_required", exc.status, exc.request_id) from None
                raise
            if PLAN_SCOPE not in account["scopes"]:
                raise AuthError("plan_not_enabled")
            return account["access_token"]

    def sign_out(self, account_id=None):
        with self._lock:
            self._stop_attempt("cancelled")
            with self._storage():
                data = self._vault.load()
                account_id = account_id or data.get("active_account_id")
                account = data["accounts"].get(account_id)
                if not account:
                    raise AuthError("account_not_found")
                confirmed = not account.get("access_token") and not account.get("refresh_token")
                if account.get("refresh_token"):
                    try:
                        self._request("POST", REVOCATION_URL, data={"token": account["refresh_token"],
                            "token_type_hint": "refresh_token", "client_id": account["client_id"]}, empty=True)
                        confirmed = True
                    except AuthError:
                        pass
                self._clear_tokens(account)
                account["revocation_unconfirmed"] = not confirmed
                self._vault.save(data)
        return self.status()

    def close(self):
        with self._lock:
            self._stop_attempt("cancelled")
            self._closed = True
            if self._owns_client:
                self._client.close()
