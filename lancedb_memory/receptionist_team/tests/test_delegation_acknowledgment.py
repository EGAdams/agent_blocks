"""The spoken acknowledgment must reach the sink BEFORE the slow delegated
call, and the background speaker must not block it.

Ported from mazda_team/tests/test_delegation_acknowledgment.py when that
package was retired, and now run against the shared RoutingModel both
receptionists use. All doubles are offline.
"""
import tempfile
import threading
from types import SimpleNamespace

from tests.fakes import ScriptedModel

from ..adapters import tool_agent_runner as tool_agent_runner_module
from ..adapters.mickey_runner import MickeyRunner
from ..adapters.routing_model import RoutingModel
from ..adapters.voice_io import AcknowledgmentSpeaker
from ..delegation import RunnerMap, UnwiredRunner
from ..mazda_roles import MAZDA_ROSTER, ROLE_SPOKEN_NAMES
from ..roles import TOYOTA_ROSTER


def _mazda_routing(reply_text, on_delegation, explainer=None, root=None):
    runners = {}
    if explainer is not None:
        runners["mickey"] = MickeyRunner(explainer=explainer, repo_root=root)
    return RoutingModel(
        ScriptedModel([reply_text]),
        model="fake-role-model",
        roster=MAZDA_ROSTER,
        runner=RunnerMap(runners, default=UnwiredRunner(ROLE_SPOKEN_NAMES)),
        speaker="Mazda",
        on_delegation=on_delegation,
    )


def _toyota_routing(reply_text, on_delegation):
    return RoutingModel(
        ScriptedModel([reply_text]),
        model="fake-role-model",
        roster=TOYOTA_ROSTER,
        runner=tool_agent_runner_module.ToolAgentRunner(),
        speaker="Toyota",
        on_delegation=on_delegation,
    )


async def test_acknowledgment_reaches_the_sink_before_the_role_runs():
    """The whole point: "please wait" is spoken while the role is working."""
    order = []

    async def fake_run_role(role, cwd, model, echo=True):
        order.append(("run_role", None))
        return SimpleNamespace(is_error=False, final_text="done", cost_usd=None)

    original = tool_agent_runner_module.run_role
    tool_agent_runner_module.run_role = fake_run_role
    try:
        routing = _toyota_routing(
            "ROUTE: receipt_scanner", lambda s: order.append(("ack", s))
        )
        await routing.complete("audit the receipts")
    finally:
        tool_agent_runner_module.run_role = original

    assert [step for step, _ in order] == ["ack", "run_role"], order
    assert "Please wait" in order[0][1]


async def test_mickey_route_also_acknowledges_first():
    with tempfile.TemporaryDirectory() as root:
        spoken = []
        explainer = ScriptedModel(["Here is the status."])
        reply = await _mazda_routing(
            "ROUTE: mickey", spoken.append, explainer=explainer, root=root
        ).complete("where are we?")

        assert spoken and "progress tracker" in spoken[0]
        assert reply.text == "Here is the status."


async def test_unknown_route_name_never_acknowledges():
    spoken = []
    reply = await _mazda_routing("ROUTE: bogus", spoken.append).complete("go")

    # Nothing is going to run, so there is nothing to ask the user to wait for.
    assert not spoken
    assert all(name in reply.text for name in ("mickey", "parser"))


async def test_ordinary_reply_never_acknowledges():
    spoken = []
    reply = await _mazda_routing("just chatting", spoken.append).complete("hello")

    assert reply.text == "just chatting"
    assert not spoken


async def test_broken_sink_does_not_lose_the_delegated_work():
    """A failed acknowledgment is a courtesy lost, not a turn lost."""

    def boom(sentence):
        raise RuntimeError("no speaker")

    async def fake_run_role(role, cwd, model, echo=True):
        return SimpleNamespace(is_error=False, final_text="done anyway", cost_usd=None)

    original = tool_agent_runner_module.run_role
    tool_agent_runner_module.run_role = fake_run_role
    try:
        reply = await _toyota_routing("ROUTE: receipt_scanner", boom).complete(
            "audit the receipts"
        )
    finally:
        tool_agent_runner_module.run_role = original

    assert "done anyway" in reply.text


async def test_speaker_say_returns_before_playback_finishes():
    """say() must not block -- that was the bug that made the sink pointless."""
    released = threading.Event()
    started = threading.Event()

    class SlowVoice:
        def speak(self, text):
            started.set()
            released.wait(5)

    speaker = AcknowledgmentSpeaker(SlowVoice())
    speaker.say("please wait")
    assert started.wait(5), "playback thread never started"
    # Still playing, yet say() has already returned -- delegation can proceed.
    assert speaker._thread is not None and speaker._thread.is_alive()
    released.set()
    speaker.wait()
    assert speaker._thread is None


async def test_speaker_wait_joins_so_players_never_overlap():
    played = []

    class RecordingVoice:
        def speak(self, text):
            played.append(text)

    speaker = AcknowledgmentSpeaker(RecordingVoice())
    speaker.say("please wait")
    speaker.wait()
    assert played == ["please wait"]


async def test_speaker_swallows_playback_failure():
    class BrokenVoice:
        def speak(self, text):
            raise OSError("ffplay missing")

    speaker = AcknowledgmentSpeaker(BrokenVoice())
    speaker.say("please wait")
    speaker.wait()  # must not raise


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))
