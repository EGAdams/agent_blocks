"""Sydney and Toyota reach Sonnet, Opus and each other through the post office.

Pins the fix for 2026-10-28, when Sydney was asked to "get Sonnet to
replicate the communication system that's in Swarmforge" and the request
ended at ask_sonnet's dispatcher ("this doesn't contain an actual question")
with nothing ever sent.

Offline: a real post office in a tmpdir, but no worker is launched and no
model runs.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.tests.test_post_office_runner
"""
import asyncio
import shutil
import tempfile
from pathlib import Path

from agent_comms.adapters.launchers import RecordingLauncher
from agent_comms.factory import build_post_office
from agent_comms.models import TaskStatus
from agent_comms.tests.fakes import FakeClock

from receptionist_team.adapters.post_office_route import PostOfficeRoute
from receptionist_team.adapters.post_office_runner import PostOfficeRunner

THE_REQUEST = (
    "Get Sonnet to replicate the communication system that's in Swarmforge. Let you, Sydney, and "
    "Toyota use that mechanism to communicate. Tell Sonnet to make that happen."
)


def _prompt(message):
    return f"USER: hello\nAGENT: hi\nUSER: {message}\nAGENT:"


def _runner(sender):
    root = Path(tempfile.mkdtemp(prefix="po_runner_"))
    # FakeClock: a quick question waits for its reply, and no worker runs here.
    kit = build_post_office(root=root, launcher=RecordingLauncher(), clock=FakeClock())
    return PostOfficeRunner(sender, kit_factory=lambda: kit), kit, root


def test_the_refused_request_is_forced_to_the_post_office():
    route = PostOfficeRoute("sydney_post_office")
    assert route.forced_agent(_prompt(THE_REQUEST)) == "sydney_post_office"
    assert route.question(_prompt(THE_REQUEST)) == THE_REQUEST
    # A plain question to Sonnet is still the model's call.
    assert route.forced_agent(_prompt("Ask Sonnet where the router lives")) is None
    # Only the latest message counts, never recalled ones.
    assert route.forced_agent(f"USER: {THE_REQUEST}\nAGENT: ok\nUSER: thanks\nAGENT:") is None


def test_sydneys_build_request_is_sent_to_sonnet_and_not_blocked_on():
    runner, kit, root = _runner("sydney")
    try:
        reply = asyncio.run(runner.run(None, "sydney_post_office", "", THE_REQUEST))
        assert reply.text.startswith("I've sent that to Sonnet as task "), reply.text
        [task] = kit.board.tasks_from("sydney")
        assert (task.recipient, task.task_type, task.status) == ("sonnet", "build", TaskStatus.QUEUED)
        assert task.text == THE_REQUEST and not task.idempotent
        assert kit.launcher.woken == ["sonnet"]
        status = asyncio.run(runner.run(None, "sydney_post_office", "", "what's the status of my tasks"))
        assert "build for sonnet is waiting its turn" in status.text, status.text
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_toyota_sends_to_sydney_and_an_unknown_agent_is_named():
    runner, kit, root = _runner("toyota")
    try:
        reply = asyncio.run(runner.run(None, "toyota_post_office", "",
                                       "sydney: the expense tab won't load, please have it looked at"))
        assert kit.board.tasks_from("toyota")[0].recipient == "sydney", reply.text
        assert "hasn't answered yet" in reply.text, reply.text
        nobody = asyncio.run(runner.run(None, "toyota_post_office", "", "tell the weather bot hello"))
        assert "couldn't tell which agent" in nobody.text
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_both_desks_carry_their_post_office():
    from receptionist_team.profiles import SYDNEY, TOYOTA
    assert "sydney_post_office" in SYDNEY.roster.allowed_agents()
    assert "toyota_post_office" in TOYOTA.roster.allowed_agents()
    assert "sydney_post_office" in SYDNEY.system_prompt and "toyota_post_office" in TOYOTA.system_prompt
    assert SYDNEY.route_override.forced_agent(_prompt(THE_REQUEST)) == "sydney_post_office"
    assert TOYOTA.route_override.forced_agent(_prompt("Have Opus review the tax worksheet code")) == "toyota_post_office"


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"{len(tests)} passed")
