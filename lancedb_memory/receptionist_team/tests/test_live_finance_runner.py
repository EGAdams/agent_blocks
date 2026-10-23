"""AdwLiveFinanceRunner: the written line is the answer, and nothing else is.

The runner is three decisions, and each one is a way to report a figure that
the books did not return:

* an empty question must not become a query;
* the --message-out file outranks the exit code, because an unmatched question
  exits 0 with a useful line and a matched one that found no rows exits
  non-zero with a line worth repeating;
* a workflow that wrote no line at all must be reported as "no figure", never
  as a number and never as a guess.

The subprocess is replaced with a fake throughout, so nothing here touches the
database. The end-to-end run against the real books is in
test_live_finance_adw.py.
"""
import subprocess
from pathlib import Path

from ..adapters import live_finance_runner as runner_module
from ..adapters.live_finance_runner import AdwLiveFinanceRunner


class _FakeRun:
    """Stands in for subprocess.run: records argv, writes a line or doesn't."""

    def __init__(self, line=None, returncode=0, stderr="", raises=None):
        self.line = line
        self.returncode = returncode
        self.stderr = stderr
        self.raises = raises
        self.argv = None
        self.cwd = None

    def __call__(self, argv, cwd=None, capture_output=False, text=False, timeout=None):
        self.argv = argv
        self.cwd = cwd
        if self.raises is not None:
            raise self.raises
        if self.line is not None:
            out = Path(argv[argv.index("--message-out") + 1])
            out.write_text(self.line + "\n")
        return subprocess.CompletedProcess(argv, self.returncode, "", self.stderr)


def _flag(argv, name):
    return argv[argv.index(name) + 1] if name in argv else None


async def _ask(fake, question):
    original = runner_module.subprocess.run
    runner_module.subprocess.run = fake
    try:
        reply = await AdwLiveFinanceRunner().run(None, "live_books", "unused", question)
    finally:
        runner_module.subprocess.run = original
    return reply.text


async def test_an_empty_question_asks_the_user_instead_of_the_books():
    fake = _FakeRun(line="should never run")
    text = await _ask(fake, "   ")
    assert fake.argv is None, "an empty question must not reach the database"
    assert "didn't catch" in text


async def test_the_written_line_is_returned_word_for_word():
    """No rephrasing anywhere in the path. That is the whole design."""
    line = "ROL: $560,337.78 across 2,210 expenses, 1970-01-01 through 2028-08-07."
    fake = _FakeRun(line=line)
    assert await _ask(fake, "how much have we spent") == line


async def test_the_line_wins_over_a_non_zero_exit():
    line = "ROL: no expenses matched that, so I read no figure."
    fake = _FakeRun(line=line, returncode=3)
    assert await _ask(fake, "how much did we spend in 1999") == line


async def test_no_line_is_reported_as_no_figure():
    fake = _FakeRun(line=None, returncode=1, stderr="pymysql: can't connect")
    text = await _ask(fake, "how much have we spent")
    assert "no figure" in text
    assert "estimate" in text
    assert "can't connect" in text, "the operator needs the real reason"


async def test_a_timeout_is_reported_as_no_figure_too():
    fake = _FakeRun(raises=subprocess.TimeoutExpired(cmd="adw", timeout=90))
    text = await _ask(fake, "how much have we spent")
    assert "no figure" in text
    assert "guess" in text


async def test_the_question_is_passed_through_unrewritten():
    """The ADW's own matcher decides which question it is. A rewrite here is
    how a different question gets answered with a real number."""
    asked = "whats our biggest categories, roughly"
    fake = _FakeRun(line="x")
    await _ask(fake, asked)
    assert _flag(fake.argv, "--question") == asked
    assert _flag(fake.argv, "--org-type") == "NONPROFIT"


async def test_a_single_year_is_passed_and_two_years_are_not():
    fake = _FakeRun(line="x")
    await _ask(fake, "how much did we spend in 2025")
    assert _flag(fake.argv, "--year") == "2025"

    fake = _FakeRun(line="x")
    await _ask(fake, "compare 2025 with 2024")
    assert "--year" not in fake.argv, "a comparison is not an installed question"


async def test_it_runs_the_workflow_from_the_workspace_root():
    """The ADW's default --config path is relative to it."""
    fake = _FakeRun(line="x")
    await _ask(fake, "how much have we spent")
    assert fake.cwd == runner_module.WORKSPACE_ROOT
    assert (runner_module.WORKSPACE_ROOT / "adws").is_dir()
    assert runner_module.ADW_SCRIPT.exists()


if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))
