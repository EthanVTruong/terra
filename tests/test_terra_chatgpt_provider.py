"""Offline request, SSE, persistence, and redaction contracts for plan inference."""
import importlib
import json
import multiprocessing

import httpx
import pytest

from terra_monitoring.chatgpt import provider
from terra_monitoring.chatgpt.provider import ChatGPTPlanClient, PlanError

CATALOG = [{"slug": "account-model-z", "display_name": "Account model Z", "visibility": "list"},
           {"slug": "internal-model", "display_name": "Hidden", "visibility": "hidden"},
           {"slug": "account-model-a", "display_name": "Account model A", "visibility": "list"}]
TEXT = "Demo orchard: estimated chill remains at the supplied total."
PACKET = {"schema_version": "terra-grower-message-v1", "orchard": {"name": "Demo orchard"},
          "events": [{"facts": {"hours": 12.5, "complete": True}}]}


class FakeAuthError(Exception):
    code = "plan_usage_disabled"
    status = 403
    request_id = "auth-request"


class FakeAuth:
    account = "account-one"
    allowed = True
    token_calls = 0

    def status(self):
        return {"active_account_id": self.account, "plan_enabled": self.allowed}

    def access_token(self):
        self.token_calls += 1
        if not self.allowed:
            raise FakeAuthError("PRIVATE-TOKEN must never be visible")
        return "oauth-only-test-token"


def event(kind, **fields):
    return {"type": kind, **fields}


def delta(text=TEXT, **fields):
    return event("response.output_text.delta", output_index=0, content_index=0, delta=text, **fields)


def completed(text=TEXT, **fields):
    return event("response.completed", response={"status": "completed", "output": [
        {"type": "message", "content": [{"type": "output_text", "text": text}]}], **fields})


def sse(*events):
    content = "".join("event: " + item["type"] + "\ndata: " + json.dumps(item) + "\n\n" for item in events)
    return httpx.Response(200, content=content, headers={"content-type": "text/event-stream", "x-request-id": "req-test-123"})


def setup_client(tmp_path, outcome=None, *, auth=None, clock=None, catalog=None, select=True):
    requests = []

    def respond(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"models": CATALOG if catalog is None else catalog})
        return outcome(request) if callable(outcome) else outcome if outcome is not None else sse(completed())

    auth = auth or FakeAuth()
    client = httpx.Client(transport=httpx.MockTransport(respond))
    instance = ChatGPTPlanClient(auth, tmp_path, client=client, clock=clock)
    if select:
        instance.select_model("account-model-z")
    return instance, requests, auth, client


def assert_safe(error):
    value = json.dumps(error.to_dict()) + str(error)
    assert "PRIVATE" not in value
    assert "oauth-only" not in value
    assert "hours" not in value


def test_large_realistic_catalog_keeps_only_visible_display_fields(tmp_path):
    # The real account catalog includes substantial model metadata. It is not
    # note content and must fit without persisting prompts or hidden entries.
    rows = [{**row, "instructions": "PRIVATE-CATALOG-METADATA" * 9_000}
            for row in CATALOG]
    assert 568_400 < len(json.dumps({"models": rows}).encode()) < provider.MAX_CATALOG_BYTES
    instance, _, _, _ = setup_client(tmp_path, catalog=rows, select=False)
    expected = [{"slug": row["slug"], "display_name": row["display_name"]}
                for row in CATALOG if row["visibility"] == "list"]
    assert instance.models() == expected
    assert "PRIVATE-CATALOG" not in json.dumps(instance._catalog)
    assert all("PRIVATE-CATALOG" not in path.read_text(encoding="utf-8")
               for path in tmp_path.glob("*.json"))


def test_request_uses_public_oauth_stream_array_without_unsupported_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "PRIVATE-API-KEY")
    instance, requests, _, _ = setup_client(tmp_path)
    result = instance.generate("Write one or two concise sentences.", PACKET)
    assert result == {"text": TEXT, "model": "account-model-z", "request_id": "req-test-123", "usage": {}}
    request = requests[-1]
    assert str(request.url) == "https://api.openai.com/v1/responses"
    assert request.method == "POST"
    assert request.headers["authorization"] == "Bearer oauth-only-test-token"
    body = json.loads(request.content)
    assert set(body) == {"model", "instructions", "input", "store", "stream"}
    assert body["model"] == "account-model-z"
    assert body["store"] is False and body["stream"] is True
    assert body["input"][0]["role"] == "user"
    assert json.loads(body["input"][0]["content"]) == PACKET
    assert len(requests) == 2
    assert str(requests[0].url) == "https://api.openai.com/v1/models"
    assert requests[0].headers["authorization"] == "Bearer oauth-only-test-token"


