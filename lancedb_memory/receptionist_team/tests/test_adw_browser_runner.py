"""Mazda firing the Open Browser ADW.

The runner is deliberately thin: one judgement (address or search phrase) and
one subprocess whose written line becomes the reply verbatim. These pin both,
plus the three ways the subprocess can fail to tell us anything -- because a
receptionist who can't tell what happened has to say so, not guess.

Only the last test spawns the real ADW, and it does so with --dry-run, so
nothing here opens a browser window.
"""
from pathlib import Path

from ..adapters.adw_runner import ADW_SCRIPT, AdwBrowserRunner, target_of
from ..adapters.routing_model import RoutingModel
from ..mazda_roles import BROWSER_AGENT_NAME, MAZDA_ROSTER
from ..profiles import MAZDA
from agent_memory.models import AgentReply


class _RouteOnce:
    def __init__(self, text):
        self._text = text

    async def complete(self, prompt):
        return AgentReply(text=self._text)


async def test_an_address_becomes_a_url_and_everything_else_a_search():
    assert target_of("https://docs.lance.dev/x?a=1") == ("https://docs.lance.dev/x?a=1", "")
    # A bare domain needs the scheme added or nothing will open it.
    assert target_of("youtube.com") == ("https://youtube.com", "")
    assert target_of("github.com/anthropics/claude-code") == (
        "https://github.com/anthropics/claude-code", "",
    )
    assert target_of("lancedb vector search") == ("", "lancedb vector search")
    # Being wrong toward "search" is harmless; being wrong toward "url" sends
    # a sentence to a URL validator. So anything unrecognised is a search.
    assert target_of("what is a vector index") == ("", "what is a vector index")


async def test_the_politeness_around_the_request_is_not_searched_for():
    assert target_of("please open youtube.com for me") == ("https://youtube.com", "")
    assert target_of("google lancedb vector search") == ("", "lancedb vector search")
    assert target_of("look up pytest fixtures please") == ("", "pytest fixtures")
    assert target_of("search for 'edge tts voices' in the browser") == (
        "", "edge tts voices",
    )


async def test_an_empty_request_asks_rather_than_opening_something_random():
    reply = await AdwBrowserRunner().run(None, BROWSER_AGENT_NAME, "unused", extra="open")
    assert "didn't catch what to open" in reply.text


async def test_the_line_the_workflow_wrote_is_the_reply_verbatim():
    # Nothing rephrases it: the workflow's own sentence is the only evidence
    # that anything happened, and Mazda must not improve on it.
    runner = AdwBrowserRunner(python=_fake_adw_writing("Opened via rundll32.exe: youtube.com"))
    reply = await runner.run(None, BROWSER_AGENT_NAME, "unused", extra="youtube.com")
    assert reply.text == "Opened via rundll32.exe: youtube.com"


async def test_a_workflow_that_reports_nothing_is_reported_as_unknown():
    # Never "it's open" when we can't tell. The ADW's own invariant is that it
    # never raises, so silence here means something broke upstream of it.
    runner = AdwBrowserRunner(python=_fake_adw_writing(None, stderr="ModuleNotFoundError: sssf_roles"))
    reply = await runner.run(None, BROWSER_AGENT_NAME, "unused", extra="youtube.com")
    assert "didn't report back" in reply.text
    assert "ModuleNotFoundError: sssf_roles" in reply.text


async def test_a_hung_workflow_times_out_instead_of_hanging_the_turn():
    runner = AdwBrowserRunner(python=_fake_adw_writing(None, sleep=5))
    runner_timeout = 0.5
    import receptionist_team.adapters.adw_runner as module
    was, module.TIMEOUT_SECONDS = module.TIMEOUT_SECONDS, runner_timeout
    try:
        reply = await runner.run(None, BROWSER_AGENT_NAME, "unused", extra="youtube.com")
    finally:
        module.TIMEOUT_SECONDS = was
    assert "didn't finish within" in reply.text


async def test_a_route_line_reaches_the_runner_through_mazdas_stack():
    seen = {}

    class _Recording:
        async def run(self, role, agent_name, model, extra=""):
            seen.update(agent=agent_name, extra=extra)
            return AgentReply(text="ok")

    model = RoutingModel(
        _RouteOnce(f"ROUTE: {BROWSER_AGENT_NAME} | lancedb vector search"),
        model="unused",
        roster=MAZDA_ROSTER,
        runner=_Recording(),
        speaker="Mazda",
        override=MAZDA.route_override,
    )
    reply = await model.complete("look up lancedb vector search")

    assert seen == {"agent": BROWSER_AGENT_NAME, "extra": "lancedb vector search"}
    assert reply.text == "ok"


async def test_her_profile_really_wires_the_adw_runner_to_that_name():
    runner = MAZDA.build_runner()
    assert type(runner._runners[BROWSER_AGENT_NAME]).__name__ == "AdwBrowserRunner"


async def test_the_real_workflow_still_answers_on_a_dry_run():
    # The one test that spawns the actual ADW. --dry-run resolves a launcher
    # and deliberately opens nothing, so this pins that our argv and the
    # --message-out contract still match the script as it is written today.
    assert ADW_SCRIPT.exists(), ADW_SCRIPT
    reply = await AdwBrowserRunner(dry_run=True).run(
        None, BROWSER_AGENT_NAME, "unused", extra="google lancedb vector search",
    )
    assert "Dry run" in reply.text
    assert "lancedb+vector+search" in reply.text


def _fake_adw_writing(line, stderr="", sleep=0):
    """A stand-in interpreter: reads --message-out from argv and obeys.

    Faking the interpreter rather than the subprocess call keeps the real
    argv, cwd and file-reading in the test -- which is where the mistakes are.
    """
    import tempfile

    script = Path(tempfile.mkdtemp()) / "fake_python.py"
    body = f"""#!/usr/bin/env python3
import sys, time
argv = sys.argv[1:]
time.sleep({sleep!r})
if {stderr!r}:
    sys.stderr.write({stderr!r})
if {line!r} is not None:
    out = argv[argv.index("--message-out") + 1]
    open(out, "w").write({line!r} + "\\n")
"""
    script.write_text(body)
    wrapper = script.parent / "python"
    wrapper.write_text(f"#!/bin/sh\nexec {Path(__import__('sys').executable)} {script} \"$@\"\n")
    wrapper.chmod(0o755)
    return str(wrapper)


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))
