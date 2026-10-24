"""Only fake credentials and MockTransport; loopback tests never contact OpenAI."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import stat
import time
from urllib.parse import parse_qs, urlencode, urlsplit

from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import jwt
import pytest

from terra_monitoring.chatgpt.auth import (
    AuthError, AuthManager, ISSUER, JWKS_URL, PLAN_SCOPE, RESOURCE, REVOCATION_URL, SCOPES, TOKEN_URL,
)
from terra_monitoring.chatgpt.vault import Vault, VaultError


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class Provider:
    def __init__(self, key):
        self.key = key
        self.requests = []
        self.parameters = None
        self.scopes = SCOPES
        self.subject = "fake-subject-one"
        self.client_id = "oaiapp_fake_one"
        self.failure = None
        self.claim_overrides = {}
        self.refresh_count = 0
        self.refresh_delay = 0
        self.refresh_scope = None
        self.refresh_id = None
        self.id_override = None
        self.revocation_status = 200
        self.client = httpx.Client(transport=httpx.MockTransport(self.handle))

    def handle(self, request):
        form = {key: items[0] for key, items in parse_qs(request.content.decode()).items()}
        self.requests.append((str(request.url), form))
        if str(request.url) == JWKS_URL:
            key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
            key.update(kid="fake-key", alg="RS256", use="sig")
            return httpx.Response(200, json={"keys": [key]})
        if str(request.url) == REVOCATION_URL:
            return httpx.Response(self.revocation_status, content=b"" if self.revocation_status == 200 else b'{"error":"unavailable"}')
        assert str(request.url) == TOKEN_URL
        if self.failure is not None:
            return self.failure(request)
        if form["grant_type"] == "refresh_token":
            self.refresh_count += 1
            time.sleep(self.refresh_delay)
            payload = {"access_token": "fake-refreshed-access", "refresh_token": "fake-rotated-refresh",
                       "token_type": "Bearer", "expires_in": 3600}
            if self.refresh_scope is not None:
                payload["scope"] = self.refresh_scope
            if self.refresh_id is not None:
                payload["id_token"] = self.refresh_id
            return httpx.Response(200, json=payload)
        claims = {"iss": ISSUER, "aud": self.client_id, "sub": self.subject,
                  "exp": time.time() + 3600, "nonce": self.parameters["nonce"], "email": "fake@example.test"}
        claims.update(self.claim_overrides)
        token = self.id_override or jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "fake-key"})
        return httpx.Response(200, json={"access_token": "fake-access", "refresh_token": "fake-refresh",
            "id_token": token, "token_type": "Bearer", "expires_in": 3600, "scope": self.scopes})


@pytest.fixture
def connection(tmp_path, signing_key):
    provider = Provider(signing_key)
    clock = [time.time()]
    manager = AuthManager(tmp_path / "credentials", client=provider.client, clock=lambda: clock[0])
    yield manager, provider, clock
    manager.close()
    provider.client.close()


def start(manager, provider, **kwargs):
    result = manager.start(**kwargs)
    provider.parameters = {key: items[0] for key, items in parse_qs(urlsplit(result["authorization_url"]).query).items()}
    if provider.parameters["client_id"] != "dynamic_agent_client":
        provider.client_id = provider.parameters["client_id"]
    return result


def callback(provider, *, state=None, code="fake-code", client_id=True, extra=None, headers=None):
    parameters = {"state": state if state is not None else provider.parameters["state"], "code": code}
    if client_id:
        parameters["client_id"] = provider.client_id
    parameters.update(extra or {})
    url = provider.parameters["redirect_uri"] + "?" + urlencode(parameters)
    with httpx.Client(trust_env=False, timeout=5) as browser:
        return browser.get(url, headers=headers)


def login(manager, provider, **kwargs):
    start(manager, provider, **kwargs)
    assert callback(provider).status_code == 200
    assert manager.status()["attempt"]["state"] == "succeeded"
    return manager.status()["active_account_id"]


def expire(manager, clock):
    clock[0] += 3550


def test_initial_authorization_uses_exact_contract_and_fresh_values(connection):
    manager, provider, _ = connection
    first = start(manager, provider)
    original = provider.parameters.copy()
    assert original["client_id"] == "dynamic_agent_client"
    assert original["agent_name_hint"] == "Terra"
    assert original["ext_agent_host_id"].startswith("urn:uuid:")
    assert original["scope"] == SCOPES and original["resource"] == RESOURCE
    assert original["code_challenge_method"] == "S256"
    expected = __import__("base64").urlsafe_b64encode(hashlib.sha256(manager._pending["verifier"].encode()).digest()).rstrip(b"=").decode()
    assert original["code_challenge"] == expected
    assert urlsplit(original["redirect_uri"]).hostname == "127.0.0.1"
    assert urlsplit(original["redirect_uri"]).path == "/auth/callback"
    assert not {"id_token_hint", "login_hint", "prompt"} & original.keys()
    second = start(manager, provider)
    assert first["attempt_id"] != second["attempt_id"]
    assert original["state"] != provider.parameters["state"]
    assert original["nonce"] != provider.parameters["nonce"]


def test_real_signed_login_status_redaction_and_exchange(connection):
    manager, provider, _ = connection
    account_id = login(manager, provider)
    status = manager.status()
    assert status["schema"] == "terra-chatgpt-connection-v1"
    assert status["state"] == "connected" and status["plan_enabled"]
    assert status["accounts"][0]["account_id"] == account_id
    assert set(status["accounts"][0]) == {"account_id", "email", "label", "state", "plan_enabled", "revocation_unconfirmed"}
    serialized = json.dumps(status)
    for secret in ("fake-access", "fake-refresh", "fake-code", provider.subject, provider.client_id, provider.parameters["nonce"], provider.parameters["state"]):
        assert secret not in serialized
    form = provider.requests[0][1]
    assert form["client_id"] == provider.client_id
    assert form["redirect_uri"] == provider.parameters["redirect_uri"]
    assert form["resource"] == RESOURCE
    assert manager.access_token() == "fake-access"


def test_restart_preserves_account_credentials_and_host(connection):
    manager, provider, _ = connection
    account_id = login(manager, provider)
    host = provider.parameters["ext_agent_host_id"]
    manager.close()
    restarted = AuthManager(manager._vault.directory, client=provider.client)
    try:
        assert restarted.status()["active_account_id"] == account_id
        assert restarted.access_token() == "fake-access"
        start(restarted, provider, account_id=account_id, reconsent=True)
        assert provider.parameters["client_id"] == provider.client_id
        assert provider.parameters["ext_agent_host_id"] == host
        assert provider.parameters["prompt"] == "consent"
        assert "agent_name_hint" not in provider.parameters
        assert "id_token_hint" not in provider.parameters and "login_hint" not in provider.parameters
    finally:
        restarted.close()


def test_identity_without_actual_plan_scope_never_enables_inference(connection):
    manager, provider, _ = connection
    provider.scopes = "openid email profile"
    login(manager, provider)
    assert manager.status()["state"] == "plan_disabled"
    assert not manager.status()["plan_enabled"]
    with pytest.raises(AuthError, match="plan_not_enabled"):
        manager.access_token()
    assert len(provider.requests) == 2


@pytest.mark.parametrize("overrides", [
    {"nonce": "wrong"}, {"nonce": None}, {"iss": "https://attacker.test"},
    {"aud": "another-client"}, {"exp": 1}, {"sub": ""}, {"nbf": 9_999_999_999},
    {"aud": ["oaiapp_fake_one", "other-client"]}, {"azp": "other-client"},
])
def test_invalid_id_token_claims_fail_closed(connection, overrides):
    manager, provider, _ = connection
    provider.claim_overrides = overrides
    start(manager, provider)
    assert callback(provider).status_code == 400
    assert manager.status()["attempt"]["error"] == "invalid_id_token"
    assert manager.status()["accounts"] == []
    with pytest.raises(AuthError, match="reauth_required"):
        manager.access_token()


def test_wrong_signature_is_rejected(connection):
    manager, provider, _ = connection
    original_key = provider.key
    provider.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    start(manager, provider)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(original_key.public_key()))
    manager._jwks = {"keys": [dict(jwk, kid="fake-key")]}
    manager._jwks_saved = manager._clock()
    assert callback(provider).status_code == 400
    assert manager.status()["attempt"]["error"] == "invalid_id_token"


def test_symmetric_algorithm_is_rejected_without_jwks_request(connection):
    manager, provider, _ = connection
    start(manager, provider)
    provider.id_override = jwt.encode({"iss": ISSUER, "aud": provider.client_id,
        "sub": provider.subject, "exp": time.time() + 3600, "nonce": provider.parameters["nonce"]},
        "fake-symmetric-key-that-is-long-enough", algorithm="HS256", headers={"kid": "fake-key"})
    assert callback(provider).status_code == 400
    assert manager.status()["attempt"]["error"] == "invalid_id_token"
    assert all(url != JWKS_URL for url, _ in provider.requests)


def test_unknown_kid_refetches_fixed_jwks_once_for_key_rotation(connection):
    manager, provider, _ = connection
    manager._jwks = {"keys": []}
    manager._jwks_saved = manager._clock()
    login(manager, provider)
    assert len([url for url, _ in provider.requests if url == JWKS_URL]) == 1


def test_wrong_state_is_not_consumed_and_denial_is_terminal(connection):
    manager, provider, _ = connection
    start(manager, provider)
    assert callback(provider, state="wrong").status_code == 400
    assert callback(provider, state="non-ascii-\u2603").status_code == 400
    assert manager.status()["attempt"]["state"] == "pending"
    assert provider.requests == []
    denied = callback(provider, extra={"error": "access_denied", "error_description": "secret-supplied-text"})
    assert denied.status_code == 200
    assert "secret-supplied-text" not in denied.text
    assert manager.status()["attempt"]["state"] == "denied"
    assert provider.requests == []
    assert manager._server is None


def test_callback_rejects_duplicate_fields_host_and_body(connection):
    manager, provider, _ = connection
    start(manager, provider)
    base = provider.parameters["redirect_uri"]
    state = provider.parameters["state"]
    with httpx.Client(trust_env=False, timeout=5) as browser:
        assert browser.get(base + "?state=" + state + "&state=" + state).status_code == 400
        assert browser.get(base + "?state=" + state, headers={"Host": "attacker.test"}).status_code == 400
        assert browser.request("GET", base + "?state=" + state, content=b"unexpected").status_code == 400
        assert browser.get(base + "?state=" + state + "&padding=" + "x" * 8200).status_code == 400
    assert manager.status()["attempt"]["state"] == "pending"
    assert provider.requests == []


def test_callback_html_never_echoes_credentials_and_is_one_use(connection):
    manager, provider, _ = connection
    start(manager, provider)
    pending = manager._pending.copy()
    response = callback(provider)
    assert response.status_code == 200
    assert "fake-code" not in response.text and "fake-access" not in response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    with pytest.raises(AuthError, match="attempt_not_found"):
        manager._callback(pending["attempt_id"], {"state": pending["state"], "code": "fake-code"})


def test_cancel_timeout_close_stop_listeners(connection):
    manager, provider, clock = connection
    attempt = start(manager, provider)
    with pytest.raises(AuthError, match="attempt_not_found"):
        manager.cancel("other-attempt")
    manager.cancel(attempt["attempt_id"])
    assert manager._server is None and manager.status()["attempt"]["state"] == "cancelled"
    start(manager, provider)
    clock[0] += 301
    assert callback(provider).status_code == 400
    assert manager._server is None and manager.status()["attempt"]["state"] == "timed_out"
    start(manager, provider)
    manager.close()
    assert manager._server is None
    with pytest.raises(AuthError, match="manager_closed"):
        manager.start()


def test_timer_stops_listener_without_callback(tmp_path, signing_key):
    provider = Provider(signing_key)
    manager = AuthManager(tmp_path, client=provider.client, callback_timeout=0.05)
    try:
        manager.start()
        deadline = time.monotonic() + 2
        while manager.status()["attempt"]["state"] == "pending" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert manager.status()["attempt"]["state"] == "timed_out"
        assert manager._server is None
    finally:
        manager.close()
        provider.client.close()


def test_issued_client_persisted_before_invalid_grant_then_reused_after_restart(connection):
    manager, provider, _ = connection
    provider.failure = lambda request: httpx.Response(400, json={"error": "invalid_grant", "secret": "never-expose"})
    start(manager, provider)
    host = provider.parameters["ext_agent_host_id"]
    assert callback(provider).status_code == 400
    assert manager.status()["attempt"]["error"] == "invalid_grant"
    manager.close()
    restarted = AuthManager(manager._vault.directory, client=provider.client)
    try:
        start(restarted, provider)
        assert provider.parameters["client_id"] == provider.client_id
        assert provider.parameters["ext_agent_host_id"] == host
        assert "agent_name_hint" not in provider.parameters
        provider.failure = None
        assert callback(provider, client_id=False).status_code == 200
        assert restarted.status()["state"] == "connected"
    finally:
        restarted.close()


def test_missing_registration_client_id_is_rejected(connection):
    manager, provider, _ = connection
    start(manager, provider)
    assert callback(provider, client_id=False).status_code == 400
    assert manager.status()["attempt"]["error"] == "registration_incomplete"
    assert provider.requests == []


def test_multiregistration_same_email_switching_and_identity_preservation(connection):
    manager, provider, _ = connection
    first = login(manager, provider)
    provider.client_id = "oaiapp_fake_two"
    provider.subject = "fake-subject-two"
    start(manager, provider)
    assert manager.status()["active_account_id"] == first
    assert callback(provider).status_code == 200
    second = manager.status()["active_account_id"]
    assert first != second and len(manager.status()["accounts"]) == 2
    manager.select_account(first)
    start(manager, provider, account_id=first)
    assert callback(provider).status_code == 400
    assert manager.status()["attempt"]["error"] == "account_mismatch"
    assert manager.status()["active_account_id"] == first
    assert manager.access_token() == "fake-access"
    assert len(manager.status()["accounts"]) == 2


def test_returning_client_id_mismatch_preserves_existing_credentials(connection):
    manager, provider, _ = connection
    account_id = login(manager, provider)
    start(manager, provider, account_id=account_id)
    provider.client_id = "oaiapp_other"
    assert callback(provider).status_code == 400
    assert manager.status()["attempt"]["error"] == "client_mismatch"
    assert manager.access_token() == "fake-access"
    assert len(provider.requests) == 2


def test_rotating_refresh_uses_saved_client_and_retains_scopes(connection):
    manager, provider, clock = connection
    login(manager, provider)
    expire(manager, clock)
    assert manager.access_token() == "fake-refreshed-access"
    form = provider.requests[-1][1]
    assert form == {"grant_type": "refresh_token", "client_id": provider.client_id,
                    "refresh_token": "fake-refresh", "resource": RESOURCE}
    assert manager.access_token() == "fake-refreshed-access" and provider.refresh_count == 1
    with manager._vault.locked():
        data = manager._vault.load()
    account = data["accounts"][data["active_account_id"]]
    assert account["refresh_token"] == "fake-rotated-refresh"
    assert PLAN_SCOPE in account["scopes"]


def test_refresh_rechecks_actual_scope_and_retains_identity(connection):
    manager, provider, clock = connection
    account_id = login(manager, provider)
    expire(manager, clock)
    provider.refresh_scope = "openid profile email"
    with pytest.raises(AuthError, match="plan_not_enabled"):
        manager.access_token()
    assert manager.status()["active_account_id"] == account_id
    assert manager.status()["state"] == "plan_disabled"


def test_refresh_rejects_changed_signed_identity_without_replacing_tokens(connection):
    manager, provider, clock = connection
    login(manager, provider)
    expire(manager, clock)
    provider.refresh_id = jwt.encode({"iss": ISSUER, "aud": provider.client_id,
        "sub": "fake-other-subject", "exp": clock[0] + 3600}, provider.key,
        algorithm="RS256", headers={"kid": "fake-key"})
    with pytest.raises(AuthError, match="account_mismatch"):
        manager.access_token()
    with manager._vault.locked():
        data = manager._vault.load()
        assert data["accounts"][data["active_account_id"]]["refresh_token"] == "fake-refresh"


@pytest.mark.parametrize("error", ["invalid_grant", "invalid_refresh_token", "refresh_token_reused", "refresh_token_expired", "token_expired", "refresh_token_invalidated"])
def test_terminal_refresh_clears_only_tokens_reauthorization_reuses_mapping(connection, error):
    manager, provider, clock = connection
    account_id = login(manager, provider)
    expire(manager, clock)
    provider.failure = lambda request: httpx.Response(400, json={"error": error})
    with pytest.raises(AuthError, match="reauth_required"):
        manager.access_token()
    assert manager.status()["state"] == "reauth_required"
    assert manager.status()["active_account_id"] == account_id
    start(manager, provider, account_id=account_id)
    assert provider.parameters["client_id"] == provider.client_id
    assert "id_token_hint" not in provider.parameters


@pytest.mark.parametrize("kind", ["network", "server", "invalid_client", "redirect", "malformed", "oversized", "malformed_error"])
def test_transient_or_configuration_refresh_failure_preserves_credentials(connection, kind):
    manager, provider, clock = connection
    login(manager, provider)
    expire(manager, clock)

    def fail(request):
        if kind == "network":
            raise httpx.ConnectError("fake-refresh secret-network-message", request=request)
        if kind == "server":
            return httpx.Response(503, json={"error": {"code": "unknown", "message": "secret-message"}})
        if kind == "invalid_client":
            return httpx.Response(400, json={"error": "invalid_client"})
        if kind == "redirect":
            return httpx.Response(302, headers={"Location": "https://attacker.test"})
        if kind == "malformed":
            return httpx.Response(200, content=b"not-json-secret")
        if kind == "oversized":
            return httpx.Response(200, content=b"x" * 131073)
        return httpx.Response(400, json={"error": {"code": {"unexpected": "secret"}}})

    provider.failure = fail
    with pytest.raises(AuthError) as caught:
        manager.access_token()
    assert "secret" not in str(caught.value)
    assert manager.status()["state"] == "connected"
    provider.failure = None
    assert manager.access_token() == "fake-refreshed-access"
    assert provider.requests[-1][1]["refresh_token"] == "fake-refresh"


def test_two_managers_refresh_once_in_threads(connection):
    manager, provider, clock = connection
    login(manager, provider)
    expire(manager, clock)
    other = AuthManager(manager._vault.directory, client=provider.client, clock=lambda: clock[0])
    provider.refresh_delay = 0.1
    try:
        with ThreadPoolExecutor(2) as executor:
            tokens = list(executor.map(lambda item: item.access_token(), [manager, other]))
        assert tokens == ["fake-refreshed-access"] * 2
        assert provider.refresh_count == 1
    finally:
        other.close()


def _process_refresh(directory, queue):
    def handle(request):
        with (Path(directory) / "fake-refresh-count").open("ab") as count:
            count.write(b"1")
        time.sleep(0.15)
        return httpx.Response(200, json={"access_token": "fake-process-access", "refresh_token": "fake-process-rotated",
                                       "token_type": "Bearer", "expires_in": 3600})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        manager = AuthManager(directory, client=client)
        try:
            queue.put(manager.access_token())
        finally:
            manager.close()


def test_native_lock_serializes_rotating_refresh_across_processes(connection):
    manager, provider, _ = connection
    login(manager, provider)
    with manager._vault.locked():
        data = manager._vault.load()
        data["accounts"][data["active_account_id"]]["expires_at"] = 1
        manager._vault.save(data)
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    processes = [context.Process(target=_process_refresh, args=(str(manager._vault.directory), queue)) for _ in range(2)]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(15)
            assert process.exitcode == 0
        assert [queue.get(timeout=2) for _ in processes] == ["fake-process-access"] * 2
        assert (manager._vault.directory / "fake-refresh-count").read_bytes() == b"1"
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(3)
        queue.close()


@pytest.mark.parametrize("confirmed", [True, False])
def test_sign_out_attempts_revocation_then_clears_tokens_and_retains_mapping(connection, confirmed):
    manager, provider, _ = connection
    account_id = login(manager, provider)
    host = provider.parameters["ext_agent_host_id"]
    provider.revocation_status = 200 if confirmed else 503
    status = manager.sign_out()
    assert status["state"] == "reauth_required"
    assert status["accounts"][0]["revocation_unconfirmed"] is (not confirmed)
    assert provider.requests[-1] == (REVOCATION_URL, {"token": "fake-refresh", "token_type_hint": "refresh_token", "client_id": provider.client_id})
    assert len([item for item in provider.requests if item[0] == REVOCATION_URL]) == 1
    with manager._vault.locked():
        account = manager._vault.load()["accounts"][account_id]
    assert not {"access_token", "refresh_token", "id_token", "nonce"} & account.keys()
    start(manager, provider, account_id=account_id)
    assert provider.parameters["client_id"] == provider.client_id
    assert provider.parameters["ext_agent_host_id"] == host


def test_auth_errors_sanitize_metadata_and_body():
    error = AuthError("invalid_grant", 400, "req_fake-123")
    assert str(error) == "invalid_grant" and error.status == 400 and error.request_id == "req_fake-123"
    error = AuthError("secret body\n", -1, "secret\nrequest")
    assert str(error) == "authentication_failed" and error.status is None and error.request_id is None


def test_vault_atomic_roundtrip_and_platform_protection(tmp_path):
    vault = Vault(tmp_path / "vault")
    document = {"version": 1, "accounts": {"fake": {"access_token": "fake-secret-roundtrip"}}}
    with vault.locked():
        vault.save(document)
        assert vault.load() == document
    if os.name == "nt":
        assert vault.path.read_bytes().startswith(b"TERRA-DPAPI-1\n")
        assert b"fake-secret-roundtrip" not in vault.path.read_bytes()
    else:
        assert stat.S_IMODE(vault.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(vault.directory.stat().st_mode) == 0o700


def test_protection_failure_leaves_existing_vault_and_no_plaintext_fallback(tmp_path, monkeypatch):
    vault = Vault(tmp_path / "vault")
    document = {"version": 1, "accounts": {}}
    with vault.locked():
        vault.save(document)
        original = vault.path.read_bytes()

        def broken_protection(data):
            raise VaultError("credential_protection_failed")

        monkeypatch.setattr(vault, "_protect", broken_protection)
        with pytest.raises(VaultError, match="credential_protection_failed"):
            vault.save({"version": 1, "accounts": {"fake-secret": {}}})
        assert vault.path.read_bytes() == original
        assert list(vault.directory.glob(".chatgpt-*")) == []


def test_storage_failure_is_redacted_auth_error(connection, monkeypatch):
    manager, _, _ = connection
    monkeypatch.setattr(manager._vault, "load", lambda: (_ for _ in ()).throw(VaultError("credential_protection_failed")))
    with pytest.raises(AuthError, match="credential_protection_failed"):
        manager.status()
