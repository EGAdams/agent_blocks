"""Toyota's Bridge specialists: form -> checked request, read-back + yes/no,
cancel, finish notices. Offline: fake client and form filler, no sleeping.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.tests.test_bridge_specialists
"""
import json

from ..adapters.bridge_confirmation_route import BridgeConfirmationRoute
from ..adapters.bridge_specialist_runner import BridgeSpecialistRunner
from ..bridge_client import errors as e
from ..bridge_client.desk import (BridgeCompletionNotices, CombinedAlerts, PendingBridgeRequests, yes_or_no)
from ..bridge_client.models import BridgeActionRequest, BridgeAgent, BridgeResult, BridgeTaskView
from ..bridge_client.specialists import SPECIALISTS, FormProblem, SpecialistForm
from ..bridge_client.workflow import readback_of

TID = "t_" + "abcdef0123456789abcdef01"
WRITER = BridgeAgent(name="codex-writer", operations=["implement"],
                     workspaces=["bridge_sandbox", "letta_code"], write_workspaces=["bridge_sandbox", "letta_code"])
SONNET = BridgeAgent(name="claude-sonnet", operations=["inspect", "explain", "implement"],
                     workspaces=["agent_blocks", "bridge_sandbox"], write_workspaces=["bridge_sandbox"])
PRINTER = BridgeAgent(name="printer", operations=["print", "print_cancel", "print_status", "print_discover"],
                      workspaces=["printers"])
SCANNER = BridgeAgent(name="scanner", operations=["scan"], workspaces=["scanners"])
NOTES = BridgeAgent(name="notes", operations=["inspect", "implement"], workspaces=["notes"], write_workspaces=["notes"])
LIVE = [WRITER, SONNET, PRINTER, SCANNER, NOTES]


def _view(state, op="implement"):
    return BridgeTaskView(task_id=TID, state=state, agent="x", workspace="w", operation=op)


class FakeClient:
    def __init__(self, submits=(), states=(), result=None, cancel_state="cancelled"):
        self.submits, self.states, self._result = list(submits), list(states), result
        self.sent, self.cancelled, self.cancel_state = [], [], cancel_state

    def discover(self):
        return LIVE

    def submit(self, request):
        self.sent.append(request)
        return self.submits.pop(0)

    def status(self, task_id):
        return _view(self.states.pop(0)) if self.states else _view("running")

    def result(self, task_id):
        return self._result

    def cancel(self, task_id):
        self.cancelled.append(task_id)
        return _view(self.cancel_state)


class Filler:
    def __init__(self, *forms):
        self.forms, self.calls = list(forms), []

    def fill(self, system_prompt, request):
        self.calls.append(request)
        form = self.forms.pop(0)
        if isinstance(form, Exception):
            raise form
        return json.dumps(form)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Notices:
    def __init__(self):
        self.tracked = []

    def track(self, task_id, label):
        self.tracked.append((task_id, label))


def _runner(name, client, filler, pending=None, notices=None):
    clock = Clock()
    return BridgeSpecialistRunner(name, filler=filler, pending=pending or PendingBridgeRequests(clock=clock),
                                  notices=notices or Notices(), client_factory=lambda: client,
                                  wait_seconds=15, poll_seconds=5, sleep=clock.sleep, clock=clock,
                                  key_factory=lambda: "toyota-k1", log_factory=lambda: None)


async def _say(runner, text):
    return (await runner.run(None, runner._specialist.name, "m", text)).text


# -- builders ---------------------------------------------------------------
async def test_writer_builds_implement_in_a_write_workspace_and_refuses_others():
    b = SPECIALISTS["bridge_writer"].builder
    req, said = b.build(SpecialistForm(instructions="make hello.txt"), WRITER)
    assert (req.operation, req.workspace, req.approval_mode) == ("implement", "bridge_sandbox", "workspace_write")
    assert req.needs_confirmation and "make hello.txt" in said and req.prompt.endswith("make hello.txt")
    for form in (SpecialistForm(instructions="x", workspace="agent_blocks"),
                 SpecialistForm(instructions="x", operation="print"), SpecialistForm()):
        try:
            b.build(form, WRITER)
            raise AssertionError(f"built {form}")
        except FormProblem:
            pass


async def test_coder_read_only_ops_go_straight_and_never_write():
    req, _ = SPECIALISTS["bridge_coder"].builder.build(
        SpecialistForm(operation="inspect", workspace="agent_blocks", instructions="what is roles.py"), SONNET)
    assert (req.approval_mode, req.needs_confirmation) == ("read_only", False)
    assert "Do not modify" in req.prompt


