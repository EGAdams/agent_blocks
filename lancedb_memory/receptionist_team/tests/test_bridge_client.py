"""Contract tests for Toyota's narrow Star Trek Bridge client (spec Phase 4, item 5).

Offline: a fake transport for the client and a fake urllib opener for the
transport, so no socket is opened and no credential is needed.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.tests.test_bridge_client
"""
import io
import json
import socket
import tempfile
import urllib.error
from pathlib import Path

from pydantic import ValidationError

from ..bridge_client import errors as e
from ..bridge_client.client import BridgeClient
from ..bridge_client.config import load_client_config, load_client_token, load_policy
from ..bridge_client.models import BridgeClientConfig, BridgePolicy, BridgeRoute, BridgeTaskRequest
from ..bridge_client.transport import USER_AGENT, HttpsBridgeTransport, _NoRedirect

TOKEN = "tok_" + "a" * 44
TID = "t_" + "0123456789abcdef01234567"
VIEW = {"task_id": TID, "state": "queued", "agent": "codex", "workspace": "agent_blocks",
        "operation": "inspect", "approval_mode": "read_only", "attempts": 0}


def _request(**over):
    fields = dict(agent="codex", workspace="agent_blocks", operation="inspect", prompt="Say hello world.",
                  idempotency_key="toyota-abc")
    fields.update(over)
    return BridgeTaskRequest(**fields)


class FakeTransport:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class FakeResponse(io.BytesIO):
    def __init__(self, data, status=200):
        super().__init__(data)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    def __init__(self, outcome):
        self.outcome = outcome
        self.requests = []

    def open(self, req, timeout=None):
        self.requests.append(req)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _http_error(status, body):
    return urllib.error.HTTPError("https://x", status, "err", {}, io.BytesIO(body))


def _transport(outcome, **cfg):
    opener = FakeOpener(outcome)
    return HttpsBridgeTransport(BridgeClientConfig(**cfg), TOKEN, opener=opener), opener


def _raises(exc_type, fn):
    try:
        fn()
    except exc_type as exc:
        return exc
    raise AssertionError(f"expected {exc_type.__name__}")


# -- request model -----------------------------------------------------------
async def test_submit_body_has_exactly_the_contract_fields():
    body = _request().body()
    assert body == {"schema_version": "1.0", "task_type": "agent_task", "agent": "codex",
                    "workspace": "agent_blocks", "operation": "inspect", "prompt": "Say hello world.",
                    "approval_mode": "read_only", "idempotency_key": "toyota-abc"}, body


async def test_unknown_fields_implement_and_write_mode_are_rejected():
    _raises(ValidationError, lambda: _request(extra_field=1))
    _raises(ValidationError, lambda: _request(operation="implement"))
    _raises(ValidationError, lambda: _request(approval_mode="workspace_write"))
    _raises(ValidationError, lambda: _request(workspace="../etc"))
    _raises(ValidationError, lambda: _request(prompt="   "))
    _raises(ValidationError, lambda: _request(prompt="x" * 8001))
    _raises(ValidationError, lambda: _request(idempotency_key="has space"))
    _raises(ValidationError, lambda: BridgeRoute(agent="codex", workspace="agent_blocks", operation="implement"))


async def test_shipped_policy_defaults_to_codex_agent_blocks_and_is_read_only():
    policy = load_policy()
    assert (policy.default_agent, policy.default_workspace, policy.approval_mode) == (
        "codex", "agent_blocks", "read_only")
    assert set(policy.allowed_operations) <= {"inspect", "explain", "search_memory"}
    for bad in ({"allowed_operations": ["implement"]}, {"allowed_operations": ["print"]},
                {"allowed_operations": []}, {"approval_mode": "workspace_write"}, {"surprise": 1}):
        _raises(ValidationError, lambda: BridgePolicy(default_agent="codex", default_workspace="agent_blocks", **bad))


