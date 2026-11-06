"""Toyota's four inspection questions reach the object inspector, and her Bridge submissions are traced.

    .venv/bin/python -m receptionist_team.tests.test_object_inspection
"""
import asyncio
import tempfile
from pathlib import Path

from object_trace.adapters.sqlite_store import SqliteTraceStore
from object_trace.tracer import ObjectTracer

from ..adapters.inspection_route import ObjectInspectionRoute, inspection_question
from ..adapters.inspection_runner import ObjectInspectionRunner
from ..bridge_client.models import BridgeActionRequest, BridgeTaskView
from ..bridge_client.tracing import TracingBridgeClient
from ..object_tracing import end_turn, traced_turn
from ..profiles import TOYOTA

TID = "t_" + "8a7119bac894a2e2f85a0d06"


def _prompt(message, recalled=""):
    return f"{recalled}\n\nUSER: {message}\nAGENT:"


def test_the_four_questions_name_their_inspector_question_and_subject():
    assert inspection_question("Show the latest Notes update") == ("latest_notes_update", None)
    assert inspection_question(f"Where did task {TID} stop?") == ("where_stopped", TID)
    assert inspection_question("where did this task stop?") == ("where_stopped", None)
    assert inspection_question("Show object agent.notes:t_abc:1 state and error") == ("object_state", "agent.notes:t_abc:1")
    assert inspection_question("show the state and error of object bridge.task_run:t_abc:2.") == ("object_state", "bridge.task_run:t_abc:2")
    assert inspection_question("Did the content save, and did the browser render that version?") == ("saved_and_rendered", None)


def test_other_words_have_no_opinion():
    for line in ("what did we spend on utilities?", "where is the stop button", "run the notes diagnostic",
                 "where should I send the end-of-month report", f"status {TID}", "save this note"):
        assert inspection_question(line) is None, line


def test_toyota_asks_this_route_before_the_task_route_which_reads_stop_as_cancel():
    route = TOYOTA.route_override
    assert route.forced_agent(_prompt(f"where did task {TID} stop?")) == "object_inspector"
    assert route.forced_agent(_prompt(f"status {TID}")) == "star_trek_bridge"
    assert route.forced_agent(_prompt("show the latest notes update")) == "object_inspector"
    # only the live message counts, never a recalled one
    assert ObjectInspectionRoute().forced_agent(_prompt("hello", recalled="USER: show the latest notes update")) is None


def test_runner_repeats_the_inspector_answer_with_evidence_and_missing():
    seen = []

    def fetch(url, timeout):
        seen.append(url)
        return {"question": "Where did this task stop?", "found": True, "answer": "Bridge task X stopped at phase execute.",
                "evidence": [{"fact": f"fact {n}"} for n in range(5)], "missing": ["a Toyota submission record"],
                "subject": "bridge.task_run:X:1"}

    runner = ObjectInspectionRunner("http://inspector", fetch=fetch)
    text = asyncio.run(runner.run(None, "object_inspector", "", f"where did task {TID} stop?")).text
    assert seen == [f"http://inspector/api/ask/where_stopped?subject={TID}"]
    assert text.startswith("Bridge task X stopped at phase execute.")
    assert "fact 0; fact 1; fact 2 (and 2 more)" in text and "Missing: a Toyota submission record." in text
    assert "Object id: bridge.task_run:X:1." in text


def test_runner_says_so_when_the_inspector_is_down():
    def down(url, timeout):
        raise ConnectionRefusedError("refused")

    text = ObjectInspectionRunner("http://127.0.0.1:1", fetch=down).answer("show the latest notes update")
    assert "didn't answer (ConnectionRefusedError)" in text and "won't guess" in text


class _Gateway:
    def __init__(self, fail=False):
        self.fail = fail

    def submit(self, request):
        if self.fail:
            raise TimeoutError("gateway slow")
        return BridgeTaskView(task_id=TID, state="queued", agent=request.agent, workspace=request.workspace,
                              operation=request.operation)


def test_a_submission_inside_a_turn_is_traced_with_its_task_and_turn():
    with tempfile.TemporaryDirectory() as tmp:
        store = SqliteTraceStore(Path(tmp) / "t.db")
        tracer = ObjectTracer(store, process_session="test")
        request = BridgeActionRequest(agent="notes", workspace="agent_blocks", operation="implement",
                                      prompt="{\"secret\": \"do not record\"}", approval_mode="workspace_write",
                                      idempotency_key="toyota-abc")
        import object_trace.factory as factory
        saved, factory._shared = factory._shared, tracer
        try:
            with traced_turn("toyota", "req-1", "write a notes marker") as turn:
                TracingBridgeClient(_Gateway(), tracer_factory=lambda: tracer).submit(request)
                end_turn(turn, True)
            try:
                TracingBridgeClient(_Gateway(fail=True), tracer_factory=lambda: tracer).submit(request)
            except TimeoutError:
                pass
        finally:
            factory._shared = saved
        submits = store.objects(kind="toyota.bridge_submit", task_id=TID, limit=5)
        assert len(submits) == 1 and submits[0].run_id == "req-1" and submits[0].state == "succeeded"
        assert "do not record" not in submits[0].model_dump_json()
        assert store.objects(kind="receptionist.turn", limit=5)[0].state == "succeeded"
        failed = [o for o in store.objects(kind="toyota.bridge_submit", limit=5) if o.state == "failed"]
        assert len(failed) == 1 and failed[0].task_id is None


def test_a_turn_that_ends_in_an_error_record_is_failed():
    with tempfile.TemporaryDirectory() as tmp:
        store = SqliteTraceStore(Path(tmp) / "t.db")
        import object_trace.factory as factory
        saved, factory._shared = factory._shared, ObjectTracer(store, process_session="test")
        try:
            with traced_turn("toyota", "req-2", "hi") as turn:
                end_turn(turn, False)
        finally:
            factory._shared = saved
        assert store.objects(kind="receptionist.turn", limit=5)[0].state == "failed"


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for name, test in tests:
        test()
        print("PASS ", name)
    print(f"\n{len(tests)}/{len(tests)} passed")