def test_optional_schema_uses_documented_text_format(tmp_path):
    schema = {"type": "object", "properties": {"text": {"type": "string"}},
              "required": ["text"], "additionalProperties": False}
    instance, requests, _, _ = setup_client(tmp_path, sse(completed('{"text":"A supplied note."}')))
    result = instance.generate("Return JSON.", PACKET, schema=schema)
    assert result["text"] == '{"text":"A supplied note."}'
    assert json.loads(requests[-1].content)["text"] == {"format": {
        "type": "json_schema", "name": "terra_grower_note", "strict": True, "schema": schema}}


def test_catalog_is_ordered_redacted_copied_and_requires_explicit_selection(tmp_path):
    rows = [{**CATALOG[0], "secret": "PRIVATE", "eligibility": "invented"}, *CATALOG[1:]]
    instance, requests, _, _ = setup_client(tmp_path, catalog=rows, select=False)
    models = instance.models()
    assert models == [{"slug": "account-model-z", "display_name": "Account model Z"},
                      {"slug": "account-model-a", "display_name": "Account model A"}]
    models[0]["slug"] = "changed"
    assert instance.models()[0]["slug"] == "account-model-z"
    with pytest.raises(PlanError, match="model_selection_required"):
        instance.generate("Write a note.", PACKET)
    assert len(requests) == 1
    with pytest.raises(PlanError, match="model_unavailable"):
        instance.select_model("internal-model")


@pytest.mark.parametrize("catalog", [None, {}, {"models": {}}, {"models": [None]},
    {"models": [CATALOG[0], CATALOG[0]]}, {"models": [{**CATALOG[0], "slug": "bad slug"}]},
    {"models": [{**CATALOG[0], "display_name": "bad\nname"}]}, {"models": CATALOG * 100}])
def test_malformed_catalog_is_rejected(tmp_path, catalog):
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=catalog)))
    instance = ChatGPTPlanClient(FakeAuth(), tmp_path, client=client)
    with pytest.raises(PlanError, match="invalid_model_catalog"):
        instance.models()


def test_catalog_selection_persists_per_account_and_switch_refreshes(tmp_path):
    instance, requests, auth, client = setup_client(tmp_path)
    restarted = ChatGPTPlanClient(auth, tmp_path, client=client)
    assert restarted.status()["selected_model"] == "account-model-z"
    restarted.generate("Write a note.", PACKET)
    auth.account = "account-two"
    assert restarted.status()["selected_model"] is None
    restarted.select_model("account-model-a")
    auth.account = "account-one"
    restarted.generate("Write a note.", PACKET)
    assert len([request for request in requests if request.method == "GET"]) == 4
    state = (tmp_path / "provider.json").read_text()
    assert "account-one" not in state and "account-two" not in state and "oauth" not in state


def test_refresh_invalidates_unavailable_selected_model(tmp_path):
    instance, _, _, _ = setup_client(tmp_path)
    instance.client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"models": []})))
    assert instance.models(refresh=True) == []
    assert instance.status()["selected_model"] is None


def test_status_is_local_and_permission_failure_never_falls_back_to_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "PRIVATE-API-KEY")
    instance, requests, auth, _ = setup_client(tmp_path)
    before, token_calls = len(requests), auth.token_calls
    assert instance.status()["connected"] is True
    assert len(requests) == before and auth.token_calls == token_calls
    auth.allowed = False
    with pytest.raises(PlanError) as caught:
        instance.generate("Write a note.", PACKET)
    assert caught.value.code == "plan_usage_disabled"
    assert caught.value.status == 403
    assert_safe(caught.value)
    assert len(requests) == before
    auth.account = None
    assert instance.status()["connected"] is False


def test_completed_text_overrides_partial_deltas_and_redacts_usage(tmp_path):
    instance, _, _, _ = setup_client(tmp_path, sse(delta("partial and wrong"), completed(usage={
        "input_tokens": 14, "output_tokens": 12, "total_tokens": 26,
        "private": "PRIVATE", "input_tokens_details": {"secret": "PRIVATE"}})))
    result = instance.generate("Write a note.", PACKET)
    assert result["text"] == TEXT
    assert result["usage"] == {"input_tokens": 14, "output_tokens": 12, "total_tokens": 26}


