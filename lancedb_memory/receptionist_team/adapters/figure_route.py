"""FigureQuestionRoute -- a question about an actual figure always reaches the books.

WHY

Toyota's only data-shaped assignment was synthetic: one training expense,
#900001, $12.34, in `rol_receptionist_training`. Her capability text said so
on every turn. So when she was asked what the organization had actually spent,
there was no path to an answer and she produced a plausible one instead --
and that reply was stored, recalled, and re-asserted until it read like
established fact. Sixty-six turns of training talk were in her memory when
this was written.

Deleting those turns (receptionist_team/forget.py) stops the recall. It does
not stop the behaviour, because the cause was never the memory: she had no way
to read a real number. `adws/adw_interface_live_finance.py` gives her one, and
this makes the route to it not depend on the model agreeing to take it --
exactly the argument StatusQuestionRoute makes for Mickey, for the same
failure.

HOW GENEROUS TO BE

More generous than you would dare if this route ended at a model, because it
doesn't. The ADW has its own matcher over its installed questions, and when
nothing matches it says plainly that it read no figure and names what it CAN
be asked. So a false positive here costs one honest "that isn't a question I
can ask the books yet" instead of a fabricated amount -- which is strictly
better than the behaviour being replaced.

What the vetoes exist for is the opposite direction: requests that are about
real money but belong to another agent that does the job properly. A
spreadsheet goes to category_spreadsheet, which writes a workbook; the tax
assignment's own records go through a WORK command; a question about which
reports exist goes to progress_check. Routing any of those here would answer
a narrower question than the one asked, which is its own kind of wrong.
"""
import re

from .latest_user import latest_user_message

# Asking for a figure out of the books, in the wordings people actually use.
# Kept deliberately close to the ADW's own question triggers: this decides
# that the books are the right place to look, and the ADW decides which
# question was asked.
_FIGURE_QUESTION = re.compile(
    r"\b("
    r"how much\b.{0,40}\b(spen|spent|spending|paid|pay|cost|have we|did we|do we|is there|left)"
    r"|how many\b.{0,30}\b(expense|transaction|receipt|vendor|merchant|entr|categor)"
    # "what did we spend on facilities" asks for a figure without ever
    # saying "how much". Second person is left out on purpose: "did you
    # pay attention" is conversation, not a question for the books.
    r"|\b(did|do|have|has) (we|they|i)\b.{0,25}\b(spend|spent|spending|pay|paid|cost)"
    r"|\b(total|totals|subtotal|grand total)\b.{0,30}\b(spen|spent|spending|expense|amount|paid)"
    r"|\b(spen|spent|spending|expenses|outlay)\b.{0,30}\bby (month|category|vendor|merchant|method)"
    r"|\b(by|per|each) (month|category|vendor|merchant|payment method)\b"
    r"|\b(biggest|largest|top|most expensive)\b.{0,30}\b(categor|vendor|merchant|expense|spend)"
    r"|\b(missing|no|without)\b.{0,20}\breceipt"
    r"|\b(unverified|not verified|human.?verified)\b"
    r"|\b(recent|latest|last)\b.{0,25}\b(expense|transaction|purchase|entr)"
    r"|\bwhat('s| is| are| was| were)\b.{0,30}\b(in the books|on the books|the balance|our spending)"
    r"|\b(the|our|rol'?s?) (books|actuals|real numbers|real figures)\b"
    r"|\bwhere\b.{0,25}\b(money|funds) (go|going|went)\b"
    r")",
    re.IGNORECASE | re.DOTALL,
)

# Real-money requests that belong to a different agent, which does more than
# read one figure. Checked first, so the more capable agent keeps the request.
_BELONGS_ELSEWHERE = re.compile(
    r"\b("
    r"spreadsheet|workbook|\.csv\b|\.xlsx\b|excel"          # category_spreadsheet
    r"|900001|training|synthetic|practice"                  # the tax assignment
    r"|expense\s*#?\s*\d{4,}"                               # a specific record: WORK select
    r"|editor|edit form|change the categor|set (it|this one) to"
    r"|which reports?\b|report (exist|freshness|is ready)"  # progress_check
    r")",
    re.IGNORECASE | re.DOTALL,
)


class FigureQuestionRoute:
    """Forces one agent when the latest user message asks for a real figure.

    Same IRouteOverride shape as StatusQuestionRoute, and stateless for the
    same reason -- profiles.py holds the instance directly rather than behind
    a factory, because there is nothing to build.
    """

    def __init__(self, agent_name: str) -> None:
        self._agent_name = agent_name

    def forced_agent(self, prompt: str) -> str | None:
        question = self.question(prompt)
        if not question or _BELONGS_ELSEWHERE.search(question):
            return None
        return self._agent_name if _FIGURE_QUESTION.search(question) else None

    def question(self, prompt: str) -> str:
        """The latest user message. See adapters/latest_user.py for why only
        the last one, and why a prompt with no marker yields "" rather than
        the whole text."""
        return latest_user_message(prompt)
