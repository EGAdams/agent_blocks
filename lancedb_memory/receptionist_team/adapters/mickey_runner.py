"""MickeyRunner -- the IDelegationRunner that answers a progress question.

Not a tool agent. progress/tracker.py collects the facts off disk with no
model involved, and the explaining model only ever sees that snapshot, so it
can rephrase the status but cannot add to it. That is why Mickey needs her own
runner rather than a brief handed to run_role.
"""
from agent_memory.adapters.groq_model import GroqLanguageModel
from agent_memory.models import AgentReply
from agent_memory.ports import LanguageModel

from ..progress.mickey import REPO_ROOT, answer
from ..progress.narrate import DEFAULT_PROGRESS_QUESTION, MICKEY_SYSTEM_PROMPT


class MickeyRunner:
    """Collect the progress snapshot, then let one constrained model explain it."""

    def __init__(self, explainer: LanguageModel | None = None, repo_root: str | None = None) -> None:
        # Built lazily so constructing a profile costs nothing and tests can
        # inject a scripted model instead of reaching Groq.
        self._explainer = explainer
        self._repo_root = repo_root or REPO_ROOT

    async def run(
        self, role: object, agent_name: str, model: str, extra: str = ""
    ) -> AgentReply:
        explainer = self._explainer or GroqLanguageModel(system_prompt=MICKEY_SYSTEM_PROMPT)
        # The text after the "|" is the question; with no "|" she gives the
        # standing status update instead of guessing what was meant.
        return await answer(extra or DEFAULT_PROGRESS_QUESTION, explainer=explainer,
                            repo_root=self._repo_root)
