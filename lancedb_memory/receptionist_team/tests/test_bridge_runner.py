"""BridgeDelegationRunner outcomes over a fake client (spec Phase 3/4).

Offline: no transport, no credential, no sleeping.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.tests.test_bridge_runner
"""
from ..adapters.bridge_runner import SPOKEN_LIMIT, BridgeDelegationRunner
from ..bridge_client import errors as e
from ..bridge_client.models import BridgeAgent, BridgePolicy, BridgeResult, BridgeTaskView
from ..bridge_client.workflow import PHASES

TID = "t_" + "abcdef0123456789abcdef01"
POLICY = BridgePolicy(default_agent="codex", default_workspace="agent_blocks")
CODEX = BridgeAgent(name="codex", operations=["inspect", "explain"], workspaces=["agent_blocks", "digi"])


def _view(state, agent="codex"):
    return BridgeTaskView(task_id=TID, state=state, agent=agent, workspace="agent_blocks", operation="inspect")


def _result(state, ready=True, ok=None, body=None, last_error=None):
    return BridgeResult(task_id=TID, state=state, ready=ready, ok=ok, body=body, last_error=last_error)


class FakeClient:
    def __init__(self, agents=(CODEX,), submits=(), states=(), result=None):
        self.agents = list(agents)
        self.submits = list(submits)
        self.states = list(states)
        self._result = result
        self.sent = []

    def discover(self):
        return self.agents

    def submit(self, request):
        self.sent.append(request)
        answer = self.submits.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def status(self, task_id):
        return _view(self.states.pop(0)) if self.states else _view("queued")

    def result(self, task_id):
        return self._result


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _runner(client, policy=POLICY):
    clock = Clock()
    factory = (lambda: (_ for _ in ()).throw(client)) if isinstance(client, Exception) else (lambda: client)
    return BridgeDelegationRunner(client_factory=factory, policy_factory=lambda: policy, wait_seconds=15,
                                  poll_seconds=5, sleep=clock.sleep, clock=clock, key_factory=lambda: "toyota-k1",
                                  log_factory=lambda: Log())


class Log:
    records = []

    def record(self, record):
        Log.records.append(record)


async def _say(runner, text):
    return (await runner.run(None, "star_trek_bridge", "m", text)).text


async def test_ambiguous_submission_reuses_its_key_on_next_identical_request():
    client = FakeClient(submits=[e.BridgeAmbiguous("uncertain", "timeout") for _ in range(3)] + [_view("queued")])
    keys = iter(["first-key", "second-key"])
    runner = _runner(client)
    runner._key_factory = lambda: next(keys)
    await _say(runner, "Say Hello World.")
    await _say(runner, "Say Hello World.")
    assert len(client.sent) == 4
    assert {r.idempotency_key for r in client.sent} == {"first-key"}


async def test_machine_ops_receives_exact_json_without_spoken_prompt_frame():
    machine = BridgeAgent(name="machine-ops", operations=["inspect"], workspaces=["machine_ops"])
    machine_view = BridgeTaskView(task_id=TID, state="queued", agent="machine-ops", workspace="machine_ops", operation="inspect")
    client = FakeClient(agents=[machine], submits=[machine_view])
    runner = _runner(client)
    runner._wait = 0
    raw = '{"operation":"status","unit":"adw-supervisor.service"}'
    await _say(runner, "machine-ops@machine_ops: " + raw)
    assert client.sent[0].prompt == raw


async def test_success_reads_back_the_body_and_sends_a_read_only_inspect():
    client = FakeClient(submits=[_view("queued")], states=["running", "succeeded"],
                        result=_result("succeeded", ok=True, body="Hello World"))
    text = await _say(_runner(client), "Say Hello World.")
    assert "Hello World" in text and TID in text, text
    sent = client.sent[0]
    assert (sent.agent, sent.workspace, sent.operation, sent.approval_mode) == (
        "codex", "agent_blocks", "inspect", "read_only")
    assert "Do not modify" in sent.prompt and sent.prompt.endswith("Say Hello World.")


async def test_pending_is_reported_with_the_task_id_never_as_done():
    runner = _runner(FakeClient(submits=[_view("queued")]))
    text = await _say(runner, "look at roles.py")
    assert TID in text and "queued" in text and "not finished" in text, text
    assert runner.last_task_id == TID


