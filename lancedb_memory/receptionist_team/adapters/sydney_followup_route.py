"""Two routes for Sydney's desk, both from her 2026-11-22 conversation, where she asked "what's the exact systemd
service name?" five times and never reached anyone who could answer.

* SonnetQuestionRoute -- "ask Sonnet", "maybe Sonnet knows", "get that from Sonnet" goes to ask_sonnet, with the
  question she had just asked spelled out, so Sonnet gets a whole question and not "that". Without it, "ask somebody
  from the Star Trek bridge, like maybe Sonnet knows" matched BridgeDeviceRoute's "Sonnet ... bridge" and reached the
  Bridge's coder with no task in it ("What would you like me to do?"), and "ask Sonnet ..." reached nobody: NamedRoute
  only knows the roster spelling ask_sonnet. Work for Sonnet -- build, fix, write, take care of -- is left alone; it
  goes through the post office, as her prompt says.
* ScannerChoiceRoute -- "freezer" or "the window one", answering her "which scanner?", is a scan on that scanner.
  The scanner specialist asks that when a request doesn't say; the bare answer otherwise matches no route at all.

Both read the latest user message, and her own reply just before it (latest_user.previous_agent_message).
"""
import re

from .latest_user import latest_user_message, previous_agent_message

_SONNET = r"(?:claude[\s-]+)?sonnet"
_ASKED_OF_SONNET = re.compile(
    rf"\b(?:ask|check\s+with|consult|query)\s+(?:the\s+)?{_SONNET}\b|"
    rf"\b(?:find\s+out|get|learn|hear)\b.{{0,40}}\bfrom\s+(?:the\s+)?{_SONNET}\b|"
    rf"\b{_SONNET}\s+(?:knows|(?:would|might|should|may|will|could)\s+know|can\s+tell|has\s+(?:it|that|the\s+answer))\b|"
    rf"\bwhat\s+(?:does|did|would)\s+{_SONNET}\s+(?:say|think)\b", re.I | re.S)
# Work for Sonnet is the post office's, not a question (her prompt: "never send work to ask_sonnet").
_WORK = re.compile(r"\b(?:build|implement|fix|repair|write|code|change|create|make|deploy|refactor|replicate|"
                   r"plan|design|review|install|restart|take\s+care\s+of|work\s+on|handle)\b", re.I)
_TASK_ID = re.compile(r"\bt_[0-9a-f]{24}\b")

_SCANNER_CHOICE = re.compile(r"^\s*(?:(?:use|on|try)\s+)?(?:the\s+)?(window|freezer)(?:\s+(?:one|scanner))?"
                             r"(?:\s+(?:please|one\s+please))?\s*[.!]?\s*$", re.I)
_ASKED_WHICH_SCANNER = re.compile(r"\bscan(?:ner)?\b.*\?|\bwindow\b.*\bfreezer\b|\bfreezer\b.*\bwindow\b", re.I | re.S)


class SonnetQuestionRoute:
    def __init__(self, agent_name: str = "ask_sonnet") -> None:
        self._agent = agent_name

    def forced_agent(self, prompt: str) -> str | None:
        text = latest_user_message(prompt)
        if not text or _TASK_ID.search(text) or _WORK.search(text) or not _ASKED_OF_SONNET.search(text):
            return None
        return self._agent

    def question(self, prompt: str) -> str:
        text = latest_user_message(prompt)
        asked = previous_agent_message(prompt)
        if not asked.endswith("?"):
            return text
        return (f"{text}\n\nThe user wants you to answer the question Sydney had just asked them, "
                f"which they couldn't answer: \"{asked}\"")


class ScannerChoiceRoute:
    def __init__(self, agent_name: str = "bridge_scanner") -> None:
        self._agent = agent_name

    def _choice(self, prompt: str) -> str | None:
        match = _SCANNER_CHOICE.match(latest_user_message(prompt))
        if not match or not _ASKED_WHICH_SCANNER.search(previous_agent_message(prompt)):
            return None
        return match.group(1).lower()

    def forced_agent(self, prompt: str) -> str | None:
        return self._agent if self._choice(prompt) else None

    def question(self, prompt: str) -> str:
        choice = self._choice(prompt)
        return f"scan on the {choice} scanner" if choice else latest_user_message(prompt)
