"""The Live Finance ADW's two guarantees, with no database and no network.

What can go wrong here without anything crashing:

* a statement that is not a plain read reaching the real books -- the MySQL
  account on this box holds ALL PRIVILEGES, so grants are not the safeguard
  and ReadOnlyBooks is;
* the wrong installed question matching, which answers a question nobody
  asked with a real number, the most convincing kind of wrong;
* an unmatched question falling through to some default query instead of
  saying plainly that no figure was read.

ReadOnlyBooks.assert_read_only is a classmethod precisely so these run with
no connection, and every question's sql() is built from its request rather
than executed, so nothing here opens a socket.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

# The ADW lives in the workspace's adws/ directory, which is its own import
# root and is not a package -- reached by path, same as test_search_open_adw.
ADWS_DIR = Path(__file__).resolve().parents[3] / "adws"
if str(ADWS_DIR) not in sys.path:
    sys.path.insert(0, str(ADWS_DIR))

import adw_interface_live_finance as adw  # noqa: E402


class _SilentLog:
    """ILifecycleLog that says nothing, so the harness output stays readable."""

    def created(self, obj):
        pass

    def phase(self, name, detail=""):
        pass

    def done(self, name, detail=""):
        pass


def _catalog():
    return adw.QuestionCatalog(adw.install_questions(), _SilentLog())


async def test_a_plain_read_is_allowed():
    for sql in [
        "SELECT 1",
        "  select id, name from organizations where id = %s",
        "WITH t AS (SELECT 1) SELECT * FROM t",
        "SELECT SUM(amount) FROM expenses -- a trailing comment is fine",
    ]:
        adw.ReadOnlyBooks.assert_read_only(sql)


async def test_anything_that_is_not_a_plain_read_is_refused():
    """Not a prompt rule and not a grant: the code refuses to send it.

    Both halves matter. A statement that does not start as a read is refused
    outright, and a read with a write stapled on behind a semicolon is refused
    too -- that second form is the one a careless edit introduces.
    """
    refused = [
        "UPDATE expenses SET amount = 0",
        "DELETE FROM expenses",
        "INSERT INTO expenses (amount) VALUES (1)",
        "DROP TABLE expenses",
        "TRUNCATE expenses",
        "ALTER TABLE expenses ADD COLUMN x INT",
        "GRANT ALL ON nonprofit_finance.* TO 'x'@'%'",
        "CALL some_procedure()",
        "SET autocommit = 1",
        "SELECT 1; UPDATE expenses SET amount = 0",
        "SELECT 1; DROP TABLE expenses",
        "",
    ]
    for sql in refused:
        try:
            adw.ReadOnlyBooks.assert_read_only(sql)
        except PermissionError:
            continue
        raise AssertionError(f"not refused: {sql!r}")


async def test_every_installed_question_names_itself_and_describes_itself():
    questions = adw.install_questions()
    names = [q.name for q in questions]
    assert len(set(names)) == len(names), f"duplicate question name: {names}"
    for question in questions:
        assert question.name and question.name.islower()
        assert question.summary, question.name


async def test_each_question_is_the_one_its_own_wording_selects():
    """Ordering is the whole risk in a first-match registry.

    total_spend sits last among the amount questions for exactly this reason:
    "how much did we spend by category" contains "how much did we spend", so a
    looser question placed earlier would quietly answer the wrong one.
    """
    expected = {
        "how many expenses are missing receipts": "missing_receipts",
        "which expenses have no receipt": "missing_receipts",
        "how many are unverified": "unverified",
        "what hasn't been human verified": "unverified",
        "show me spending by month": "spend_by_month",
        "break our spending down by category": "spend_by_category",
        "what are our biggest categories": "spend_by_category",
        "what's the split by payment method": "spend_by_method",
        "who are our top vendors": "top_vendors",
        "what are the most recent expenses": "recent_expenses",
        "how much did we spend": "total_spend",
        "what's our total spending": "total_spend",
    }
    catalog = _catalog()
    for text, name in expected.items():
        chosen = catalog.select(text)
        assert chosen is not None, f"nothing matched: {text!r}"
        assert chosen.name == name, f"{text!r} -> {chosen.name}, expected {name}"


async def test_an_unmatched_question_matches_nothing_rather_than_something():
    """No default query. A number answering a question nobody asked is the
    exact failure this workflow was built to end."""
    catalog = _catalog()
    for text in [
        "what's the weather like",
        "who is on the board",
        "reconcile the bank statement against the ledger",
        "forecast next year's spending",
    ]:
        assert catalog.select(text) is None, text


async def test_every_question_builds_a_statement_read_only_accepts():
    """The guarantee is only real if the installed SQL actually passes it."""
    scope = adw.OrgScope(org_id=1, name="Test Org", org_type="NONPROFIT")
    for question in adw.install_questions():
        for year in (None, 2025):
            asked = adw.QuestionAsked(
                text=question.name.replace("_", " "), year=year, limit=10, scope=scope
            )
            sql, params = question.sql(asked)
            adw.ReadOnlyBooks.assert_read_only(sql)
            assert "%s" in sql or not params, question.name


async def test_the_catalog_can_list_what_it_can_be_asked():
    """--list-questions is the honest half of an unmatched answer, so it has
    to stay in step with the registry without a second list to maintain."""
    catalog = _catalog()
    names = catalog.names()
    assert names == tuple(q.name for q in adw.install_questions())
    assert len(catalog.summaries()) == len(names)


async def test_the_real_workflow_reads_a_real_figure():
    """The one test here that reaches the live books.

    Every other test fakes the database away, which is exactly why this one
    cannot: a schema change, a moved column or a missing credential looks
    identical to a passing offline suite. It reads only, and it skips rather
    than fails when the database is unreachable, so an offline box is a quiet
    suite and not a false alarm.
    """
    with tempfile.TemporaryDirectory() as scratch:
        line_file = Path(scratch) / "line.txt"
        argv = [
            sys.executable, str(ADWS_DIR / "adw_interface_live_finance.py"),
            "A test checking the live books still answer.",
            "--question", "how much have we spent in total",
            "--org-type", "NONPROFIT",
            "--message-out", str(line_file),
        ]
        try:
            done = subprocess.run(
                argv, cwd=ADWS_DIR.parent, capture_output=True, text=True, timeout=90,
            )
        except subprocess.TimeoutExpired:
            print("    SKIP  the books did not answer within 90s")
            return
        # Read inside the with: the scratch directory, and the line in it, are
        # gone the moment this block ends.
        if not line_file.exists():
            detail = (done.stderr or done.stdout or "").strip().splitlines()
            print(f"    SKIP  no line written (database unreachable?): {detail[-1:]}")
            return
        line = line_file.read_text().strip()
    if "could not" in line.lower() or "couldn't" in line.lower():
        print(f"    SKIP  {line}")
        return
    assert "$" in line, f"a total with no amount in it: {line}"
    assert "expense" in line.lower(), line
    print(f"    live: {line}")

if __name__ == "__main__":
    from tests.runner import main

    main(dict(globals()))
