"""AdwBrowserRunner -- an IDelegationRunner that fires the Open Browser ADW.

Deterministic first, same posture as MickeyRunner: the work is a subprocess, the
reply is the one line that subprocess wrote, and no model stands between the
two. There is nothing here for a model to embellish, which matters more for
Mazda than for Toyota -- her standing rule is never to claim something ran
unless a tool confirmed it, and here the tool's own sentence *is* the answer.

Toyota reaches the same ADW through her `browser_opener` agent folder, which
spends a Claude Agent SDK turn with Bash deciding --url versus --phrase and
then reads the same file. That's the right shape when the agent might also need
to look around first. For "open this", it is a subprocess spawn, a
bypassPermissions Claude turn, and a few cents to reach a line we can read
ourselves -- so this runner skips all of it.

The ADW's two invariants carry the weight: it never blocks (the browser is a
detached spawn) and it never raises (a bad URL or a missing launcher comes back
as a message, not a traceback). `--message-out` is the contract for exactly
this kind of non-terminal caller.
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
ADW_SCRIPT = WORKSPACE_ROOT / "adws" / "adw_interface_browser_open.py"

# How long to wait on a script whose own job takes about a second. It spawns
# the browser detached and returns, so overrunning this means something is
# stuck rather than slow, and a receptionist must not hang on it.
TIMEOUT_SECONDS = 60.0

# What counts as an address rather than something to search for. Deliberately
# narrow: anything it doesn't recognise becomes a search phrase, which is the
# harmless direction to be wrong in. The ADW validates the URL properly anyway
# and falls back to --phrase itself when it can't open one.
_LOOKS_LIKE_URL = re.compile(
    r"^(https?://\S+$|(?:www\.)?[\w-]+(?:\.[\w-]+)+(?:[/:?#]\S*)?$)",
    re.IGNORECASE,
)

# Wrappers people put around what they actually want opened. Stripped so the
# search phrase is "lancedb vector search" and not "please open lancedb vector
# search for me", which searches for the politeness too.
_LEAD_IN = re.compile(
    r"^(please\s+)?(go\s+to|open\s+up|open|launch|visit|bring\s+up|pull\s+up|"
    r"search\s+(for|up)?|look\s+up|google|find)(\s+|$)",
    re.IGNORECASE,
)
_TRAILING = re.compile(
    r"\s+(for\s+me|please|in\s+(the\s+)?browser|in\s+a\s+(new\s+)?tab)\s*$",
    re.IGNORECASE,
)


def target_of(request: str) -> tuple[str, str]:
    """Split a request into the ADW's two flags: ( url, phrase ).

    Exactly one is ever non-empty. A plain function rather than a method so
    the decision can be read and tested on its own -- it is the only judgement
    this runner makes.
    """
    wanted = _TRAILING.sub("", _LEAD_IN.sub("", request.strip())).strip()
    wanted = wanted.strip("\"'").rstrip(".!?").strip()
    if not wanted:
        return "", ""
    if _LOOKS_LIKE_URL.match(wanted):
        # A bare domain needs a scheme before anything will open it.
        return (wanted if "://" in wanted else f"https://{wanted}"), ""
    return "", wanted


class AdwBrowserRunner:
    """Runs the Open Browser ADW and reports the line it wrote. Nothing else."""

    def __init__(self, python: str | None = None, dry_run: bool = False) -> None:
        # This interpreter by default: the ADW is a `uv run` script with a
        # PEP 723 header, but every dependency it actually imports is already
        # in our venv, and `uv run` would resolve a fresh environment on a
        # user's first browser request.
        self._python = python or sys.executable
        self._dry_run = dry_run

    async def run(
        self, role: object, agent_name: str, model: str, extra: str = ""
    ) -> AgentReply:
        url, phrase = target_of(extra)
        if not url and not phrase:
            return AgentReply(
                text=(
                    "I didn't catch what to open. Give me a web address or "
                    "something to search for and I'll open it."
                )
            )
        return AgentReply(text=await asyncio.to_thread(self._launch, url, phrase))

    def _launch(self, url: str, phrase: str) -> str:
        with tempfile.TemporaryDirectory() as scratch:
            line_file = Path(scratch) / "browser_open_line.txt"
            argv = [
                self._python, str(ADW_SCRIPT),
                f"Mazda is opening this for the user: {url or phrase}",
                "--message-out", str(line_file),
            ]
            argv += ["--url", url] if url else ["--phrase", phrase]
            if self._dry_run:
                argv.append("--dry-run")
            try:
                done = subprocess.run(
                    argv, cwd=WORKSPACE_ROOT, capture_output=True, text=True,
                    timeout=TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                return (
                    f"The browser workflow didn't finish within "
                    f"{int(TIMEOUT_SECONDS)} seconds, so I can't tell you "
                    "whether it opened."
                )
            # The file is the contract; the exit code only says whether the ADW
            # accepted the run. A non-zero exit with a message still has
            # something true to report ("no launcher found"), so the message
            # wins and the exit code only matters when there is no message.
            if line_file.exists():
                return line_file.read_text().strip()
            detail = (done.stderr or done.stdout or "").strip().splitlines()
            return (
                "The browser workflow didn't report back, so I don't know "
                "whether it opened. "
                + (f"It said: {detail[-1]}" if detail else "It said nothing.")
            )
