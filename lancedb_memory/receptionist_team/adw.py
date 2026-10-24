"""The interfaces a receptionist uses to reach an ADW, and nothing else.

Three of Toyota and Mazda's specialists are not agents at all: they are
`adws/adw_interface_*.py` workflows run as subprocesses, whose one written
line IS the reply. That arrangement is deliberate -- a figure, a path or a
"the browser opened" must not pass through a model that could rephrase it --
but it was arranged three times, once per runner, and each copy knew about
subprocess, tempfile, the `--message-out` contract and how to apologize for a
timeout.

So the arrangement is named here instead. `IAdwWorkflow` is "run this
workflow and give me the line it wrote"; `IAdwFlagBuilder` is "spell this
request as that workflow's flags"; `IAdwFailureVoice` is "say, in this
receptionist's voice, that no line came back". A runner composes the three
and contains only its own judgement.

Protocol-only and import-light, matching agent_memory/ports.py and
delegation.py: nothing here imports subprocess, and the implementations live
under adapters/. The existing AdwBrowserRunner and AdwLiveFinanceRunner still
carry their own copies of the mechanism; they can adopt these interfaces
without either of them changing behaviour, which is the point of writing the
interfaces down before there is a fourth copy.
"""
from collections.abc import Sequence
from typing import Protocol


class IAdwFlagBuilder(Protocol):
    """Turn one understood request into the command-line flags that express it.

    Separate from the workflow runner because these are the two things that
    change for different reasons: how a request is spelled is a fact about one
    ADW's CLI, and how a subprocess is driven is a fact about every ADW's
    contract. A new flag is an edit here and nowhere else.
    """

    def purpose(self, ask: object) -> str:
        """The positional prompt -- why this is being run, for the run log."""
        ...

    def flags(self, ask: object) -> list[str]:
        """Everything after the prompt. Never includes --message-out: that
        belongs to the caller, which is the only thing that knows where the
        line should land."""
        ...


class IAdwFailureVoice(Protocol):
    """What to say when the workflow returned no line at all.

    A voice rather than a string because the same silence has to be reported
    differently by different receptionists and for different work: "I have no
    figure for you" and "I can't tell you whether it opened" are the same
    event. Keeping the wording out of the subprocess runner is what lets one
    runner serve all of them.
    """

    def timed_out(self, seconds: float) -> str: ...

    def no_report(self, detail: str) -> str: ...


class IAdwWorkflow(Protocol):
    """Run one ADW to completion and return the single line it wrote.

    The contract every ADW here already honours: it writes the operator's one
    line to `--message-out`, and that file -- not the exit code -- is the
    answer. A workflow that accepted nothing still has something true to say
    ("no installed question matched", "that name matches nine categories"),
    so the line wins whenever there is one.

    This never raises. A caller is a receptionist mid-sentence; a traceback
    there would be reported to the user as the workflow having broken, when
    what actually happened is usually something the workflow itself explained.
    """

    def line(self, purpose: str, flags: Sequence[str]) -> str: ...
