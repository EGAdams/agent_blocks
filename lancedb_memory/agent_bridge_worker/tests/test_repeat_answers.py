"""RepeatAnswers offline: a fake agent, a fixed clock, a temp store.

    cd lancedb_memory
    .venv/bin/python -m agent_bridge_worker.tests.test_repeat_answers
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parents[ 2 ]))

from agent_bridge_worker.models import AgentOutcome, ClaimedTask, OutcomeKind
from agent_bridge_worker.repeat_answers import JsonAnswerStore, RepeatAnswers


def _task( tid="t_" + "a" * 24, prompt="what does roles.py do", mode="read_only", op="inspect" ) -> ClaimedTask:
    return ClaimedTask( task_id=tid, lease_token="L" * 32, lease_expires_at=0, lease_seconds=60, attempt=1,
                        agent="codex", workspace="agent_blocks", operation=op, approval_mode=mode, prompt=prompt )


class FakeAgent:
    def __init__( self, kind=OutcomeKind.SUCCEEDED ) -> None:
        self.calls, self.kind = 0, kind

    def run( self, task, workdir, keep_alive ) -> AgentOutcome:
        self.calls += 1
        return AgentOutcome( kind=self.kind, summary="sum", body=f"answer { self.calls }", performer="codex",
                             local_task_id="local-1" )

    def lines( self, local_task_id ):
        return [ f"line for { local_task_id }" ]


def test_cache_is_partitioned_by_authenticated_sender():
    from agent_bridge_worker.repeat_answers import request_key
    first = _task()
    second = first.model_copy(update={"sender": "toyota-bridge"})
    assert request_key(first) != request_key(second)


def test_machine_ops_always_observes_fresh_state():
    tmp, clock, agent, store, cached = _setup()
    try:
        task = _task().model_copy(update={"agent": "machine-ops"})
        cached.run(task, Path(tmp.name), lambda: None)
        cached.run(task, Path(tmp.name), lambda: None)
        assert agent.calls == 2
    finally:
        tmp.cleanup()


def _setup( kind=OutcomeKind.SUCCEEDED, window=900 ):
    tmp   = tempfile.TemporaryDirectory()
    clock = [ 1000.0 ]
    agent = FakeAgent( kind )
    store = JsonAnswerStore( Path( tmp.name ) / "answers.json" )
    return tmp, clock, agent, store, RepeatAnswers( agent, store, lambda: clock[ 0 ], window )


def test_a_repeat_inside_the_window_is_answered_without_running_and_says_so() -> None:
    tmp, clock, agent, store, deco = _setup()
    first = deco.run( _task(), Path( "." ), lambda: None )
    clock[ 0 ] += 120
    again = deco.run( _task( tid="t_" + "b" * 24 ), Path( "." ), lambda: None )
    assert agent.calls == 1 and first.body == again.body == "answer 1"
    assert again.kind is OutcomeKind.SUCCEEDED and "t_" + "a" * 24 in again.summary and "2m 00s" in again.summary
    assert again.local_task_id is None
    assert "what does roles.py" not in ( Path( tmp.name ) / "answers.json" ).read_text()


def test_old_answers_different_prompts_and_writes_run_again() -> None:
    tmp, clock, agent, store, deco = _setup()
    deco.run( _task(), Path( "." ), lambda: None )
    deco.run( _task( prompt="something else" ), Path( "." ), lambda: None )
    assert agent.calls == 2
    write = _task( mode="workspace_write", op="implement" )
    deco.run( write, Path( "." ), lambda: None )
    deco.run( write, Path( "." ), lambda: None )
    assert agent.calls == 4
    clock[ 0 ] += 901
    deco.run( _task(), Path( "." ), lambda: None )
    assert agent.calls == 5


def test_failures_are_never_reused() -> None:
    tmp, clock, agent, store, deco = _setup( kind=OutcomeKind.FAILED )
    deco.run( _task(), Path( "." ), lambda: None )
    deco.run( _task(), Path( "." ), lambda: None )
    assert agent.calls == 2


def test_object_debugger_always_reads_live_telemetry() -> None:
    tmp, clock, agent, store, deco = _setup()
    try:
        for prompt in ( "status", "recent", "task t_" + "c" * 24 ):
            task = _task( prompt=prompt ).model_copy( update={ "agent": "object-debugger", "workspace": "object_trace" })
            first = deco.run( task, Path( "." ), lambda: None )
            clock[ 0 ] += 1
            again = deco.run( task, Path( "." ), lambda: None )
            assert first.body != again.body and "reused" not in again.summary
        assert agent.calls == 6 and not ( Path( tmp.name ) / "answers.json" ).exists()
    finally:
        tmp.cleanup()


def test_lines_pass_through_and_a_broken_store_file_is_ignored() -> None:
    tmp, clock, agent, store, deco = _setup()
    assert deco.lines( "x" ) == [ "line for x" ]
    ( Path( tmp.name ) / "answers.json" ).write_text( "not json" )
    deco.run( _task(), Path( "." ), lambda: None )
    deco.run( _task(), Path( "." ), lambda: None )
    assert agent.calls == 1


if __name__ == "__main__":
    for name, fn in list( globals().items()):
        if name.startswith( "test_" ):
            fn()
            print( f"ok  { name }" )
    print( "all repeat-answer tests passed" )
