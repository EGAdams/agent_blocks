"""AdwLiveFinanceRunner -- an IDelegationRunner that fires the Live Finance ADW.

Deterministic first, the same posture as MickeyRunner and AdwBrowserRunner: the
work is a subprocess, the reply is the one line that subprocess wrote, and no
model stands between the two.

That order is the entire point here. The failure being fixed is Toyota
answering a question about real money with a plausible invented figure, so the
one thing this runner must not do is give a model the chance to restate the
number in its own words. The ADW's `report` phase has the question read its own
rows back, this returns that sentence unchanged, and RoutingModel hands it to
the engine as the reply. A figure the books did not return cannot appear.

A deliberate consequence: the reply reads a little more like a ledger than like
Toyota. That is the right trade. Her system prompt tells her to pass real
figures through exactly as given, and a number she has rephrased is a number
nobody can check.
"""
import asyncio
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from agent_memory.models import AgentReply

# The workspace root -- adws/ is resolved relative to it, and the ADW's own
# default --config path ("adws/adw_sssf_config/...") assumes it as the cwd.
WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
ADW_SCRIPT = WORKSPACE_ROOT / "adws" / "adw_interface_live_finance.py"

# A grouped query over ~2,500 rows on a local MySQL is milliseconds; this is
# the "something is stuck" ceiling, not an expected duration. A receptionist
# must not hang on it.
TIMEOUT_SECONDS = 90.0

# Which organization's books. The ADW resolves this to an id by
# organizations.type rather than a pinned number, so this stays the readable
# name of a choice instead of a magic 1.
ORG_TYPE = "NONPROFIT"

# A four-digit year in the request. Narrow on purpose: a dollar amount, a count
# and a record number are all digits too, so only something that actually
# looks like a recent calendar year is read as one.
_YEAR = re.compile(r"\b(20[12]\d)\b")

# How many rows a grouped question may report. Twelve so "by month" covers a
# year without the spoken line running on past where anyone is still listening.
DEFAULT_LIMIT = 12


def year_of(request: str) -> int | None:
    """The year the request names, or None for "all of it".

    A plain function rather than a method so it can be read and tested on its
    own. Returns None when more than one year appears: "compare 2025 with
    2026" is a question this ADW does not install, and silently picking the
    first would answer a different question than the one asked.
    """
    found = _YEAR.findall(request)
    return int(found[0]) if len(set(found)) == 1 else None


class AdwLiveFinanceRunner:
    """Runs the Live Finance ADW and reports the line it wrote. Nothing else."""

    def __init__(self, python: str | None = None, org_type: str = ORG_TYPE) -> None:
        # This interpreter by default: the ADW is a `uv run` script with a
        # PEP 723 header, but every dependency it imports is already in our
        # venv, and `uv run` would resolve a fresh environment on the user's
        # first question about the books.
        self._python = python or sys.executable
        self._org_type = org_type

    async def run(
        self, role: object, agent_name: str, model: str, extra: str = ""
    ) -> AgentReply:
        question = extra.strip()
        if not question:
            return AgentReply(
                text=(
                    "I didn't catch what to look up in the books. Ask me for a "
                    "total, a breakdown by month or category, the top vendors, "
                    "or what's missing a receipt."
                )
            )
        return AgentReply(text=await asyncio.to_thread(self._ask, question))

    def _ask(self, question: str) -> str:
        with tempfile.TemporaryDirectory() as scratch:
            line_file = Path(scratch) / "live_finance_line.txt"
            argv = [
                self._python, str(ADW_SCRIPT),
                f"Toyota is reading the live books for the user: {question}",
                "--question", question,
                "--org-type", self._org_type,
                "--limit", str(DEFAULT_LIMIT),
                "--message-out", str(line_file),
            ]
            year = year_of(question)
            if year is not None:
                argv += ["--year", str(year)]
            try:
                done = subprocess.run(
                    argv, cwd=WORKSPACE_ROOT, capture_output=True, text=True,
                    timeout=TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                return (
                    f"The books didn't answer within {int(TIMEOUT_SECONDS)} "
                    "seconds, so I have no figure for you. I'd rather say that "
                    "than guess one."
                )
            # The file is the contract; the exit code only says whether the ADW
            # accepted the run. An unmatched question exits 0 with a useful
            # line, and a matched question that returned no rows exits non-zero
            # with a line worth repeating, so the message wins either way and
            # the exit code matters only when there is no message at all.
            if line_file.exists():
                return line_file.read_text().strip()
            detail = (done.stderr or done.stdout or "").strip().splitlines()
            return (
                "I couldn't reach the books just now, so I have no figure for "
                "you -- and I'm not going to estimate one. "
                + (f"The workflow said: {detail[-1]}" if detail else "It said nothing.")
            )
