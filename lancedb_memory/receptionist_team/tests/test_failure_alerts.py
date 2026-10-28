"""No silent failures: a task she sent that failed is said out loud first.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.tests.test_failure_alerts
"""
import asyncio
import shutil
import tempfile
from pathlib import Path

from agent_comms.adapters.launchers import RecordingLauncher
from agent_comms.factory import build_post_office
from agent_comms.models import TaskRequest
from agent_comms.tests.fakes import FakeClock

from ..adapters.post_office_alerts import PostOfficeAlerts
from ..profiles import MAZDA, SYDNEY, TOYOTA
from ..service_models import AlertAckRequest
from .test_service_turns import _ScriptedAgent, _drain, _runner


class _Inbox:
    def __init__(self, alerts=None, broken=False):
        self.alerts = list(alerts or [])
        self.broken = broken
        self.acked = []

    def pending(self):
        if self.broken:
            raise OSError("alerts dir unreadable")
        return list(self.alerts)

    def acknowledge(self, alert_ids=None):
        self.acked.append(alert_ids)
        before = len(self.alerts)
        self.alerts = [a for a in self.alerts if alert_ids is not None and a["id"] not in alert_ids]
        return before - len(self.alerts)


_ALERT = {"id": "t1", "spoken": "Task 20bf19be failed: the plan I sent to Opus did not run. Refused."}


def _with_inbox(inbox):
    runner = _runner(_ScriptedAgent(reply="Here you go."))
    runner._alerts = inbox
    return runner


async def test_a_pending_failure_is_spoken_before_the_answer_and_then_marked_heard():
    inbox = _Inbox([_ALERT])
    records = _drain(_with_inbox(inbox), "what's next?")
    assert [r.kind for r in records] == ["acknowledgment", "reply", "result"], [r.kind for r in records]
    assert "went wrong" in records[0].text and "20bf19be" in records[0].text
    assert inbox.acked == [["t1"]] and inbox.alerts == []


async def test_no_alerts_means_no_extra_record():
    records = _drain(_with_inbox(_Inbox()), "hello")
    assert [r.kind for r in records] == ["reply", "result"]


async def test_an_unreadable_alert_board_is_itself_announced():
    records = _drain(_with_inbox(_Inbox(broken=True)), "hello")
    assert records[0].kind == "acknowledgment" and "can't read my failure alerts" in records[0].text
    assert records[-1].kind == "result"   # the turn still runs
    assert _with_inbox(_Inbox(broken=True)).alerts()["ok"] is False


async def test_alerts_endpoint_data_and_acknowledge():
    inbox = _Inbox([_ALERT])
    runner = _with_inbox(inbox)
    assert runner.alerts()["alerts"] == [_ALERT]
    assert runner.acknowledge_alerts(["t1"]) == {"ok": True, "acknowledged": 1}
    assert AlertAckRequest.model_validate_json("{}").ids is None


async def test_post_office_alerts_reads_the_real_board():
    root = Path(tempfile.mkdtemp(prefix="po_alerts_"))
    try:
        kit = build_post_office(root=root, launcher=RecordingLauncher(), clock=FakeClock())
        sent = kit.dispatcher.send(TaskRequest(sender="toyota", recipient="opus", task_type="plan", text="x"))
        kit.transport.claim("opus", sent.task_id)          # its worker died
        kit.sweeper(grace_seconds=-1)                      # nothing yet: idempotent work is requeued
        inbox = PostOfficeAlerts("toyota", kit_factory=lambda: kit)
        build = kit.dispatcher.send(TaskRequest(sender="toyota", recipient="sonnet", task_type="build", text="y"))
        kit.transport.claim("sonnet", build.task_id)
        assert kit.sweeper(grace_seconds=-1).sweep()       # a build is failed, not re-run
        pending = inbox.pending()
        assert [a["task_id"] for a in pending] == [build.task_id], pending
        assert "build I sent to Sonnet did not run" in pending[0]["spoken"]
        assert inbox.acknowledge() == 1 and inbox.pending() == []
    finally:
        shutil.rmtree(root, ignore_errors=True)


async def test_toyota_and_sydney_hear_alerts_and_mazda_has_none_to_hear():
    assert TOYOTA.build_alerts is not None and SYDNEY.build_alerts is not None
    assert MAZDA.build_alerts is None


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))
