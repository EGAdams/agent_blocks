"""The ADW-side copy of an agent's steps.  cd lancedb_memory && .venv/bin/python -m agent_comms.tests.test_step_tail"""
import tempfile
from pathlib import Path

from agent_comms.adapters.event_log import JsonlEventLog, JsonlTail, step_line
from agent_comms.models import TaskEvent


def test_only_what_arrives_afterwards_is_read_once() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        log = JsonlEventLog( Path( tmp ) / "events.jsonl" )
        log.notify( TaskEvent( name="task.sent", task_id="old", agent="a" ))
        tail = JsonlTail( Path( tmp ) / "events.jsonl" )
        log.notify( TaskEvent( name="task.output", task_id="new", agent="codex",
                               details={ "phase": "command", "message": "$ ls" }))
        assert [ step_line( e ) for e in tail.new()] == [ "codex command: $ ls" ]
        assert tail.new() == []


if __name__ == "__main__":
    test_only_what_arrives_afterwards_is_read_once()
    print( "1 passed" )
