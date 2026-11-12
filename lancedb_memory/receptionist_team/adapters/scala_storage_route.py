"""ScalaStorageRoute -- filing a scan on BATI, R46 or SQMC goes to bridge_scala_storage.

Scala's answer route (adapters/scala_route.py) fires on the word "Scala", which is right for an answer to one
of her to-do questions ("Scala, file #7 under documents_511/insurance") and wrong for "Scala, copy
scan_01M7... to BATI's insurance folder": that is a request to the Bridge's Scala storage agent, which only
the bridge_scala_storage specialist can send -- after reading it back and getting a yes.

So this goes just ahead of it, on the latest user message only, and fires when the message names a scan id,
or names one of the three machines together with a filing word (and no deploy, log or print word). A to-do number ("#7", "to-do 7") is left to
Scala's answer route, because that is the answer to a question she asked.
"""
import re

from .latest_user import latest_user_message

_SCAN_ID = re.compile(r"\bscan_[0-9A-Za-z]{26}\b")
_MACHINE = re.compile(r"\b(?:bati|r46|rosemary\s?46|sqmc)\b|"
                      r"\bmom'?s\s+(?:computer|machine|pc|cabinet|file\s+cabinet|folders?)\b", re.I)
_FILING = re.compile(r"\b(?:file|filed|filing|cop(?:y|ied)|store|stored|put|save|saved|send|verify|check|"
                     r"retrieve|fetch|get\s+back|folders?|cabinet|storage|reachable|up)\b", re.I)
_TODO = re.compile(r"#\s?\d+\b|\bto-?do\b", re.I)
# "Check SQMC's dashboard logs" names a machine and "check", and belongs to deploy, not to filing.
_ELSEWHERE = re.compile(r"\b(?:deploy\w*|restart|logs?|dashboard|services?|print\w*|browser|notes?)\b", re.I)


def storage_request(text: str) -> bool:
    if not text or _TODO.search(text):
        return False
    if _SCAN_ID.search(text):
        return True
    return bool(_MACHINE.search(text) and _FILING.search(text) and not _ELSEWHERE.search(text))


class ScalaStorageRoute:
    def __init__(self, agent_name: str = "bridge_scala_storage") -> None:
        self._agent = agent_name

    def forced_agent(self, prompt: str) -> str | None:
        return self._agent if storage_request(latest_user_message(prompt)) else None

    def question(self, prompt: str) -> str:
        return latest_user_message(prompt)
