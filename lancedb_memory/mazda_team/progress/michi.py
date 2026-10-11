"""Composition point for Michi's deterministic tracker and LLM narrator."""
from agent_memory.models import AgentReply
from agent_memory.ports import LanguageModel

from .. import specialist_roles
from . import tracker
from .narrate import DEFAULT_PROGRESS_QUESTION, build_explainer_prompt

AGENT_NAME = "michi"


async def answer(question: str, explainer: LanguageModel, repo_root: str | None = None) -> AgentReply:
    root = repo_root or specialist_roles.REPO_ROOT
    snapshot = tracker.collect_and_persist(root)
    prompt = build_explainer_prompt(snapshot, question.strip() or DEFAULT_PROGRESS_QUESTION)
    return await explainer.complete(prompt)
