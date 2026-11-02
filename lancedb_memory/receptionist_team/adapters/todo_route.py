"""CompanyTodoRoute -- anything said about the company to-do list goes to the list.

Same failure the figure route defends against: asked what was on a list she
had no way to read, a receptionist answers from recalled conversation, and
"we need a company to-do list" said once becomes, a week later, a list she
recites. So the route is not left to the model. It fires on the latest user
message only (adapters/latest_user.py), and only when that message names the
to-do list -- the ADW then decides add / list / done, and an unclear request
changes nothing.

It goes first in Toyota's FirstRouteOpinion: "add check what we spent on
utilities to the to-do list" is word for word also a figure question, and
what was asked for is an item, not a total.
"""
import re

from .latest_user import latest_user_message

_TODO = re.compile(r"\bto[\s-]?do(?:s|\s+list)?\b|\btask\s+list\b", re.I)
# Talk about having or building a list is not a request to the list. "We need
# a company to-do list" is the sentence that started this, and the right
# answer to it is a conversation, not an empty list read back.
_ABOUT_THE_LIST = re.compile(r"\b(?:need|want|build|make|create|set\s+up|start)\s+(?:a|an)\s+(?:company\s+|office\s+)?to[\s-]?do\s+list\b", re.I)


class CompanyTodoRoute:
    def __init__(self, agent_name: str = "company_todos") -> None:
        self._agent = agent_name

    def forced_agent(self, prompt: str) -> str | None:
        message = latest_user_message(prompt)
        if _TODO.search(message) and not _ABOUT_THE_LIST.search(message):
            return self._agent
        return None

    def question(self, prompt: str) -> str:
        return latest_user_message(prompt)