async def test_printer_and_scanner_prompts_are_built_by_code():
    b = SPECIALISTS["bridge_printer"].builder
    req, said = b.build(SpecialistForm(operation="print", text="Staff meeting at 3", copies=2), PRINTER)
    assert json.loads(req.prompt) == {"kind": "text", "text": "Staff meeting at 3", "options": {"copies": 2}}
    assert req.approval_mode == "device_control" and req.needs_confirmation and "2 copies" in said
    assert b.build(SpecialistForm(operation="print_discover"), PRINTER)[0].prompt == "discover"
    assert b.build(SpecialistForm(operation="print_status"), PRINTER)[0].prompt == "recent"
    assert b.build(SpecialistForm(operation="print_cancel", job_id=TID), PRINTER)[0].prompt == f"cancel={TID}"
    for bad in (SpecialistForm(operation="print", text="x", copies=11), SpecialistForm(operation="print_cancel", job_id="7")):
        try:
            b.build(bad, PRINTER)
            raise AssertionError(bad)
        except FormProblem:
            pass
    req, said = SPECIALISTS["bridge_scanner"].builder.build(SpecialistForm(scanner="Freezer"), SCANNER)
    assert req.prompt == "scanner=freezer" and "Scala" in said and req.needs_confirmation


async def test_notes_builder_makes_the_marker_and_reads_back_every_write():
    import re
    b = SPECIALISTS["bridge_notes"].builder
    req, said = b.build(SpecialistForm(operation="marker_write", marker="NOTESDIAG-000000000000"), NOTES)
    body = json.loads(req.prompt)
    assert body["notes"] == "marker_write" and re.fullmatch(r"NOTESDIAG-[0-9a-f]{12}", body["marker"])
    assert body["marker"] != "NOTESDIAG-000000000000"               # the model never picks the marker it writes
    assert req.operation == "implement" and req.workspace == "notes" and req.needs_confirmation
    assert body["marker"] in said
    other = json.loads(b.build(SpecialistForm(operation="marker_write"), NOTES)[0].prompt)["marker"]
    assert other != body["marker"]
    req, said = b.build(SpecialistForm(operation="status", text="is NOTESDIAG-0123456789ab on the page?"), NOTES)
    assert json.loads(req.prompt) == {"notes": "status", "marker": "NOTESDIAG-0123456789ab"}
    assert req.operation == "inspect" and not req.needs_confirmation and said == ""
    req, said = b.build(SpecialistForm(operation="marker_clear"), NOTES)
    assert json.loads(req.prompt) == {"notes": "marker_clear"} and req.needs_confirmation and said
    for bad in ("", "edit_deed", "write"):
        try:
            b.build(SpecialistForm(operation=bad), NOTES)
            raise AssertionError(bad)
        except FormProblem:
            pass


async def test_a_request_whose_mode_disagrees_with_its_operation_is_refused():
    try:
        BridgeActionRequest(agent="printer", workspace="printers", operation="print", prompt="x", approval_mode="read_only")
        raise AssertionError("built")
    except ValueError:
        pass


# -- the runner ----------------------------------------------------------------
async def test_a_change_is_read_back_and_sent_only_after_yes():
    client = FakeClient(submits=[_view("queued")], states=["succeeded"],
                        result=BridgeResult(task_id=TID, state="succeeded", ready=True, ok=True, body="Created hello.txt"))
    pending = PendingBridgeRequests()
    runner = _runner("bridge_writer", client, Filler({"operation": "implement", "instructions": "make hello.txt"}), pending)
    said = await _say(runner, "make a hello file in the sandbox")
    assert "Shall I send it" in said and client.sent == [], said
    assert pending.peek().specialist == "bridge_writer"
    said = await _say(runner, "yes")
    assert "Created hello.txt" in said and TID in said, said
    assert client.sent[0].idempotency_key == "toyota-k1" and pending.peek() is None


async def test_no_drops_it_and_a_bare_yes_with_nothing_waiting_sends_nothing():
    client = FakeClient()
    runner = _runner("bridge_scanner", client, Filler({"scanner": "window"}))
    await _say(runner, "scan on the window scanner")
    assert "didn't send" in await _say(runner, "no thanks")
    assert "nothing waiting" in await _say(runner, "yes")
    assert client.sent == []


async def test_reads_go_straight_and_an_unfinished_task_is_tracked():
    notices = Notices()
    client = FakeClient(submits=[_view("queued")], states=["running"] * 5)
    runner = _runner("bridge_printer", client, Filler({"operation": "print_discover"}), notices=notices)
    said = await _say(runner, "list the printers")
    assert client.sent[0].prompt == "discover" and TID in said and "tell you when it finishes" in said, said
    assert notices.tracked == [(TID, "printer")]


