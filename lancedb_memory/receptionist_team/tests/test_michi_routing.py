"""Michi's route must reach her tracker and nobody else's runner.

Ported from mazda_team/tests/test_michi_routing.py when that package was
retired. The shape changed -- RoutingModel no longer knows Michi exists, it
asks the injected RunnerMap -- so these now pin the dispatch instead of an
`if role_name == "michi"` branch. All models are offline doubles.
"""
import tempfile

from tests.fakes import ScriptedModel

from ..adapters.michi_runner import MichiRunner
from ..adapters.routing_model import RoutingModel
from ..delegation import RunnerMap, UnwiredRunner
from ..mazda_roles import MAZDA_ROSTER, ROLE_SPOKEN_NAMES
from ..progress import tracker


def _routing(reply_text, explainer, root, on_delegation=None):
    return RoutingModel(
        ScriptedModel([reply_text]),
        model="fake-role-model",
        roster=MAZDA_ROSTER,
        runner=RunnerMap(
            {"michi": MichiRunner(explainer=explainer, repo_root=root)},
            default=UnwiredRunner(ROLE_SPOKEN_NAMES),
        ),
        speaker="Mazda",
        on_delegation=on_delegation,
    )


async def test_progress_question_routes_to_michi_and_persists_a_snapshot():
    with tempfile.TemporaryDirectory() as root:
        explainer = ScriptedModel(["Here is the status."])
        reply = await _routing("ROUTE: michi | focus on checking", explainer, root).complete(
            "where are we?"
        )

        assert reply.text == "Here is the status."
        assert "QUESTION: focus on checking" in explainer.prompts[0]
        # The facts came off disk before the model saw them -- that ordering is
        # what stops the explanation from inventing status.
        assert "not_started" in explainer.prompts[0]
        assert tracker.state_path(root).exists()


async def test_ordinary_reply_passes_through_and_never_calls_the_explainer():
    with tempfile.TemporaryDirectory() as root:
        explainer = ScriptedModel([])
        reply = await _routing("just chatting, no route", explainer, root).complete("hello")

        assert reply.text == "just chatting, no route"
        assert not explainer.prompts
        assert not tracker.state_path(root).exists()


async def test_unknown_route_name_lists_michi_among_the_others():
    with tempfile.TemporaryDirectory() as root:
        reply = await _routing("ROUTE: bogus", ScriptedModel([]), root).complete("go")

        assert all(name in reply.text for name in ("michi", "parser", "categorization"))


async def test_another_specialists_route_never_reaches_michi():
    """The map dispatches by name: a parser route must not collect a snapshot."""
    with tempfile.TemporaryDirectory() as root:
        explainer = ScriptedModel([])
        reply = await _routing("ROUTE: parser", explainer, root).complete("pull the rows")

        assert "our statement parser" in reply.text
        assert not explainer.prompts
        assert not tracker.state_path(root).exists()


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))
