"""Machine Ops integration, command safety and bounded real subprocess handling."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

from agent_comms.executors.machine_ops import MachineOpsExecutor
from agent_comms.machine_ops.commands import Request, CommandSpec, command, PROPERTIES, UNITS, TESTS
from agent_comms.machine_ops.runner import SubprocessCommandRunner, Outcome, MAX_OUTPUT_BYTES, redact
from agent_comms.models import Envelope, MessageKind


def envelope(text, **overrides):
    values = dict(id="test", correlation_id="test", kind=MessageKind.REQUEST,
                  sender="chatgpt-bridge", recipient="machine-ops", task_type="inspect", text=text)
    return Envelope(**{**values, **overrides})


class FakeRunner:
    def __init__(self): self.calls = []
    def run(self, spec):
        self.calls.append(spec)
        return Outcome(returncode=0, stdout="ActiveState=active", stderr="")


class MachineOpsTests(unittest.TestCase):
    def test_status_has_only_approved_properties(self):
        for unit in UNITS:
            argv = command(Request(operation="status", unit=unit)).argv
            self.assertIn("--property=" + ",".join(PROPERTIES), argv)
            self.assertNotIn("Environment", ",".join(argv))

    def test_approved_test_names_resolve_fixed_commands(self):
        for suite in TESTS:
            spec = command(Request(operation="run_test", unit="agent-bridge-worker.service", suite=suite))
            self.assertEqual(spec.argv, TESTS[suite])

    def test_health_discards_body(self):
        argv = command(Request(operation="check", unit="dashboard-server.service")).argv
        self.assertIn("/dev/null", argv)
        self.assertIn("--max-time", argv)

    def test_restarts_arbitrary_commands_paths_sql_and_senders_rejected(self):
        runner = FakeRunner()
        executor = MachineOpsExecutor(runner)
        requests = [
            '{"operation":"status","operation":"restart","unit":"dashboard-server.service"}',
            '{"operation":"restart","unit":"dashboard-server.service","confirm":true}',
            '{"operation":"status","unit":"dashboard-server.service; id"}',
            '{"operation":"status","unit":"dashboard-server.service","command":"id"}',
            '{"operation":"run_test","unit":"agent-bridge-worker.service","suite":"../../evil"}',
            '{"operation":"run_test","unit":"agent-bridge-worker.service","suite":"python -c evil"}',
            '{"operation":"status","unit":"dashboard-server.service","sql":"SELECT 1"}',
            '{"operation":"journal_tail","unit":"dashboard-server.service","lines":true}',
            '{"operation":"journal_tail","unit":"dashboard-server.service","lines":201}',
        ]
        for raw in requests:
            with self.subTest(raw=raw): self.assertFalse(asyncio.run(executor.execute(envelope(raw))).ok)
        raw = '{"operation":"status","unit":"dashboard-server.service"}'
        for sender in ["voice", "unknown", "toyota", "oauth-user"]:
            self.assertFalse(asyncio.run(executor.execute(envelope(raw, sender=sender))).ok)
        self.assertFalse(runner.calls)

    def test_both_explicit_senders_can_inspect(self):
        for sender in ["chatgpt-bridge", "toyota-bridge", "sydney-bridge"]:
            out = asyncio.run(MachineOpsExecutor(FakeRunner()).execute(envelope(
                '{"operation":"status","unit":"adw-supervisor.service"}', sender=sender)))
            self.assertTrue(out.ok)
            self.assertFalse(json.loads(out.text)["restart_enabled"])

    def test_possible_credentials_are_withheld(self):
        for value in ["Bearer opaque-value", "api_key=" + "example" * 10,
                      "-----BEGIN PRIVATE KEY-----\nvalue\n-----END PRIVATE KEY-----"]:
            self.assertNotIn(value, redact(value))

    def test_real_output_limit_kills_child_and_bounds_memory(self):
        result = SubprocessCommandRunner().run(CommandSpec(argv=(sys.executable, "-c",
            "import os,time; os.write(1,b'x'*200000); time.sleep(60)"), timeout_seconds=3))
        self.assertTrue(result.truncated)
        self.assertLessEqual(len(result.stdout.encode()) + len(result.stderr.encode()), MAX_OUTPUT_BYTES)
        self.assertNotEqual(result.returncode, 0)

    def test_timeout_even_when_child_closes_its_output(self):
        result = SubprocessCommandRunner().run(CommandSpec(argv=(sys.executable, "-c",
            "import os,time; os.close(1); os.close(2); time.sleep(60)"), timeout_seconds=.15))
        self.assertTrue(result.timed_out)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__": unittest.main()


class RefusalIsFinalTests(unittest.TestCase):
    def test_refusal_names_reason_and_is_not_retried(self):
        from agent_comms.worker import IdempotentRetries
        result = asyncio.run(MachineOpsExecutor(FakeRunner()).execute(envelope("please check the worker")))
        self.assertFalse(result.ok)
        self.assertIn("Reason: not valid JSON", result.text)
        self.assertNotIn("please check", result.text)             # never echoes the request
        again = envelope("x", idempotent=True, attempts=1, max_attempts=3)
        self.assertFalse(IdempotentRetries().should_retry(again, result))

    def test_ordinary_failure_still_retries(self):
        from agent_comms.models import TaskResult
        from agent_comms.worker import IdempotentRetries
        again = envelope("x", idempotent=True, attempts=1, max_attempts=3)
        self.assertTrue(IdempotentRetries().should_retry(again, TaskResult(ok=False, text="boom", performer="p")))