def test_verified_deltas_only_fallback_after_completed(tmp_path):
    terminal = event("response.completed", response={"status": "completed"})
    done = event("response.output_text.done", output_index=0, content_index=0, text=TEXT)
    instance, _, _, _ = setup_client(tmp_path, sse(delta(), done, terminal))
    assert instance.generate("Write a note.", PACKET)["text"] == TEXT
    instance.client = httpx.Client(transport=httpx.MockTransport(lambda _: sse(delta(), terminal)))
    with pytest.raises(PlanError, match="invalid_stream"):
        instance.generate("Write a note.", PACKET)


def test_plan_gateway_sse_without_content_type_still_requires_completed(tmp_path):
    wire = sse(completed()).content
    response = httpx.Response(200, content=wire, headers={"x-oai-request-id": "req-gateway-1"})
    instance, _, _, _ = setup_client(tmp_path, response)
    result = instance.generate("Write a note.", PACKET)
    assert result["text"] == TEXT
    assert result["request_id"] == "req-gateway-1"


def gateway_items(text=TEXT, **fields):
    return event("response.output_item.done", output_index=0, item={"type": "message", "role": "assistant",
        "status": "completed", "content": [{"type": "output_text", "text": text}], **fields})


def test_gateway_empty_terminal_uses_only_cross_checked_completed_items(tmp_path):
    final = event("response.completed", response={"status": "completed", "output": []})
    done = event("response.output_text.done", output_index=0, content_index=0, text=TEXT)
    instance, _, _, _ = setup_client(tmp_path, sse(delta(), done, gateway_items(), final))
    assert instance.generate("Write a note.", PACKET)["text"] == TEXT


@pytest.mark.parametrize("item", [gateway_items("changed"), gateway_items(role="user"),
    gateway_items(status="in_progress")])
def test_gateway_terminal_never_accepts_mismatch_refusal_or_incomplete_item(tmp_path, item):
    final = event("response.completed", response={"status": "completed", "output": []})
    done = event("response.output_text.done", output_index=0, content_index=0, text=TEXT)
    instance, _, _, _ = setup_client(tmp_path, sse(delta(), done, item, final))
    with pytest.raises(PlanError, match="invalid_stream"):
        instance.generate("Write a note.", PACKET)


@pytest.mark.parametrize("outcome", [
    sse(event("response.refusal.done", refusal="No"), completed()),
    sse(event("response.content_part.done", part={"type": "refusal", "refusal": "No"}), completed()),
    sse(delta(), event("response.output_text.done", output_index=0, content_index=0, text=TEXT),
        gateway_items(content=[{"type": "refusal", "refusal": "No"}]),
        event("response.completed", response={"status": "completed"})),
    sse(event("response.completed", response={"status": "completed", "output": [{"type": "message",
        "content": [{"type": "output_text", "text": TEXT}, {"type": "refusal", "refusal": "No"}]}]})),
])
def test_refusal_is_sticky_across_all_completion_paths(tmp_path, outcome):
    instance, _, _, _ = setup_client(tmp_path, outcome)
    with pytest.raises(PlanError, match="response_refused"):
        instance.generate("Write a note.", PACKET)


@pytest.mark.parametrize("body", [b'{"text":"not an event"}', sse(delta()).content])
def test_missing_content_type_never_accepts_json_or_partial_stream(tmp_path, body):
    instance, _, _, _ = setup_client(tmp_path, httpx.Response(200, content=body))
    with pytest.raises(PlanError, match="stream_interrupted"):
        instance.generate("Write a note.", PACKET)


