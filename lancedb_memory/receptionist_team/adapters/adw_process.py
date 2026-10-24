"""SubprocessAdwWorkflow -- the one class that knows an ADW is a subprocess.

Implements adw.IAdwWorkflow. Everything specific to a particular workflow --
which script, which flags, how to apologize -- is injected, so this holds only
the part that is the same every time: spawn it with a `--message-out` file,
wait, and return what it wrote.

The `--message-out` file is the contract, not the exit code. Every ADW this
package calls writes its operator line there whether or not the run was
accepted, and an unaccepted run is routinely the most useful thing to repeat:
"no installed question matched", "that name matches nine categories, re-run
with --category-id". So the file wins when it exists, and the exit code only
matters when it does not.

Nothing here raises. A receptionist is mid-sentence when this runs.
"""
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from ..adw import IAdwFailureVoice

# The workspace root -- adws/ is resolved relative to it, and every ADW's own
# default --config path ("adws/adw_sssf_config/...") assumes it as the cwd.
WORKSPACE_ROOT = Path(__file__).resolve().parents[3]


class SpokenAdwFailure:
    """IAdwFailureVoice that names the work in the receptionist's own words.

    Two sentences, parameterized by what was being attempted ("a figure",
    "your spreadsheet"), because the honest apology has to say what the user
    is now not getting. Both end by refusing to fill the gap -- the failure
    these runners exist to prevent is a plausible answer offered when no tool
    produced one.
    """

    def __init__(self, work: str, lacking: str) -> None:
        self._work = work          # "the books", "the spreadsheet workflow"
        self._lacking = lacking    # "a figure for you", "a spreadsheet for you"

    def timed_out(self, seconds: float) -> str:
        return (
            f"{self._work} didn't answer within {int(seconds)} seconds, so I "
            f"have no {self._lacking}. I'd rather say that than guess."
        )

    def no_report(self, detail: str) -> str:
        return (
            f"I couldn't reach {self._work} just now, so I have no "
            f"{self._lacking} -- and I'm not going to invent one. "
            + (f"The workflow said: {detail}" if detail else "It said nothing.")
        )


class SubprocessAdwWorkflow:
    """Runs one ADW script and returns the line it wrote. Nothing else."""

    def __init__(
        self,
        script: str,
        voice: IAdwFailureVoice,
        timeout_seconds: float = 90.0,
        python: str | None = None,
    ) -> None:
        self._script = WORKSPACE_ROOT / "adws" / script
        self._voice = voice
        self._timeout = timeout_seconds
        # This interpreter by default: the ADWs are `uv run` scripts with PEP
        # 723 headers, but every dependency they import is already in our venv,
        # and `uv run` would resolve a fresh environment on the user's first
        # request. A caller that needs the declared environment -- openpyxl is
        # the first dependency not already here -- passes its own.
        self._python = python or sys.executable

    def line(self, purpose: str, flags: Sequence[str]) -> str:
        with tempfile.TemporaryDirectory() as scratch:
            line_file = Path(scratch) / "adw_line.txt"
            argv = [
                self._python, str(self._script), purpose,
                *flags,
                "--message-out", str(line_file),
            ]
            try:
                done = subprocess.run(
                    argv, cwd=WORKSPACE_ROOT, capture_output=True, text=True,
                    timeout=self._timeout,
                )
            except subprocess.TimeoutExpired:
                return self._voice.timed_out(self._timeout)
            except OSError as exc:
                # A missing interpreter or an unreadable script. Spoken, not
                # raised, for the same reason as everything else here.
                return self._voice.no_report(f"{type(exc).__name__}: {exc}")
            if line_file.exists():
                written = line_file.read_text().strip()
                if written:
                    return written
            detail = (done.stderr or done.stdout or "").strip().splitlines()
            return self._voice.no_report(detail[-1] if detail else "")