async def test_failed_and_cancelled_are_said_as_such():
    failed = FakeClient(submits=[_view("failed")], result=_result("failed", ok=False, last_error="codex exited 1"))
    assert "failed: codex exited 1" in await _say(_runner(failed), "x")
    cancelled = FakeClient(submits=[_view("cancelled")], result=_result("cancelled", ok=False))
    assert "cancelled" in await _say(_runner(cancelled), "x")


async def test_ambiguous_submit_retries_with_the_same_key_then_admits_it_cannot_tell():
    client = FakeClient(submits=[e.BridgeAmbiguous("unreachable"), _view("succeeded")],
                        result=_result("succeeded", ok=True, body="hi"))
    assert "hi" in await _say(_runner(client), "x")
    assert [r.idempotency_key for r in client.sent] == ["toyota-k1", "toyota-k1"]
    client = FakeClient(submits=[e.BridgeTimeout("timeout")] * 3)
    text = await _say(_runner(client), "x")
    assert "can't tell whether it arrived" in text and len({r.idempotency_key for r in client.sent}) == 1


async def test_missing_credential_auth_and_malformed_send_nothing_and_say_so():
    assert "Nothing was sent" in await _say(_runner(e.BridgeUnavailable("no_credential", "no client credential is configured")), "x")
    client = FakeClient(submits=[e.BridgeAuthError("unauthenticated", "", 401)])
    assert "refused my credential" in await _say(_runner(client), "x")
    client = FakeClient(submits=[e.BridgeMalformed("malformed")])
    assert "couldn't read" in await _say(_runner(client), "x")


async def test_an_unlisted_route_is_not_submitted():
    for agents in ([], [BridgeAgent(name="codex", operations=["implement"], workspaces=["agent_blocks"])],
                   [BridgeAgent(name="codex", operations=["inspect"], workspaces=["letta_code"],
                                write_workspaces=["letta_code"])]):
        client = FakeClient(agents=agents)
        assert "didn't send anything" in await _say(_runner(client), "x"), agents
        assert client.sent == []


async def test_status_and_result_readback_by_id_and_by_last_task():
    client = FakeClient(states=["running"], result=_result("running", ready=False))
    runner = _runner(client)
    assert "running" in await _say(runner, f"status {TID}")
    assert "no result yet" in await _say(runner, f"result {TID}")
    assert "Which Bridge task" in await _say(runner, "status")
    assert client.sent == []


async def test_reworded_readbacks_with_a_task_id_send_nothing():
    for line, said in ((f"What is the result of Star Trek Bridge task {TID}?", "no result yet"),
                       (f"Can you tell me what Bridge task {TID} said", "no result yet"),
                       (f"Is task {TID} still running on the bridge?", "running")):
        client = FakeClient(states=["running"], result=_result("running", ready=False))
        text = await _say(_runner(client), line)
        assert said in text and client.sent == [], (line, text)


async def test_a_task_id_without_a_question_about_it_is_still_a_submit():
    client = FakeClient(submits=[_view("queued")])
    await _say(_runner(client), f"explain how the gateway assigns ids like {TID}")
    assert len(client.sent) == 1


async def test_long_answers_are_clipped_for_speech():
    client = FakeClient(submits=[_view("succeeded")], result=_result("succeeded", ok=True, body="w " * 2000))
    assert len(await _say(_runner(client), "x")) <= SPOKEN_LIMIT


# -- dynamic agents, policy and phases ---------------------------------------
HELLO = BridgeAgent(name="hello-world", operations=["inspect"], workspaces=["agent_blocks"])
WRITER = BridgeAgent(name="codex-writer", operations=["implement"], workspaces=["bridge_sandbox", "letta_code"],
                     write_workspaces=["bridge_sandbox", "letta_code"])
PRINTER = BridgeAgent(name="printer", operations=["print", "print_status"], workspaces=["printers"])
LIVE = (CODEX, WRITER, HELLO, PRINTER)


async def test_a_named_bridge_agent_gets_the_task_with_its_own_read_only_route():
    client = FakeClient(agents=LIVE, submits=[_view("succeeded", agent="hello-world")],
                        result=_result("succeeded", ok=True, body="Hello World"))
    text = await _say(_runner(client), "hello-world: say hello")
    assert "Hello World" in text and TID in text, text
    sent = client.sent[0]
    assert (sent.agent, sent.workspace, sent.operation, sent.approval_mode) == (
        "hello-world", "agent_blocks", "inspect", "read_only")
    assert sent.prompt.endswith("say hello")


