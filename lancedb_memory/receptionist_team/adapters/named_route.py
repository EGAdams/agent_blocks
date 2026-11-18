"""NamedRoute -- "use bridge_cashy to ..." goes to bridge_cashy, because the user said so.

2026-11-17, Sydney: "Use bridge_cashy to list the options on R46" reached the read-only star_trek_bridge desk,
which could only refuse it ("cashy-r46 can write... I only send read-only requests"), and "use bridge_deploy",
"use bridge_chatgpt" and "use bridge_browser" were each left to the model, which sometimes answered from memory
instead. CashyR46Route didn't fire because `\\bcashy\\b` has no word boundary inside `bridge_cashy`.

When the user names one of the receptionist's own specialists outright, that is not a judgement call. So this
fires on the latest user message only, when a roster name (exactly as the roster spells it, underscores and
all) follows a handing-over word -- "use", "ask", "via", "through", "send it to" -- or opens the message
("bridge_notes: show the deed"). A name merely mentioned ("explain how bridge_cashy works") has no opinion
here, and two different names in one message have none either: the model decides that, as before.

A match only reaches the specialist. A Bridge specialist still reads the request back and waits for a yes.
"""
import re
from collections.abc import Iterable

from .latest_user import latest_user_message

_HANDOVER = (r"(?:use|using|ask|tell|have|via|through|with|route\s+(?:it|this|that)\s+to|"
             r"send\s+(?:it|this|that)\s+to|hand\s+(?:it|this|that)\s+to|pass\s+(?:it|this|that)\s+to)")


class NamedRoute:
    def __init__(self, names: Iterable[str]) -> None:
        self._names = tuple(sorted(set(names), key=len, reverse=True))   # longest first: bridge_scala_storage
        alternatives = "|".join(re.escape(name) for name in self._names)
        self._named = re.compile(rf"(?:^\s*|\b{_HANDOVER}\s+(?:the\s+)?)({alternatives})\b", re.I)
        self._mentioned = re.compile(rf"\b({alternatives})\b", re.I)

    def named(self, text: str) -> str | None:
        if len({match.group(1).lower() for match in self._mentioned.finditer(text or "")}) != 1:
            return None
        match = self._named.search(text or "")
        return match.group(1).lower() if match else None

    def forced_agent(self, prompt: str) -> str | None:
        return self.named(latest_user_message(prompt))

    def question(self, prompt: str) -> str:
        return latest_user_message(prompt)
