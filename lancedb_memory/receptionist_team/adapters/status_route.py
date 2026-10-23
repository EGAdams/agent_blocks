"""StatusQuestionRoute -- "where does the work stand?" always reaches Mickey.

Mickey's tracker reads the report files off disk with no model involved, so her
snapshot is the only thing in the package that actually knows the status. A
receptionist answering that question from recalled conversation is guessing
with a confident voice -- and Mazda did exactly that, reporting a year of
finance reports as complete and verified when not one of them existed, then
storing the claim where recall handed it back as history.

The system prompt tells her to route every status question to Mickey. This
makes it true whether or not the model cooperates, which is the same reason
tracker.py collects the facts before any model sees them: the important part
of the answer shouldn't depend on a model choosing well.
"""
import re

from .latest_user import latest_user_message

# Asking where things stand, in the wordings people actually use.
_STATUS_QUESTION = re.compile(
    r"\b("
    r"where (do|does|are|is) .{0,40}\b(stand|at)\b"
    r"|what('s| is| are)? .{0,30}\b(status|progress|outstanding|left|remaining)\b"
    r"|status (update|report|of|on)"
    r"|progress (update|report|on)"
    r"|(which|what) .{0,40}\b(still |are )?(missing|outstanding|not started|incomplete|done|left)\b"
    r"|how (far|much) .{0,30}\b(along|done|left)\b"
    r"|(where|how) (are we|did we leave)"
    r"|are .{0,40}\b(reports?|statements?)\b .{0,20}\b(done|finished|complete)\b"
    r")",
    re.IGNORECASE | re.DOTALL,
)


class StatusQuestionRoute:
    """Forces one agent when the latest user message asks about status."""

    def __init__(self, agent_name: str) -> None:
        self._agent_name = agent_name

    def forced_agent(self, prompt: str) -> str | None:
        return self._agent_name if _STATUS_QUESTION.search(self.question(prompt)) else None

    def question(self, prompt: str) -> str:
        """The latest user message. See adapters/latest_user.py for why only
        the last one, and why a prompt with no marker yields "" rather than
        the whole text."""
        return latest_user_message(prompt)
