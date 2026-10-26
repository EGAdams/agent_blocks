"""Telemetry stays content-free, correlated, and observational on failure."""
import io
import json
from contextlib import redirect_stdout

from agent_memory.adapters.turn_telemetry import turn_scope, timed_stage, TimedModel
from agent_memory.models import AgentReply


async def test_request_scope_is_restored_and_failures_are_recorded_without_content():
    output = io.StringIO()
    class Model:
        async def complete(self, prompt):
            raise ValueError('private prompt material')
    with redirect_stdout(output):
        with turn_scope('diagnostic-request'):
            try:
                await TimedModel(Model(), 'provider').complete('private input')
            except ValueError:
                pass
        with timed_stage('outside'):
            pass
    records = [json.loads(line.split(' ', 1)[1]) for line in output.getvalue().splitlines()]
    assert records[0]['request_id'] == 'diagnostic-request'
    assert records[0]['outcome'] == 'ValueError'
    assert records[1]['request_id'] == 'local'
    assert records[0]['duration_ms'] >= 0
    assert 'private' not in output.getvalue()


if __name__ == '__main__':
    from tests.runner import main
    main(dict(globals()))
