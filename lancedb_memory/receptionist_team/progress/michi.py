"""Composition point for Michi's deterministic tracker and LLM narrator.

The order is the whole design: collect the facts first (tracker, no model),
then hand the model a prompt it can only explain, never extend (narrate).
"""
import os
from pathlib import Path

from agent_memory.models import AgentReply
from agent_memory.ports import LanguageModel

from . import tracker
from .narrate import DEFAULT_PROGRESS_QUESTION, build_explainer_prompt

AGENT_NAME = "michi"

# Where the finance_reports/ tree and her snapshot live. A placeholder
# workspace inside the package, same escape hatch mazda_team had, so a real
# report repository can be pointed at later without a code change.
REPO_ROOT = os.environ.get(
    "MICHI_REPO_ROOT", str(Path(__file__).resolve().parent.parent / "workspace")
)


async def answer(question: str, explainer: LanguageModel, repo_root: str | None = None) -> AgentReply:
    root = repo_root or REPO_ROOT
    snapshot = tracker.collect_and_persist(root)
    prompt = build_explainer_prompt(snapshot, question.strip() or DEFAULT_PROGRESS_QUESTION)
    return await explainer.complete(prompt)
