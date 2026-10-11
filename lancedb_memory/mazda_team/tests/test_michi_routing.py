"""Mazda delegation checks; all models are offline doubles."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from tests.fakes import ScriptedModel
from mazda_team.adapters.routing_model import RoutingModel
from mazda_team.progress import tracker
from mazda_team.specialist_roles import ROLES


async def test_progress_question_routes_to_michi_not_tool_agent():
    with tempfile.TemporaryDirectory() as root:
        inner = ScriptedModel(["ROUTE: michi | focus on checking"])
        explainer = ScriptedModel(["Here is the status."])
        routing = RoutingModel(inner, "fake-role-model", explainer=explainer, repo_root=root)
        with patch("mazda_team.adapters.routing_model.run_role") as tool:
            reply = await routing.complete("where are we?")
            tool.assert_not_called()
        assert reply.text == "Here is the status."
        assert "QUESTION: focus on checking" in explainer.prompts[0]
        assert "not_started" in explainer.prompts[0]
        assert tracker.state_path(root).exists()


async def test_ordinary_reply_passes_through_and_never_calls_explainer():
    with tempfile.TemporaryDirectory() as root:
        explainer = ScriptedModel([])
        routing = RoutingModel(ScriptedModel(["just chatting, no route"]), "fake", explainer, root)
        assert (await routing.complete("hello")).text == "just chatting, no route"
        assert not explainer.prompts and not tracker.state_path(root).exists()


async def test_unknown_route_name_lists_michi_too():
    with tempfile.TemporaryDirectory() as root:
        routing = RoutingModel(ScriptedModel(["ROUTE: bogus"]), "fake", ScriptedModel([]), root)
        reply = await routing.complete("go")
        assert all(name in reply.text for name in ("michi", "sheeta", "tarita"))


async def test_sheeta_route_still_uses_tool_agent_unaffected():
    with tempfile.TemporaryDirectory() as root:
        explainer = ScriptedModel([])
        routing = RoutingModel(ScriptedModel(["ROUTE: sheeta"]), "fake", explainer, root)
        called = []

        async def fake_run(role, cwd, model):
            called.append((role, cwd, model))
            return SimpleNamespace(is_error=False, final_text="Sample written", cost_usd=None)

        with patch("mazda_team.adapters.routing_model.run_role", fake_run):
            reply = await routing.complete("make a spreadsheet")
        assert called[0][0] is ROLES["sheeta"] and called[0][2] == "fake"
        assert "Sample written" in reply.text and not explainer.prompts
        assert not tracker.state_path(root).exists()


if __name__ == "__main__":
    from mazda_team.tests.runner import main
    main(globals())
