"""ObjectInspectionRunner -- Toyota asks the object inspector, and repeats what it found.

    Toyota -> ROUTE: object_inspector | <the user's words>
      -> inspection_route.inspection_question (which of the four, and about what)
      -> GET http://127.0.0.1:8790/api/ask/<question>?subject=<id>   (object_trace.service)
      -> the inspector's answer, its strongest evidence, and what it is missing

The same service the debugger page reads, so Toyota and the UI cannot
disagree. No model in the path: the reply is built from the inspector's
Answer, never paraphrased. Nothing raises -- an inspector that is down is one
sentence saying so, with no guess in its place.
"""
import asyncio
import json
import os
import urllib.parse
import urllib.request
from collections.abc import Callable

from agent_memory.models import AgentReply

from .inspection_route import inspection_question

INSPECTOR_URL = os.environ.get("OBJECT_INSPECTOR_URL", "http://127.0.0.1:8790")
EVIDENCE_SPOKEN = 3


def _http_get(url: str, timeout: float) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 -- loopback inspector only
        return json.loads(response.read().decode("utf-8"))


class ObjectInspectionRunner:
    def __init__(self, base_url: str = INSPECTOR_URL, fetch: Callable[[str, float], dict] = _http_get,
                 timeout: float = 5.0) -> None:
        self._base, self._fetch, self._timeout = base_url.rstrip("/"), fetch, timeout

    async def run(self, role: object, agent_name: str, model: str, extra: str = "") -> AgentReply:
        return AgentReply(text=await asyncio.to_thread(self.answer, extra or ""))

    def answer(self, text: str) -> str:
        asked = inspection_question(text)
        if asked is None:
            return ("I can show the latest Notes update, where a task stopped, an object's state and error, "
                    "or whether content saved and rendered. Which one?")
        question, subject = asked
        url = f"{self._base}/api/ask/{question}"
        if subject:
            url += "?" + urllib.parse.urlencode({"subject": subject})
        try:
            body = self._fetch(url, self._timeout)
        except Exception as exc:  # noqa: BLE001 -- down, refused, timeout, bad JSON: one sentence, no guess
            return (f"The object inspector at {self._base} didn't answer ({type(exc).__name__}), "
                    "so I have no evidence to show and I won't guess.")
        return self.spoken(body)

    @staticmethod
    def spoken(body: dict) -> str:
        answer = str(body.get("answer") or "The inspector gave no answer.")
        evidence = [e for e in body.get("evidence") or [] if isinstance(e, dict)]
        missing = [str(m) for m in body.get("missing") or []]
        parts = [answer]
        if evidence:
            facts = "; ".join(str(e.get("fact", ""))[:160] for e in evidence[:EVIDENCE_SPOKEN])
            more = f" (and {len(evidence) - EVIDENCE_SPOKEN} more)" if len(evidence) > EVIDENCE_SPOKEN else ""
            parts.append(f"Evidence: {facts}{more}.")
        elif body.get("found"):
            parts.append("The inspector cited no evidence.")
        if missing:
            parts.append("Missing: " + "; ".join(m[:200] for m in missing[:3]) + ".")
        if body.get("subject"):
            parts.append(f"Object id: {body['subject']}.")
        return " ".join(parts)
