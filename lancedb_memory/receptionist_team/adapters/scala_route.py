"""ScalaAnswerRoute -- EG's answers to Scala's filing questions go to Scala.

When Scala can't tell where a scan goes, she asks on the company to-do list
("#7 Scala needs a folder for ..."). The answer -- "Scala, file #7 under
documents_511/insurance" -- has to reach her workflow, not the model: a
model asked would happily say the scan was filed. So this route fires on the
latest user message only, when it names Scala, or names a scanned_documents
folder outright (bills_511/... or documents_511/...).

It goes ahead of the to-do route in Toyota's FirstRouteOpinion: "file to-do
#7 under bills_511/water" names the list, but what was asked for is a filing.
"""
import re

from .latest_user import latest_user_message

_SCALA = re.compile(r"\bscala\b|\b(?:bills|documents)_511\s*/", re.I)


class ScalaAnswerRoute:
    def __init__(self, agent_name: str = "scala_questions") -> None:
        self._agent = agent_name

    def forced_agent(self, prompt: str) -> str | None:
        return self._agent if _SCALA.search(latest_user_message(prompt)) else None

    def question(self, prompt: str) -> str:
        return latest_user_message(prompt)