@pytest.mark.parametrize("outcome,code", [
    (sse(delta()), "stream_interrupted"),
    (sse(event("response.incomplete", response={"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}})), "response_incomplete"),
    (sse(event("response.failed", response={"error": {"code": "server_error", "message": "PRIVATE"}})), "server_error"),
    (sse(event("error", code="invalid_request_error", message="PRIVATE")), "invalid_request_error"),
    (sse(event("response.completed", response={"status": "incomplete"})), "invalid_stream"),
    (sse(completed("")), "empty_output"),
    (sse(delta(), event("response.completed", response={"status": "completed", "output": []})), "empty_output"),
    (httpx.Response(200, json={"text": "PRIVATE"}), "invalid_stream"),
    (httpx.Response(200, content="data: {bad PRIVATE}\n\n", headers={"content-type": "text/event-stream"}), "invalid_stream"),
    (httpx.Response(200, content="data: [DONE]\n\n", headers={"content-type": "text/event-stream"}), "stream_interrupted"),
])
def test_no_partial_or_failed_stream_is_success(tmp_path, outcome, code):
    instance, requests, _, _ = setup_client(tmp_path, outcome)
    with pytest.raises(PlanError) as caught:
        instance.generate("Write a note.", PACKET)
    assert caught.value.code == code
    assert_safe(caught.value)
    assert len([request for request in requests if request.method == "POST"]) == 1


@pytest.mark.parametrize("status,body,code,param", [
    (401, {"detail": "PRIVATE signed identity failed"}, "http_error", None),
    (403, {"detail": "PRIVATE region"}, "http_error", None),
    (400, {"error": {"code": "subscription_sharing_unsupported_capability", "param": "text.format", "message": "PRIVATE"}},
     "subscription_sharing_unsupported_capability", "text.format"),
    (403, {"error": {"code": "subscription_sharing_user_not_eligible"}}, "subscription_sharing_user_not_eligible", None),
    (403, {"error": {"code": "chatpass_v2_scope_not_authorized"}}, "chatpass_v2_scope_not_authorized", None),
    (400, {"error": {"code": "PRIVATE prose is not a code", "param": "PRIVATE prose"}}, "http_error", None),
    (302, {"detail": "PRIVATE redirect"}, "http_error", None),
])
def test_admission_error_retains_status_shape_code_and_request_id(tmp_path, status, body, code, param):
    response = httpx.Response(status, json=body, headers={"x-request-id": "req-machine-1", "location": "https://private.invalid"})
    instance, requests, _, _ = setup_client(tmp_path, response)
    with pytest.raises(PlanError) as caught:
        instance.generate("Write a note.", PACKET)
    error = caught.value
    assert (error.code, error.status, error.param, error.request_id) == (code, status, param, "req-machine-1")
    assert error.error_shape == provider._shape(body)
    assert_safe(error)
    assert len(requests) == 2


@pytest.mark.parametrize("before_stream", [False, True], ids=["stream-failure", "admission-failure"])
def test_usage_limit_pauses_across_restart_until_explicit_resume(tmp_path, before_stream):
    failure = event("response.failed", response={"error": {
        "code": "subscription_sharing_usage_limit_exceeded", "message": "PRIVATE"}})
    outcome = httpx.Response(429, json=failure["response"]) if before_stream else sse(delta(), failure)
    instance, requests, auth, client = setup_client(tmp_path, outcome)
    with pytest.raises(PlanError) as caught:
        instance.generate("Write a note.", PACKET)
    assert caught.value.code == "subscription_sharing_usage_limit_exceeded"
    assert caught.value.status == (429 if before_stream else 200)
    restarted = ChatGPTPlanClient(auth, tmp_path, client=client)
    assert restarted.status()["usage_paused"] is True
    before = len(requests)
    token_calls = auth.token_calls
    with pytest.raises(PlanError, match="usage_paused"):
        restarted.generate("Write a note.", PACKET)
    assert len(requests) == before
    assert auth.token_calls == token_calls
    assert restarted.status()["usage_url"] == "https://chatgpt.com/settings/usage"
    auth.account = "account-two"
    assert restarted.status()["usage_paused"] is False
    auth.account = "account-one"
    assert restarted.resume()["usage_paused"] is False
    assert auth.allowed is True


@pytest.mark.parametrize("outcome", [httpx.Response(503, json={"detail": "PRIVATE unavailable"}),
    sse(event("response.failed", response={"error": {"code": "subscription_sharing_usage_unavailable"}}))])
def test_temporary_failures_persist_bounded_cooldown_without_reauth(tmp_path, outcome):
    now = [1000.0]
    instance, requests, auth, client = setup_client(tmp_path, outcome, clock=lambda: now[0])
    for attempt in range(7):
        with pytest.raises(PlanError):
            instance.generate("Write a note.", PACKET)
        status = instance.status()
        assert 0 < status["cooldown_until"] - now[0] <= 300
        before = len(requests)
        restarted = ChatGPTPlanClient(auth, tmp_path, client=client, clock=lambda: now[0])
        with pytest.raises(PlanError, match="provider_cooldown"):
            restarted.generate("Write a note.", PACKET)
        assert len(requests) == before
        now[0] = status["cooldown_until"] + 1
    assert auth.allowed is True
    assert not instance.status()["usage_paused"]
    assert len([request for request in requests if request.method == "POST"]) == 7


@pytest.mark.parametrize("limit,value,events,code", [
    ("MAX_OUTPUT_CHARS", 5, [delta("too many characters")], "output_limit_exceeded"),
    ("MAX_OUTPUT_CHARS", 5, [completed("too many characters")], "output_limit_exceeded"),
    ("MAX_STREAM_EVENTS", 1, [event("response.created"), completed()], "stream_limit_exceeded"),
    ("MAX_STREAM_BYTES", 10, [completed()], "stream_limit_exceeded"),
])
def test_stream_resource_limits_stop_once_without_retry(tmp_path, monkeypatch, limit, value, events, code):
    monkeypatch.setattr(provider, limit, value)
    instance, requests, _, _ = setup_client(tmp_path, sse(*events))
    with pytest.raises(PlanError, match=code):
        instance.generate("Write a note.", PACKET)
    assert len(requests) == 2


class BrokenStream(httpx.SyncByteStream):
    def __iter__(self):
        yield ("data: " + json.dumps(delta()) + "\n\n").encode()
        raise httpx.ReadError("PRIVATE token, facts and provider details")


def test_interrupted_network_never_returns_partial_text_or_retries(tmp_path):
    response = httpx.Response(200, stream=BrokenStream(), headers={"content-type": "text/event-stream", "x-request-id": "req-broken"})
    instance, requests, _, _ = setup_client(tmp_path, response)
    with pytest.raises(PlanError, match="stream_interrupted") as caught:
        instance.generate("Write a note.", PACKET)
    assert_safe(caught.value)
    assert caught.value.status == 200
    assert caught.value.request_id == "req-broken"
    assert len(requests) == 2


def test_split_unicode_and_crlf_sse_frames(tmp_path):
    text = "Orchard’s supplied note."
    content = ("event: response.completed\r\ndata: " + json.dumps(completed(text), ensure_ascii=False) + "\r\n\r\n").encode()

    class TinyChunks(httpx.SyncByteStream):
        def __iter__(self):
            for byte in content:
                yield bytes([byte])

    instance, _, _, _ = setup_client(tmp_path, httpx.Response(200, stream=TinyChunks(), headers={"content-type": "text/event-stream"}))
    assert instance.generate("Write a note.", PACKET)["text"] == text


def test_elapsed_stream_limit(tmp_path):
    now = [1000.0]

    class SlowStream(httpx.SyncByteStream):
        def __iter__(self):
            now[0] += provider.MAX_STREAM_SECONDS + 1
            yield b"data: {}\n\n"

    instance, requests, _, _ = setup_client(tmp_path, httpx.Response(200, stream=SlowStream(),
        headers={"content-type": "text/event-stream"}), clock=lambda: now[0])
    with pytest.raises(PlanError, match="stream_limit_exceeded"):
        instance.generate("Write a note.", PACKET)
    assert len(requests) == 2


@pytest.mark.parametrize("instructions,packet,schema", [("", PACKET, None), ("Write.", {"hours": float("nan")}, None),
    ("Write.", PACKET, []), ("Write.", {"unsupported": object()}, None), ("Write.", "x" * 65000, None)],
    ids=["empty-instructions", "nan-fact", "schema-array", "non-json-packet", "oversize-packet"])
def test_invalid_input_does_not_open_request(tmp_path, instructions, packet, schema):
    instance, requests, _, _ = setup_client(tmp_path)
    with pytest.raises(PlanError, match="invalid_input"):
        instance.generate(instructions, packet, schema=schema)
    assert len(requests) == 1


def test_corrupt_state_fails_closed_and_relative_state_rejected(tmp_path):
    instance, requests, _, _ = setup_client(tmp_path)
    (tmp_path / "provider.json").write_text("PRIVATE damaged state")
    with pytest.raises(PlanError, match="provider_state_invalid"):
        instance.generate("Write a note.", PACKET)
    assert len(requests) == 1
    with pytest.raises(ValueError, match="absolute"):
        ChatGPTPlanClient(FakeAuth(), ".")


def test_account_switch_during_catalog_fetch_cannot_apply_old_selection(tmp_path):
    auth = FakeAuth()

    def switch_account(request):
        auth.account = "different-account"
        return httpx.Response(200, json={"models": CATALOG})

    client = httpx.Client(transport=httpx.MockTransport(switch_account))
    instance = ChatGPTPlanClient(auth, tmp_path, client=client)
    with pytest.raises(PlanError, match="account_changed"):
        instance.select_model("account-model-z")
    assert instance.status()["selected_model"] is None
    assert not (tmp_path / "provider.json").exists()


def test_catalog_size_is_bounded(tmp_path, monkeypatch):
    instance, _, _, _ = setup_client(tmp_path, select=False)
    monkeypatch.setattr(provider, "MAX_CATALOG_BYTES", 10)
    with pytest.raises(PlanError, match="response_limit_exceeded"):
        instance.models()


def test_non_json_admission_retains_only_string_shape(tmp_path):
    instance, _, _, _ = setup_client(tmp_path, httpx.Response(403, text="PRIVATE detailed restriction"))
    with pytest.raises(PlanError) as caught:
        instance.generate("Write a note.", PACKET)
    assert caught.value.error_shape == "string"
    assert caught.value.status == 403
    assert_safe(caught.value)


@pytest.mark.parametrize("prior_backoff", [False, True], ids=["clean-state", "expired-backoff"])
def test_inflight_success_preserves_other_instances_usage_pause_and_model(tmp_path, prior_backoff):
    now = [1000.0]
    first, first_requests, first_auth, _ = setup_client(tmp_path, clock=lambda: now[0])
    if prior_backoff:
        first._cooldown(first_auth.account, PlanError("http_error", status=503))
        now[0] += 11
    second, _, second_auth, _ = setup_client(tmp_path, httpx.Response(429, json={"error": {
        "code": "subscription_sharing_usage_limit_exceeded"}}), clock=lambda: now[0], select=False)
    second.models()

    def complete_after_other_request_pauses(request):
        with pytest.raises(PlanError, match="subscription_sharing_usage_limit_exceeded"):
            second.generate("Write another note.", PACKET)
        second.select_model("account-model-a")
        return sse(completed())

    first.client = httpx.Client(transport=httpx.MockTransport(complete_after_other_request_pauses))
    assert first.generate("Write a note.", PACKET)["text"] == TEXT
    status = first.status()
    assert status["usage_paused"] is True
    assert status["selected_model"] == "account-model-a"
    before, token_calls = len(first_requests), first_auth.token_calls
    with pytest.raises(PlanError, match="usage_paused"):
        first.generate("Write a note.", PACKET)
    assert len(first_requests) == before
    assert first_auth.token_calls == token_calls
    assert second_auth.allowed is True


def test_inflight_success_preserves_other_instances_newer_cooldown(tmp_path):
    now = [1000.0]
    first, _, auth, _ = setup_client(tmp_path, clock=lambda: now[0])
    first._cooldown(auth.account, PlanError("http_error", status=503))
    now[0] += 11
    second = ChatGPTPlanClient(FakeAuth(), tmp_path, clock=lambda: now[0])

    def complete_after_other_request_backs_off(request):
        second._cooldown(auth.account, PlanError("subscription_sharing_usage_unavailable", status=200))
        return sse(completed())

    first.client = httpx.Client(transport=httpx.MockTransport(complete_after_other_request_backs_off))
    assert first.generate("Write a note.", PACKET)["text"] == TEXT
    assert first.status()["cooldown_until"] == now[0] + 20
    assert first._snapshot(auth.account)["failures"] == 2
    with pytest.raises(PlanError, match="provider_cooldown"):
        first.generate("Write a note.", PACKET)


def test_pause_during_catalog_fetch_prevents_later_post(tmp_path):
    first, requests, auth, _ = setup_client(tmp_path)
    second = ChatGPTPlanClient(FakeAuth(), tmp_path)
    first._catalog.clear()

    def pause_while_fetching_models(request):
        requests.append(request)
        assert request.method == "GET"
        second._cooldown(auth.account, PlanError("subscription_sharing_usage_limit_exceeded", status=429))
        return httpx.Response(200, json={"models": CATALOG})

    first.client = httpx.Client(transport=httpx.MockTransport(pause_while_fetching_models))
    with pytest.raises(PlanError, match="usage_paused"):
        first.generate("Write a note.", PACKET)
    assert all(request.method == "GET" for request in requests)


def _child_inflight_success(directory, now, entered, release, results):
    def respond(request):
        if request.method == "GET":
            return httpx.Response(200, json={"models": CATALOG})
        entered.set()
        if not release.wait(15):
            raise AssertionError("Parent did not release the mocked response")
        return sse(completed())

    try:
        client = httpx.Client(transport=httpx.MockTransport(respond))
        instance = ChatGPTPlanClient(FakeAuth(), directory, client=client, clock=lambda: now)
        results.put({"text": instance.generate("Write a note.", PACKET)["text"], "status": instance.status()})
    except Exception as error:
        results.put({"error": type(error).__name__, "code": getattr(error, "code", None)})


def _child_cooldown_transaction(directory, ready, finished, results):
    try:
        instance = ChatGPTPlanClient(FakeAuth(), directory, clock=lambda: 1000.0)
        ready.set()
        instance._cooldown("account-one", PlanError("http_error", status=503))
        results.put({"status": instance.status()})
    except Exception as error:
        results.put({"error": type(error).__name__, "code": getattr(error, "code", None)})
    finally:
        finished.set()


def test_cross_process_inflight_success_preserves_persisted_usage_pause(tmp_path):
    now = [1000.0]
    instance, _, auth, _ = setup_client(tmp_path, clock=lambda: now[0])
    instance._cooldown(auth.account, PlanError("http_error", status=503))
    now[0] += 11
    context = multiprocessing.get_context("spawn")
    entered, release = context.Event(), context.Event()
    results = context.Queue()
    worker = context.Process(target=_child_inflight_success, args=(str(tmp_path), now[0], entered, release, results))
    try:
        worker.start()
        assert entered.wait(10), "Child did not reach mocked POST"
        instance._cooldown(auth.account, PlanError("subscription_sharing_usage_limit_exceeded", status=429))
        instance.select_model("account-model-a")
        release.set()
        worker.join(10)
        assert worker.exitcode == 0
        result = results.get(timeout=2)
        assert result["text"] == TEXT
        assert result["status"]["usage_paused"] is True
        assert result["status"]["selected_model"] == "account-model-a"
        assert instance.status()["usage_paused"] is True
    finally:
        release.set()
        if worker.is_alive():
            worker.terminate()
            worker.join(5)
        results.close()
        results.join_thread()


def test_native_file_lock_serializes_cross_process_state_transactions(tmp_path):
    instance, _, auth, _ = setup_client(tmp_path)
    context = multiprocessing.get_context("spawn")
    ready, finished = context.Event(), context.Event()
    results = context.Queue()
    worker = context.Process(target=_child_cooldown_transaction, args=(str(tmp_path), ready, finished, results))
    try:
        with instance._transaction() as state:
            entry = instance._entry(state, auth.account)
            entry.update(selected_model="account-model-a", usage_paused=True)
            instance._save(state)
            worker.start()
            assert ready.wait(10), "Child did not start its state transaction"
            assert not finished.wait(0.2), "Another process bypassed the native state lock"
        assert finished.wait(10)
        worker.join(5)
        assert worker.exitcode == 0
        result = results.get(timeout=2)
        assert "error" not in result
        assert result["status"]["selected_model"] == "account-model-a"
        assert result["status"]["usage_paused"] is True
        snapshot = instance._snapshot(auth.account)
        assert snapshot["failures"] == 1
        assert snapshot["cooldown_until"] == 1010.0
    finally:
        if worker.is_alive():
            worker.terminate()
            worker.join(5)
        results.close()
        results.join_thread()


def test_state_saves_use_unique_temporary_files_and_preserve_unrelated_files(tmp_path, monkeypatch):
    instance, _, _, _ = setup_client(tmp_path)
    unrelated = tmp_path / "provider.tmp"
    unrelated.write_text("unrelated existing file")
    names = []
    create = provider.tempfile.mkstemp

    def record_temporary(*args, **kwargs):
        descriptor, name = create(*args, **kwargs)
        names.append(name)
        return descriptor, name

    monkeypatch.setattr(provider.tempfile, "mkstemp", record_temporary)
    instance.resume()
    instance.select_model("account-model-a")
    assert len(names) == 2 and len(set(names)) == 2
    assert all(not provider.Path(name).exists() for name in names)
    assert unrelated.read_text() == "unrelated existing file"


def test_pure_import_does_not_start_http_or_read_state(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Import attempted I/O")

    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.setattr(provider.Path, "read_text", forbidden)
    spec = importlib.util.spec_from_file_location("_provider_import_check", provider.__file__)
    imported = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(imported)