# -- config -----------------------------------------------------------------
async def test_config_is_https_only_and_never_the_worker_api():
    assert load_client_config({}).base_url == "https://bridge.americansjewelry.com/api/agent-bridge/v1"
    for bad in ("http://bridge.americansjewelry.com/api/agent-bridge/v1",
                "https://user:pw@bridge.americansjewelry.com/api",
                "https://bridge.americansjewelry.com/api/agent-bridge/v1/worker"):
        _raises(e.BridgeUnavailable, lambda: load_client_config({"TOYOTA_BRIDGE_URL": bad}))


async def test_missing_credential_is_unavailable_and_worker_token_is_ignored():
    exc = _raises(e.BridgeUnavailable, lambda: load_client_token({"BRIDGE_WORKER_TOKEN": TOKEN,
                                                                    "BRIDGE_WORKER_TOKEN_FILE": "/x"}))
    assert exc.code == "no_credential"
    _raises(e.BridgeUnavailable, lambda: load_client_token({"TOYOTA_BRIDGE_CLIENT_TOKEN_FILE": "/nonexistent/x"}))
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "client_token"
        path.write_text(TOKEN + "\n")
        assert load_client_token({"TOYOTA_BRIDGE_CLIENT_TOKEN_FILE": str(path)}) == TOKEN
    assert load_client_token({"TOYOTA_BRIDGE_CLIENT_TOKEN": TOKEN}) == TOKEN
    _raises(e.BridgeUnavailable, lambda: load_client_token({"TOYOTA_BRIDGE_CLIENT_TOKEN": "two words"}))


# -- client -----------------------------------------------------------------
async def test_discover_submit_status_result_and_cancel_use_only_the_public_paths():
    result = {"task_id": TID, "state": "succeeded", "ready": True, "ok": True, "body": "Hello World"}
    fake = FakeTransport((200, {"agents": [{"name": "codex", "operations": ["inspect"], "workspaces": ["agent_blocks"]}]}),
                         (201, VIEW), (200, VIEW), (200, result))
    client = BridgeClient(fake)
    assert client.discover()[0].name == "codex"
    assert client.submit(_request()).task_id == TID
    assert client.status(TID).state == "queued"
    assert client.result(TID).succeeded
    assert [(m, p) for m, p, _ in fake.calls] == [
        ("GET", "/agents"), ("POST", "/tasks"), ("GET", f"/tasks/{TID}"), ("GET", f"/tasks/{TID}/result")]
    cancelled = BridgeClient(FakeTransport((200, {**VIEW, "cancel_requested": True})))
    assert cancelled.cancel(TID).cancel_requested
    _raises(e.BridgeRefused, lambda: cancelled.cancel("t_not-an-id"))


async def test_not_ready_is_never_success():
    fake = FakeTransport((200, {"task_id": TID, "state": "running", "ready": False}))
    result = BridgeClient(fake).result(TID)
    assert not result.ready and not result.succeeded


async def test_failed_result_is_not_success():
    fake = FakeTransport((200, {"task_id": TID, "state": "failed", "ready": True, "ok": False, "last_error": "x"}))
    assert not BridgeClient(fake).result(TID).succeeded


async def test_malformed_and_mismatched_responses_are_refused():
    _raises(e.BridgeMalformed, lambda: BridgeClient(FakeTransport((200, {"nope": 1}))).discover())
    _raises(e.BridgeMalformed, lambda: BridgeClient(FakeTransport((200, {"task_id": "bad"}))).status(TID))
    other = dict(VIEW, workspace="digi")
    _raises(e.BridgeMalformed, lambda: BridgeClient(FakeTransport((201, other))).submit(_request()))
    _raises(e.BridgeMalformed, lambda: BridgeClient(FakeTransport((202, VIEW))).submit(_request()))
    # ready=True with a non-terminal state contradicts the contract
    _raises(e.BridgeMalformed, lambda: BridgeClient(FakeTransport(
        (200, {"task_id": TID, "state": "running", "ready": True, "ok": True}))).result(TID))
    _raises(e.BridgeRefused, lambda: BridgeClient(FakeTransport()).status("t_../../worker"))


