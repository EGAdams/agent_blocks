"""Sydney's Star Trek Bridge desk: Toyota's routes, Sydney's own identity.

What has to hold: Sydney reaches the same Bridge agents through the same runners
and routes as Toyota, but on her own desk -- her own credential variable (never
Toyota's as a fallback), her own gateway sender recorded in the trace, her own
pending "yes", note revisions and completion notices. A "yes" said to one
receptionist can never send a request the other read back.

Offline: no credential, no network, no model.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.tests.test_sydney_bridge
"""
import os
from types import SimpleNamespace

from tests.fakes import ScriptedModel

from ..adapters.routing_model import RoutingModel
from ..bridge_client import desk
from ..bridge_client.config import load_client_token
from ..bridge_client.errors import BridgeUnavailable
from ..bridge_client.models import BridgeActionRequest
from ..bridge_client.tracing import TracingBridgeClient
from ..profiles import SYDNEY, TOYOTA
from ..roles import BRIDGE_ROLE_NAMES
from ..sydney_roles import SYDNEY_ROSTER, SYDNEY_SYSTEM_PROMPT

CREDENTIAL_VARS = ("TOYOTA_BRIDGE_CLIENT_TOKEN", "TOYOTA_BRIDGE_CLIENT_TOKEN_FILE",
                   "SYDNEY_BRIDGE_CLIENT_TOKEN", "SYDNEY_BRIDGE_CLIENT_TOKEN_FILE")


def _request():
    return BridgeActionRequest(agent="printer", workspace="printer", operation="print",
                               prompt="{}", approval_mode="device_control", idempotency_key="k-1")


async def test_each_receptionist_has_her_own_desk():
    toyota, sydney = desk.desk_for("toyota"), desk.desk_for("sydney")
    assert toyota.pending is desk.PENDING and toyota.notices is desk.NOTICES
    for part in ("pending", "note_revisions", "note_active_items", "notices", "client_factory", "files_factory"):
        assert getattr(toyota, part) is not getattr(sydney, part), part
    assert sydney.new_key().startswith("sydney-") and toyota.new_key().startswith("toyota-")


async def test_sydney_never_borrows_toyotas_credential():
    env = {"TOYOTA_BRIDGE_CLIENT_TOKEN": "toyota-secret-token"}
    assert load_client_token(env) == "toyota-secret-token"
    try:
        load_client_token(env, prefix="SYDNEY")
        raise AssertionError("Sydney used Toyota's token")
    except BridgeUnavailable as exc:
        assert exc.code == "no_credential"
    assert load_client_token({"SYDNEY_BRIDGE_CLIENT_TOKEN": "s-tok"}, prefix="SYDNEY") == "s-tok"
    try:
        load_client_token({}, prefix="sydney; rm")
        raise AssertionError("a bad prefix was accepted")
    except BridgeUnavailable as exc:
        assert exc.code == "bad_config"


async def test_her_submits_are_traced_under_her_own_kind():
    recorded = []

    class Tracer:
        def begin(self, kind, object_id, **kw):
            recorded.append((kind, object_id))
            return SimpleNamespace(succeed=lambda *a, **k: None, fail=lambda *a, **k: None)

    inner = SimpleNamespace(submit=lambda r: SimpleNamespace(task_id="t_" + "a" * 24, state="queued", replayed=False))
    TracingBridgeClient(inner, tracer_factory=Tracer, kind="sydney.bridge_submit").submit(_request())
    assert recorded == [("sydney.bridge_submit", "sydney.bridge_submit:t_" + "a" * 24)]


async def test_sydney_routes_to_every_bridge_specialist_on_her_desk():
    runners = SYDNEY.build_runner()._runners
    sydney = desk.desk_for("sydney")
    for name in BRIDGE_ROLE_NAMES:
        assert SYDNEY_ROSTER.has(name) and name in runners, name
        assert f"ROUTE: {name}" in SYDNEY_SYSTEM_PROMPT or name in SYDNEY_ROSTER.allowed_agents(), name
    assert runners["star_trek_bridge"]._notices is sydney.notices
    assert runners["star_trek_bridge"]._client_factory is sydney.client_factory
    writer = runners["bridge_writer"]
    assert writer._pending is sydney.pending and writer._notices is sydney.notices
    assert writer._files_factory is sydney.files_factory and writer._key_factory().startswith("sydney-")
    assert writer._revisions() is sydney.note_revisions
    # Toyota's runners still use Toyota's desk.
    toyota_writer = TOYOTA.build_runner()._runners["bridge_writer"]
    assert toyota_writer._pending is desk.PENDING and toyota_writer._key_factory().startswith("toyota-")


async def test_a_yes_to_one_receptionist_never_sends_the_others_request():
    toyota, sydney = desk.desk_for("toyota"), desk.desk_for("sydney")
    toyota.pending.put("bridge_printer", _request(), "Print it?")
    try:
        assert SYDNEY.route_override.forced_agent("USER: yes\nAGENT:") is None
        assert TOYOTA.route_override.forced_agent("USER: yes\nAGENT:") == "bridge_printer"
    finally:
        toyota.pending.take()
    sydney.pending.put("bridge_printer", _request(), "Print it?")
    try:
        assert SYDNEY.route_override.forced_agent("USER: yes\nAGENT:") == "bridge_printer"
        assert TOYOTA.route_override.forced_agent("USER: yes\nAGENT:") != "bridge_printer"
    finally:
        sydney.pending.take()


async def test_her_bridge_route_reports_unavailable_without_her_own_credential():
    saved = {k: os.environ.pop(k) for k in CREDENTIAL_VARS if k in os.environ}
    os.environ["TOYOTA_BRIDGE_CLIENT_TOKEN"] = "toyota-only-token"   # Toyota's must not be used
    try:
        routing = RoutingModel(ScriptedModel(["ROUTE: star_trek_bridge | which agents are on the bridge"]),
                               model="fake", roster=SYDNEY_ROSTER, runner=SYDNEY.build_runner(), speaker="Sydney")
        reply = await routing.complete("USER: which agents are on the bridge\nAGENT:")
        assert "isn't available" in reply.text and "Nothing was sent" in reply.text, reply.text
    finally:
        os.environ.pop("TOYOTA_BRIDGE_CLIENT_TOKEN", None)
        os.environ.update(saved)


async def test_her_alerts_include_her_own_bridge_notices():
    inbox = SYDNEY.build_alerts()
    assert type(inbox).__name__ == "CombinedAlerts"
    assert desk.desk_for("sydney").notices in inbox._inboxes
    assert desk.NOTICES not in inbox._inboxes


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))