async def test_an_agent_added_to_the_bridge_later_is_found_without_a_code_change():
    newcomer = BridgeAgent(name="librarian", operations=["search_memory", "inspect"], workspaces=["notes"])
    client = FakeClient(agents=(*LIVE, newcomer), submits=[_view("queued", agent="librarian")])
    listing = await _say(_runner(FakeClient(agents=(*LIVE, newcomer))), "which agents are on the bridge")
    assert "librarian" in listing and "hello-world" in listing, listing
    await _say(_runner(client), "librarian: what do my notes say about taxes")
    sent = client.sent[0]
    # policy order (inspect first), the agent's only workspace, read-only
    assert (sent.agent, sent.workspace, sent.operation, sent.approval_mode) == (
        "librarian", "notes", "inspect", "read_only")


async def test_listing_names_who_can_and_cannot_be_used_and_sends_nothing():
    client = FakeClient(agents=LIVE)
    text = await _say(_runner(client), "list agents")
    assert "4 agents" in text and "read-only requests to codex and hello-world" in text, text
    assert "codex-writer and printer do work I'm not allowed to start" in text, text
    assert client.sent == []


async def test_write_print_and_scan_agents_are_refused_never_escalated():
    for text in ("codex-writer: add a file", "printer: print the report", "codex/implement: change roles.py",
                 "codex-writer@bridge_sandbox: write hello.md"):
        client = FakeClient(agents=LIVE, submits=[_view("succeeded")])
        reply = await _say(_runner(client), text)
        assert "didn't send anything" in reply, (text, reply)
        assert client.sent == [], text


async def test_named_workspace_and_operation_are_checked_against_the_live_list():
    client = FakeClient(agents=LIVE, submits=[_view("queued")])
    assert "doesn't work in scanners" in await _say(_runner(client), "codex@scanners: x")
    assert "doesn't offer search_memory" in await _say(_runner(client), "hello-world/search_memory: x")
    assert "doesn't list an agent called ghost" in await _say(_runner(client), "ghost@agent_blocks: x")
    assert client.sent == []
    writable = BridgeAgent(name="codex", operations=["inspect"], workspaces=["agent_blocks", "letta_code"],
                           write_workspaces=["letta_code"])
    client = FakeClient(agents=[writable], submits=[_view("queued")])
    assert "can write in letta_code" in await _say(_runner(client), "codex@letta_code: x")
    assert client.sent == []


async def test_a_policy_that_drops_an_operation_refuses_it_even_if_the_agent_offers_it():
    narrow = BridgePolicy(default_agent="codex", default_workspace="agent_blocks", allowed_operations=("inspect",))
    client = FakeClient(agents=LIVE, submits=[_view("queued")])
    assert "explain isn't one" in await _say(_runner(client, narrow), "codex/explain: x")
    assert client.sent == []


async def test_unaddressed_text_with_a_colon_still_goes_to_the_default_agent():
    client = FakeClient(agents=LIVE, submits=[_view("queued")])
    await _say(_runner(client), "Note: what does roles.py do?")
    assert client.sent[0].agent == "codex" and client.sent[0].prompt.endswith("Note: what does roles.py do?")


async def test_every_phase_is_recorded_in_order_with_status_and_no_prompt_text():
    Log.records = []
    client = FakeClient(agents=LIVE, submits=[_view("queued")], states=["succeeded"],
                        result=_result("succeeded", ok=True, body="secret-free answer"))
    runner = _runner(client)
    await _say(runner, "hello-world: tell me a SECRETWORD")
    assert [r.name for r in runner.last_trace] == list(PHASES), [r.name for r in runner.last_trace]
    assert all(r.status == "ok" for r in runner.last_trace), runner.last_trace
    assert runner.last_trace[3].outputs["task_id"] == TID
    assert not any("SECRETWORD" in r.line() for r in Log.records)
    refused = _runner(FakeClient(agents=LIVE))
    await _say(refused, "printer: print it")
    assert [(r.name, r.status) for r in refused.last_trace] == [
        ("discover", "ok"), ("validate", "stopped"), ("respond", "ok")]


async def test_bridge_down_is_one_failed_discover_then_a_spoken_reply():
    client = FakeClient(agents=LIVE)
    client.discover = lambda: (_ for _ in ()).throw(e.BridgeAmbiguous("unreachable", "connection refused"))
    runner = _runner(client)
    text = await _say(runner, "hello-world: hi")
    assert "couldn't reach the Star Trek Bridge" in text and client.sent == [], text
    assert [(r.name, r.status) for r in runner.last_trace] == [("discover", "failed"), ("respond", "ok")]


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))
