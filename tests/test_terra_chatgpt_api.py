"""Auth HTTP controls reuse the real local guard; no credentials or inference."""
from copy import deepcopy
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from terra_monitoring.chatgpt.api import create_router
from terra_monitoring.server import create_app

ORIGIN = "http://127.0.0.1:8517"


class Auth:
    def __init__(self):
        self.calls = []
        self.address = "https://auth.openai.com/api/accounts/authorize?client_id=dynamic_agent_client&state=fake"

    def status(self):
        return {"state": "connected", "plan_enabled": True, "active_account_id": "a1",
                "accounts": [{"account_id": "a1", "label": "Personal", "email": "fake@example.test",
                              "plan_enabled": True, "state": "connected", "subject": "private-sub",
                              "client_id": "private-client", "access_token": "private-access"}],
                "access_token": "private-access", "refresh_token": "private-refresh",
                "attempt": {"attempt_id": "try1", "state": "pending", "expires_at": "2030-01-01T00:00:00Z",
                            "code_verifier": "private-verifier", "state_secret": "private-state"}}

    def start(self, **kwargs):
        self.calls.append(("start", kwargs))
        return {"authorization_url": self.address, "attempt_id": "try1", "expires_at": "2030-01-01T00:00:00Z",
                "access_token": "private-access"}

    def cancel(self, attempt_id=None):
        self.calls.append(("cancel", attempt_id))

    def select_account(self, account_id):
        self.calls.append(("select", account_id))

    def sign_out(self, account_id=None):
        self.calls.append(("sign_out", account_id))


class Provider:
    def __init__(self):
        self.calls = []
        self.model = None

    def status(self):
        return {"connected": True, "active_account_id": "a1", "selected_model": self.model,
                "usage_paused": False, "cooldown_until": None, "access_token": "private-provider"}

    def models(self, refresh=False):
        self.calls.append(("models", refresh))
        return [{"slug": "test-small", "display_name": "Test small"}]

    def select_model(self, model):
        self.calls.append(("select_model", model))
        self.model = model

    def resume(self):
        self.calls.append(("resume",))


class SMS:
    def status(self):
        return {"configured": False, "enabled": False, "demo_enabled": False}


@pytest.fixture
def setup(tmp_path):
    auth, provider = Auth(), Provider()
    app = create_app(service=object(), sms_dispatcher=SMS(), web_root=tmp_path / "no-build")
    # This is the assembly order required by runtime: guarded controls before catch-all.
    routes = create_router(auth, provider, mutation_guard=app.state.local_mutation_guard).routes
    app.router.routes[:0] = routes
    client = TestClient(app, base_url=ORIGIN)
    headers = {"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin",
               "X-Terra-SMS-Control": client.get("/api/sms/status").json()["control_token"]}
    return client, headers, auth, provider


def test_default_router_denies_every_action():
    auth, provider = Auth(), Provider()
    app = FastAPI(); app.include_router(create_router(auth, provider))
    client = TestClient(app)
    assert client.get("/api/chatgpt/status").status_code == 403
    assert client.post("/api/chatgpt/connect", json={}).status_code == 403
    assert not auth.calls and not provider.calls


@pytest.mark.parametrize("headers", [{}, {"Origin": "https://outside.example"},
                                    {"Origin": ORIGIN, "X-Terra-SMS-Control": "wrong"}])
def test_actual_shared_guard_rejects_unauthorized_control(setup, headers):
    client, _, auth, provider = setup
    assert client.get("/api/chatgpt/status", headers=headers).status_code == 403
    assert client.post("/api/chatgpt/connect", json={}, headers=headers).status_code == 403
    assert not auth.calls and not provider.calls


def test_read_status_redacts_and_cannot_generate_or_list_models(setup):
    client, headers, auth, provider = setup
    response = client.get("/api/chatgpt/status", headers=headers)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["accounts"][0]["id"] == "a1" and data["plan_enabled"] is True
    assert "private-" not in response.text
    assert not auth.calls and not provider.calls


def test_connect_model_selection_and_disconnect_are_explicit(setup):
    client, headers, auth, provider = setup
    response = client.post("/api/chatgpt/connect", json={"account_id": "a1", "reconsent": True}, headers=headers)
    assert response.status_code == 200 and "private-" not in response.text
    assert urlsplit(response.json()["authorization_url"]).netloc == "auth.openai.com"
    assert auth.calls == [("start", {"account_id": "a1", "reconsent": True})]
    assert client.get("/api/chatgpt/models", headers=headers).json()["models"][0]["slug"] == "test-small"
    response = client.put("/api/chatgpt/model", json={"model": "test-small"}, headers=headers)
    assert response.json()["writer"]["selected_model"] == "test-small"
    assert client.post("/api/chatgpt/resume", json={}, headers=headers).status_code == 200
    assert client.post("/api/chatgpt/select-account", json={"account_id": "a1"}, headers=headers).status_code == 200
    assert client.post("/api/chatgpt/cancel", json={"attempt_id": "try1"}, headers=headers).status_code == 200
    assert client.post("/api/chatgpt/disconnect", json={"account_id": "a1"}, headers=headers).status_code == 200
    assert auth.calls[-1] == ("sign_out", "a1")
    assert provider.calls == [("models", False), ("select_model", "test-small"), ("resume",)]


@pytest.mark.parametrize("address", ["https://outside.example/auth", "http://auth.openai.com/api/accounts/authorize",
    "https://auth.openai.com/api/accounts/authorize?id_token_hint=private", "https://auth.openai.com.evil.test/api/accounts/authorize"])
def test_connect_refuses_unexpected_destinations_and_credential_hints(setup, address):
    client, headers, auth, _ = setup
    auth.address = address
    response = client.post("/api/chatgpt/connect", json={}, headers=headers)
    assert response.status_code == 422 and "private" not in response.text and address not in response.text
    assert auth.calls[-1] == ("cancel", "try1")


def test_errors_preserve_status_code_request_id_without_provider_body(setup):
    client, headers, _, provider = setup
    class Error(Exception):
        code = "subscription_sharing_usage_limit_exceeded"
        status = 429
        request_id = "req_test"
    def fail(**kwargs):
        raise Error("private-provider-response-and-token")
    provider.models = fail
    response = client.get("/api/chatgpt/models", headers=headers)
    assert response.status_code == 429
    assert response.json()["error"]["request_id"] == "req_test"
    assert "private" not in response.text


def test_input_extra_fields_and_wrong_types_are_rejected_without_actions(setup):
    client, headers, auth, provider = setup
    assert client.post("/api/chatgpt/connect", json={"access_token": "fake"}, headers=headers).status_code == 422
    assert client.post("/api/chatgpt/connect", json={"reconsent": "true"}, headers=headers).status_code == 422
    assert client.put("/api/chatgpt/model", json={"model": ""}, headers=headers).status_code == 422
    assert not auth.calls and not provider.calls
