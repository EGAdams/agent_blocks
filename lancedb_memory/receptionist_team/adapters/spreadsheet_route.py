"""SpreadsheetRequestRoute -- "make me a spreadsheet" always reaches the maker.

WHY

The same argument StatusQuestionRoute makes for Mickey and FigureQuestionRoute
makes for the books, one step further along. Toyota can now write a real
spreadsheet out of the real records, and the failure that matters is her
answering the request in prose instead: a list of amounts in the chat window
looks like it came from the same place the file would have, and nobody can
open it, sort it, or hand it to a bookkeeper. A spoken total is also the one
kind of answer she has previously invented.

FigureQuestionRoute already refuses these requests -- "spreadsheet", "csv",
"xlsx" and "excel" are in its _BELONGS_ELSEWHERE veto, written when the agent
existed in the roster but had nothing behind it. That veto only kept the
request away from the wrong agent; it did not send it to the right one. This
is the other half.

HOW GENEROUS TO BE

Narrower than the figure route, because the two fail differently. An
over-eager figure route costs one honest "that isn't a question I can ask the
books yet". An over-eager spreadsheet route costs a file written to disk and
a sentence about a category the user was only talking about. So this fires
only when the words name an artifact -- a spreadsheet, a workbook, a csv, an
export -- and not merely a category.
"""
import re

from .latest_user import latest_user_message

# Naming the artifact, not just the subject. "the expenses for utilities" is
# a question for the books; "the expenses for utilities as a spreadsheet" is
# this. The artifact word is required, which is the whole discipline here.
_WANTS_A_FILE = re.compile(
    r"\b("
    r"spread\s?sheet|work\s?book|\.xlsx\b|xlsx\b|excel"
    r"|\.csv\b|\bcsv\b"
    r"|export (it|them|this|that|the)\b|export of\b"
    r")",
    re.IGNORECASE | re.DOTALL,
)

# Talking ABOUT spreadsheets rather than asking for one. Checked first.
#
# The reshaper veto is the load-bearing one: spreadsheet_reshaper takes an
# existing combined file and splits it into a column per person, so "reshape
# this spreadsheet" names the same artifact word for a completely different
# job. Routing it here would build a fresh sheet from the database and report
# success, which is the worst shape of wrong -- a real file, honestly
# described, that is not what was asked for.
_NOT_A_REQUEST = re.compile(
    r"\b("
    r"reshape|re-?shape|split .{0,20}\b(column|per person)|one column per"
    r"|column per person|combined (spread\s?sheet|file)"       # spreadsheet_reshaper
    r"|how (do|does|did|would|can) (i|you|we)\b"               # asking how, not asking for
    r"|what (is|are) a .{0,15}spread\s?sheet"
    r"|the spread\s?sheet (i|you|we) (sent|made|built|gave)"   # one that already exists
    r"|open (the|that|this|my) .{0,15}(spread\s?sheet|csv|xlsx|file)"  # browser_opener
    r"|vendor_monthly_spend|practice|training|synthetic|900001"  # the tax assignment
    r")",
    re.IGNORECASE | re.DOTALL,
)


class SpreadsheetRequestRoute:
    """Forces one agent when the latest user message asks for a sheet.

    Same IRouteOverride shape as StatusQuestionRoute and FigureQuestionRoute,
    and stateless for the same reason -- profiles.py holds the instance
    directly rather than behind a factory, because there is nothing to build.
    """

    def __init__(self, agent_name: str) -> None:
        self._agent_name = agent_name

    def forced_agent(self, prompt: str) -> str | None:
        question = self.question(prompt)
        if not question or _NOT_A_REQUEST.search(question):
            return None
        return self._agent_name if _WANTS_A_FILE.search(question) else None

    def question(self, prompt: str) -> str:
        """The latest user message. See adapters/latest_user.py for why only
        the last one, and why a prompt with no marker yields "" rather than
        the whole text."""
        return latest_user_message(prompt)