async def test_the_model_asking_back_or_failing_sends_nothing():
    client = FakeClient()
    runner = _runner("bridge_scanner", client, Filler({"ask": "Which scanner, window or freezer?"}, RuntimeError("429")))
    assert await _say(runner, "scan something") == "Which scanner, window or freezer?"
    assert "nothing was sent" in await _say(runner, "scan something")
    assert client.sent == []


async def test_cancel_by_task_id_needs_no_model():
    client = FakeClient()
    filler = Filler()
    said = await _say(_runner("bridge_writer", client, filler), f"cancel {TID}")
    assert client.cancelled == [TID] and "cancel" in said and filler.calls == [], said
    assert readback_of(f"please stop task {TID}") == ("cancel", TID)


async def test_a_bare_task_id_is_a_readback_never_a_new_task():
    # 2026-11-05: Toyota's model routed "ROUTE: star_trek_bridge | t_8a71..." for
    # "What is the result of task t_8a71...?", and it went to codex as task t_3fdd...
    # A Bridge worker can't know Bridge task ids, and a readback sends nothing.
    assert readback_of(TID) == ("result", TID)
    assert readback_of(f" {TID}. ") == ("result", TID)
    assert readback_of(f"Star Trek Bridge task {TID}") == ("result", TID)
    assert readback_of(f"explain how the gateway assigns ids like {TID}") is None
    assert readback_of("what does roles.py do?") is None


# -- yes/no, the route and the notices ------------------------------------------
async def test_yes_or_no_counts_only_short_replies():
    assert [yes_or_no(t) for t in ("Yes.", "go ahead", "no", "nope, don't", "no, print the other one instead please now")] == [
        "yes", "yes", "no", "no", None]


async def test_confirmation_route_fires_only_while_something_waits():
    pending = PendingBridgeRequests()
    route = BridgeConfirmationRoute(pending)
    def prompt_of(text):
        return '...\nLATEST USER MESSAGE: {"text": "%s"}\nTOYOTA:' % text
    prompt = prompt_of("yes")
    assert route.forced_agent(prompt) is None
    req, _ = SPECIALISTS["bridge_scanner"].builder.build(SpecialistForm(scanner="window"), SCANNER)
    pending.put("bridge_scanner", req, "")
    assert route.forced_agent(prompt) == "bridge_scanner" and route.question(prompt) == "yes"
    assert route.forced_agent(prompt_of("what's the weather")) is None


async def test_notices_speak_once_finished_and_combined_alerts_split_ids():
    clock = Clock()
    clock.now = 100
    client = FakeClient(states=["running", "succeeded"],
                        result=BridgeResult(task_id=TID, state="succeeded", ready=True, ok=True, body="done"))
    notices = BridgeCompletionNotices(lambda: client, lambda r: f"said {r.body}", clock=clock, every=15)
    notices.track(TID, "Codex writer")
    assert notices.pending() == []
    assert notices.pending() == []  # not due again yet
    clock.now = 120
    [alert] = notices.pending()
    assert alert["spoken"] == "said done" and alert["id"] == f"bridge-{TID}"

    class PostOffice:
        acked = []

        def pending(self):
            return [{"id": "po-1", "spoken": "x"}]

        def acknowledge(self, ids=None):
            PostOffice.acked.append(ids)
            return 1

    both = CombinedAlerts(PostOffice(), notices)
    assert [a["id"] for a in both.pending()] == ["po-1", f"bridge-{TID}"]
    assert both.acknowledge([f"bridge-{TID}"]) == 1 and PostOffice.acked == []
    assert notices.pending() == []


async def test_a_bridge_error_is_spoken_not_raised():
    client = FakeClient()
    client.discover = lambda: (_ for _ in ()).throw(e.BridgeAmbiguous("unreachable"))
    said = await _say(_runner("bridge_writer", client, Filler()), "make a file")
    assert "couldn't reach the Star Trek Bridge" in said, said


async def test_device_requests_route_to_their_specialist_and_task_ids_do_not():
    from ..adapters.bridge_device_route import device_route
    cases = {"Scan a receipt on the window scanner": "bridge_scanner",
             "scan this for Scala on the freezer": "bridge_scanner",
             "print a test page": "bridge_printer", "list the printers": "bridge_printer",
             "use the codex writer to add a file": "bridge_writer",
             "ask Claude Sonnet on the Bridge to fix roles.py": "bridge_coder",
             f"cancel print job {TID}": None, "what did we spend on printers last year": None,
             "how much did we spend on utilities": None,
             "run the notes diagnostic": "bridge_notes", "write a new diagnostic marker": "bridge_notes",
             "check the diagnostic note": "bridge_notes", "add a note about the deed": "bridge_notes",
             "read the deed": "bridge_notes", "list the notes": "bridge_notes",
             "change Drafted by to Prepared by in 2026-11-05_deed.md": "bridge_notes",
             "what did we spend on the deed attorney": None, "what is a deed": None,
             "explain what the notes diagnostic just proved": None}
    assert {t: device_route(t) for t in cases} == cases


