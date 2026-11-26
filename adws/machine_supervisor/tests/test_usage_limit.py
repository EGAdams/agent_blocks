"""A todo the agent could not do because its subscription's usage limit was used up is put back in the queue
until the limit resets, instead of being filed as failed for good (BATI, 2026-11-24/25: four Sheila todos)."""
from datetime import datetime, timezone

from agent_comms.models import TaskStatus

from machine_supervisor.adapters.usage_limit import ClaudeUsageLimit
from machine_supervisor.models import AgentReport
from machine_supervisor.operations.todo import TodoPoster, TodoWorker, queued_todos

from .fakes import FakeClock, FakeLock, Log
from .test_handoffs import sites  # noqa: F401  (the fixture)

LIMIT = "You've hit your 5-hour limit · resets 3:50pm (America/New_York)"


class LimitedAgent:
    """Says what the Claude CLI said on BATI until `limited` is turned off."""
    def __init__(self) -> None:
        self.limited, self.seen = True, []

    def work(self, envelope) -> AgentReport:
        self.seen.append(envelope.id)
        if self.limited:
            return AgentReport(machine="SQMC", role="todo", diagnosis=LIMIT, error=LIMIT)
        return AgentReport(machine="SQMC", role="todo", diagnosis="did it")


def worker(site, agent, clock):
    return TodoWorker(site.catalog, site.stores.boxes, agent, FakeLock(), Log(), ClaudeUsageLimit(), clock)


def test_the_claude_limit_message_gives_the_reset_time():
    now = datetime(2026, 11, 25, 20, 19, tzinfo=timezone.utc)                     # 15:19 in New York
    until = ClaudeUsageLimit().until(LIMIT, now)
    assert until is not None and until.astimezone(timezone.utc) >= datetime(2026, 11, 25, 20, 50, tzinfo=timezone.utc)
    assert ClaudeUsageLimit().until("the agent did not answer: ssh refused", now) is None


def test_a_todo_that_hit_the_usage_limit_goes_back_in_the_queue_and_is_done_after_the_reset(sites):  # noqa: F811
    bati, sqmc = sites["BATI"], sites["SQMC"]
    ok, _, data = TodoPoster(bati.catalog, bati.courier).post("SQMC", "Sheila readiness", "chatgpt-bridge")
    agent, clock = LimitedAgent(), FakeClock()
    lines = worker(sqmc, agent, clock).work()
    assert "usage limit" in lines[0]
    box, held = sqmc.stores.boxes.find(data["id"])
    assert box == "new" and held.status is TaskStatus.QUEUED          # before the fix: "failed", for good
    assert queued_todos(sqmc.stores.boxes, clock.now()) == 0           # not ready yet: the watch leaves it alone
    assert worker(sqmc, agent, clock).work() == [] and len(agent.seen) == 1
    clock.sleep(24 * 3600)
    agent.limited = False
    assert queued_todos(sqmc.stores.boxes, clock.now()) == 1
    assert "done" in worker(sqmc, agent, clock).work()[0]
    box, done = sqmc.stores.boxes.find(data["id"])
    assert box == "completed" and done.attempts == 2


def test_a_todo_already_failed_by_the_limit_is_retried_only_when_named(sites):  # noqa: F811
    bati, sqmc = sites["BATI"], sites["SQMC"]
    _, _, data = TodoPoster(bati.catalog, bati.courier).post("SQMC", "Sheila readiness", "chatgpt-bridge")
    claimed = sqmc.stores.boxes.claim(data["id"])                      # how BATI filed it before the fix
    sqmc.stores.boxes.settle(claimed.model_copy(update={"error": LIMIT}), ok=False)
    agent, clock = LimitedAgent(), FakeClock()
    agent.limited = False
    assert worker(sqmc, agent, clock).work() == [] and agent.seen == []    # never picked up on its own
    lines = worker(sqmc, agent, clock).work(data["id"])
    assert "done" in lines[-1] and agent.seen == [data["id"]]
    assert sqmc.stores.boxes.find(data["id"])[0] == "completed"


def test_a_todo_that_failed_for_another_reason_is_not_retried(sites):  # noqa: F811
    bati, sqmc = sites["BATI"], sites["SQMC"]
    _, _, data = TodoPoster(bati.catalog, bati.courier).post("SQMC", "x", "chatgpt-bridge")
    claimed = sqmc.stores.boxes.claim(data["id"])
    sqmc.stores.boxes.settle(claimed.model_copy(update={"error": "the agent did not run"}), ok=False)
    agent = LimitedAgent()
    lines = worker(sqmc, agent, FakeClock()).work(data["id"])
    assert "not retried" in lines[0] and agent.seen == []
    assert sqmc.stores.boxes.find(data["id"])[0] == "failed"