# -- transport --------------------------------------------------------------
async def test_transport_sends_bearer_user_agent_and_json():
    transport, opener = _transport(FakeResponse(json.dumps(VIEW).encode(), 201))
    status, data = transport.request("POST", "/tasks", _request().body())
    req = opener.requests[0]
    assert status == 201 and data["task_id"] == TID
    assert req.full_url == "https://bridge.americansjewelry.com/api/agent-bridge/v1/tasks"
    assert req.get_header("Authorization") == f"Bearer {TOKEN}"
    assert req.get_header("User-agent") == USER_AGENT
    assert json.loads(req.data)["operation"] == "inspect"


async def test_redirects_are_not_followed():
    assert _NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://elsewhere") is None
    transport, _ = _transport(_http_error(302, b""))
    assert _raises(e.BridgeMalformed, lambda: transport.request("GET", "/agents")).code == "redirect"


async def test_auth_errors_are_typed_and_never_echo_the_token():
    echo = json.dumps({"error": {"code": "unauthenticated",
                                 "message": f"bad header Authorization: Bearer {TOKEN}"}}).encode()
    transport, _ = _transport(_http_error(401, echo))
    exc = _raises(e.BridgeAuthError, lambda: transport.request("GET", "/agents"))
    assert TOKEN not in str(exc) and TOKEN not in exc.message, exc.message
    transport, _ = _transport(_http_error(403, b'{"error":{"code":"wrong_role","message":"client role required"}}'))
    assert _raises(e.BridgeAuthError, lambda: transport.request("GET", "/agents")).code == "wrong_role"


async def test_a_policy_refusal_is_not_a_credential_error():
    body = b'{"error":{"code":"not_allowed","message":"this client may not start scans (scanner:start)"}}'
    transport, _ = _transport(_http_error(403, body))
    exc = _raises(e.BridgeRefused, lambda: transport.request("POST", "/tasks", {}))
    assert not isinstance(exc, e.BridgeAuthError)
    assert exc.code == "not_allowed" and "scanner:start" in exc.message


async def test_refusals_server_errors_network_and_timeouts():
    transport, _ = _transport(_http_error(422, b'{"error":{"code":"invalid_task","message":"bad"}}'))
    assert _raises(e.BridgeRefused, lambda: transport.request("POST", "/tasks", {})).code == "invalid_task"
    transport, _ = _transport(_http_error(500, b"<html>oops</html>"))
    assert isinstance(_raises(e.BridgeAmbiguous, lambda: transport.request("GET", "/agents")), e.BridgeAmbiguous)
    transport, _ = _transport(urllib.error.URLError("Temporary failure in name resolution"))
    assert _raises(e.BridgeAmbiguous, lambda: transport.request("GET", "/agents")).code == "unreachable"
    transport, _ = _transport(socket.timeout("timed out"))
    _raises(e.BridgeTimeout, lambda: transport.request("GET", "/agents"))


async def test_bodies_are_bounded_and_must_be_json():
    transport, _ = _transport(FakeResponse(b"x" * 2000), max_response_bytes=1000)
    assert _raises(e.BridgeMalformed, lambda: transport.request("GET", "/agents")).code == "too_large"
    transport, _ = _transport(FakeResponse(b"<html>"))
    assert _raises(e.BridgeMalformed, lambda: transport.request("GET", "/agents")).code == "not_json"
    transport, _ = _transport(FakeResponse(b"{}"))
    _raises(e.BridgeRefused, lambda: transport.request("POST", "/tasks", {"prompt": "x" * 70000}))


async def test_worker_paths_are_refused_before_any_request():
    transport, opener = _transport(FakeResponse(b"{}"))
    for path in ("/worker/tasks/next", "//evil", "/tasks/../worker"):
        _raises(e.BridgeUnavailable, lambda: transport.request("GET", path))
    assert opener.requests == []


async def test_sanitize_strips_bearer_and_known_secret():
    out = e.sanitize(f"Bearer abc.def and {TOKEN} " + "y" * 500, (TOKEN,))
    assert "abc.def" not in out and TOKEN not in out and len(out) <= e.MESSAGE_LIMIT


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))
