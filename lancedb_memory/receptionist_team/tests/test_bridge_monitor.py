"""Status-only lifecycle events and background notice regression tests."""
import asyncio
import threading
import time
import unittest

from receptionist_team.bridge_client.models import BridgeTaskView
from receptionist_team.bridge_client.monitor import TaskMonitor
from receptionist_team.bridge_client.desk import BridgeCompletionNotices

TID = "t_" + "a" * 24


def view(**kwargs):
    return BridgeTaskView(task_id=TID, state="running", agent="machine-ops",
                          workspace="machine_ops", operation="inspect", **kwargs)


class MonitorTests(unittest.TestCase):
    def test_lifecycle_readback_reports_absence_without_submitting(self):
        from receptionist_team.bridge_client.workflow import BridgeRun, BridgeTaskWorkflow
        from receptionist_team.bridge_client.models import BridgePolicy
        from receptionist_team.bridge_client.policy import BridgeRoutePolicy
        class Client:
            def status(self, task_id): return view()
        workflow = BridgeTaskWorkflow(BridgeRoutePolicy(BridgePolicy(default_agent="codex", default_workspace="agent_blocks")))
        result = workflow.execute(BridgeRun(text="events " + TID, client_factory=lambda: Client()))
        self.assertIn("lifecycle events unavailable", result.reply)
        self.assertIsNone(result.request)

    def test_events_absent_is_explicitly_unavailable(self):
        result = TaskMonitor(TID, None).observe(view())
        self.assertFalse(result.events_available)
        self.assertIn("unavailable", result.events_note)

    def test_seq_deduplication_bounds_events_and_ignores_stale_responses(self):
        monitor = TaskMonitor(TID, None)
        for start in [1, 1, 51, 1, 101]:
            events = [dict(seq=i, kind="phase", message="phase", at=10) for i in range(start,start+50)]
            result = monitor.observe(view(recent_events=events))
        self.assertEqual(len(result.recent_events), 50)
        self.assertEqual([e.seq for e in result.recent_events], list(range(101,151)))

    def test_task_mismatch_refused(self):
        with self.assertRaises(ValueError): TaskMonitor("t_" + "b"*24, None).observe(view())

    def test_async_poll_does_not_block_voice_event_loop(self):
        class SlowClient:
            def status(self, task_id):
                time.sleep(.1)
                return view()
        async def run():
            poll = asyncio.create_task(TaskMonitor(TID, SlowClient()).poll())
            await asyncio.sleep(.01)
            self.assertFalse(poll.done())
            return await poll
        self.assertEqual(asyncio.run(run()).task_id, TID)

    def test_background_notices_do_not_block_or_duplicate_inflight_polls(self):
        started, release = threading.Event(), threading.Event()
        calls = []
        class SlowClient:
            def status(self, task_id):
                calls.append(task_id)
                started.set()
                release.wait(1)
                return view()
        notices = BridgeCompletionNotices(lambda: SlowClient(), str, every=0, background=True)
        notices.track(TID, "read-only check")
        try:
            self.assertEqual(notices.pending(), [])
            self.assertTrue(started.wait(.5))
            for _ in range(5): self.assertEqual(notices.pending(), [])
            self.assertEqual(calls, [TID])
        finally: release.set()


if __name__ == "__main__": unittest.main()