# -- approved notes: list, read, update ------------------------------------------
REV1, REV2 = "a" * 64, "b" * 64
DEED = "2026-11-05_deed.md"


async def test_notes_builder_list_and_read_need_no_yes_and_update_always_does():
    b = SPECIALISTS["bridge_notes"].builder
    req, said = b.build(SpecialistForm(operation="list"), NOTES)
    assert json.loads(req.prompt) == {"notes": "list"} and req.operation == "inspect" and not req.needs_confirmation
    req, _ = b.build(SpecialistForm(operation="read", note_id=DEED), NOTES)
    assert json.loads(req.prompt) == {"notes": "read", "id": DEED} and not req.needs_confirmation
    form = SpecialistForm(operation="update", note_id=DEED, expected_revision=REV1,
                          edits=[{"find": "Drafted by", "replace": "Prepared by"}])
    req, said = b.build(form, NOTES)
    assert json.loads(req.prompt) == {"notes": "update", "id": DEED, "expected_revision": REV1,
                                      "edits": [{"find": "Drafted by", "replace": "Prepared by"}]}
    assert req.operation == "implement" and req.approval_mode == "workspace_write" and req.needs_confirmation
    assert DEED in said and REV1[:12] in said and "Drafted by" in said and "Prepared by" in said


async def test_notes_builder_refuses_what_cannot_be_safe():
    b = SPECIALISTS["bridge_notes"].builder
    edit = [{"find": "x", "replace": "y"}]
    for form in (SpecialistForm(operation="read"),                                            # no note named
                 SpecialistForm(operation="read", note_id="../../etc/passwd"),                # not a note file name
                 SpecialistForm(operation="update", note_id=DEED, edits=edit),                # never read: no revision
                 SpecialistForm(operation="update", note_id=DEED, expected_revision="abc", edits=edit),
                 SpecialistForm(operation="update", note_id=DEED, expected_revision=REV1),    # nothing to change
                 SpecialistForm(operation="update", note_id=DEED, expected_revision=REV1,
                                edits=[{"find": "", "replace": "y"}])):
        try:
            b.build(form, NOTES)
            raise AssertionError(form)
        except FormProblem:
            pass


async def test_a_read_then_an_update_uses_the_remembered_revision_and_waits_for_yes():
    from ..bridge_client import desk
    desk.NOTE_REVISIONS.remember(DEED, "c" * 64)                                 # stale, from an earlier read
    read_body = json.dumps({"id": DEED, "revision": REV1, "note": "Drafted by X"})
    client = FakeClient(submits=[_view("queued", "inspect")], states=["succeeded"],
                        result=BridgeResult(task_id=TID, state="succeeded", ready=True, ok=True, body=read_body))
    filler = Filler({"operation": "read", "note_id": DEED},
                    {"operation": "update", "note_id": DEED, "expected_revision": "d" * 64,    # the model's own: ignored
                     "edits": [{"find": "Drafted by", "replace": "Prepared by"}]})
    pending = PendingBridgeRequests()
    runner = _runner("bridge_notes", client, filler, pending)
    assert "Drafted by X" in await _say(runner, "read the deed")
    assert client.sent[0].operation == "inspect"
    said = await _say(runner, "change Drafted by to Prepared by in the deed")
    assert "Shall I send it" in said and len(client.sent) == 1, said                  # nothing sent before yes
    assert json.loads(pending.peek().request.prompt)["expected_revision"] == REV1
    done = json.dumps({"id": DEED, "revision": REV2, "verified": True, "diff": ""})
    client.submits.append(_view("queued")); client.states.append("succeeded")
    client._result = BridgeResult(task_id=TID, state="succeeded", ready=True, ok=True, body=done)
    await _say(runner, "yes")
    assert client.sent[1].operation == "implement" and desk.NOTE_REVISIONS.get(DEED) == REV2


async def test_an_update_with_no_read_in_this_process_sends_nothing():
    from ..bridge_client import desk
    desk.NOTE_REVISIONS = desk.NoteRevisions()
    client = FakeClient()
    runner = _runner("bridge_notes", client, Filler({"operation": "update", "note_id": "never_read.md",
                                                     "edits": [{"find": "a", "replace": "b"}]}))
    said = await _say(runner, "fix never_read.md")
    assert "read it first" in said and client.sent == [], said


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))
