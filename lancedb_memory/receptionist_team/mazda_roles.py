"""Mazda's persona and roster.

Mazda is a document-verification receptionist: bank statements, arithmetic
checks, database presence and duplicate checks, vendor keys, expense
categories, and one HTML verification report per statement.

The five specialists below are real Letta agents on the Letta server
(Mazda Router / Parser / Vendor Identity / Receipt Linker / Categorization),
but this package cannot reach them yet, so they are registered as stubs and
delegation goes to UnwiredRunner -- she says plainly that the connection
isn't built rather than implying work happened. Wiring them in means swapping
that one runner in main.py; nothing here changes.

Her wording comes from the agent herself: the responsibilities, the
never-claim rules and the "calm, warm, direct, practical" tone were taken
from her own answers and her Letta `system/persona` block, not invented here.

No SDK import, same as roles.py -- this module is pure data plus one render.
"""
from dataclasses import dataclass, field

from .agents import catalog
from .progress.michi import AGENT_NAME as MICHI_AGENT_NAME
from .progress.narrate import DEFAULT_PROGRESS_QUESTION
from .rosters import Roster


# Mirrors finance_team.roles.AgentRole / mazda_team's shape: role config, not
# a wire contract, so a frozen dataclass rather than a pydantic BaseModel.
# Declared locally because Mazda's specialists are not finance_team roles and
# must not inherit its rol_finances repo root or database briefing.
@dataclass(frozen=True)
class AgentRole:
    name: str
    system_prompt: str
    task_prompt: str
    allowed_tools: list[str] = field(default_factory=list)
    max_turns: int = 40


def _stub(name: str, purpose: str) -> AgentRole:
    """A registered-but-unconnected specialist.

    allowed_tools is deliberately empty: nothing should be able to run these
    accidentally against real financial data before they are wired to the
    Letta agents that own the work.
    """
    return AgentRole(
        name=name,
        system_prompt=(
            f"You are {name}, part of Mazda's document-verification team. "
            f"Your job is to {purpose}. You are not connected to live data yet."
        ),
        task_prompt=(
            f"This agent is not wired up yet. Report that {name} is "
            "registered but not connected, and do not invent results."
        ),
    )


ROLES: dict[str, AgentRole] = {
    "parser": _stub(
        "parser",
        "extract every transaction row from a statement PDF or a difficult document",
    ),
    "vendor_identity": _stub(
        "vendor_identity",
        "identify an unclear merchant and settle on the right reusable vendor key",
    ),
    "receipt_linker": _stub(
        "receipt_linker",
        "match receipts to statement transactions without creating a double entry",
    ),
    "categorization": _stub(
        "categorization",
        "check that each expense category fits its vendor key and flag anomalies",
    ),
    "router": _stub(
        "router",
        "break an unclear or multi-step verification request into ordered steps",
    ),
    # Michi is the one specialist here that actually runs: her tracker reads
    # the report files off disk and the explaining model only rephrases that
    # snapshot (progress/, adapters/michi_runner.py). She carries no tools
    # because she needs none -- the reading is Python, not an agent.
    MICHI_AGENT_NAME: AgentRole(
        name=MICHI_AGENT_NAME,
        system_prompt=(
            "You are Michi, the progress and planning tracker, and the one who "
            "writes the finance reports. You report on observed status; you have "
            "no tools here, so a step assigned to you is work still waiting."
        ),
        task_prompt=DEFAULT_PROGRESS_QUESTION,
        max_turns=1,
    ),
}

ROLE_BLURBS: dict[str, str] = {
    "parser": (
        "pulls every transaction row out of a statement PDF or a document that "
        "resists plain reading"
    ),
    "vendor_identity": (
        "works out who an unclear merchant actually is and settles on the "
        "reusable vendor key for them"
    ),
    "receipt_linker": (
        "matches receipts to statement transactions, taking care never to "
        "create a second accounting entry for the same money"
    ),
    "categorization": (
        "checks that each expense category fits its vendor key and flags "
        "anything that looks out of place"
    ),
    "router": (
        "breaks an unclear or multi-step verification request into ordered "
        "steps before any of the work starts"
    ),
    "michi": (
        "tracks which statements and reports are done, blocked or missing and "
        "what should happen next, and writes the finance reports herself"
    ),
}

ROLE_SPOKEN_NAMES: dict[str, str] = {
    "parser": "our statement parser",
    "vendor_identity": "our vendor identity specialist",
    "receipt_linker": "our receipt linker",
    "categorization": "our categorization specialist",
    "router": "our request router",
    "michi": "our progress tracker",
}

# Each phrase follows "tell them to start".
ROLE_START_TASKS: dict[str, str] = {
    "parser": "pulling the transaction rows out of that statement",
    "vendor_identity": "working out who that merchant is",
    "receipt_linker": "matching those receipts to the statement",
    "categorization": "checking those expense categories",
    "router": "breaking that request into steps",
    "michi": "checking where every report stands and what's next",
}

MAZDA_ROSTER = Roster(
    owner="Mazda",
    roles=ROLES,
    blurbs=ROLE_BLURBS,
    spoken_names=ROLE_SPOKEN_NAMES,
    start_tasks=ROLE_START_TASKS,
)


def delegation_acknowledgment(
    agent_name: str,
    task_description: str | None = None,
) -> str:
    """Mazda's delegation acknowledgment. See Roster.acknowledgment."""
    return MAZDA_ROSTER.acknowledgment(agent_name, task_description)


# Which Groq model speaks as Mazda. The 20b rather than Toyota's 120b: her
# recall is large, and on the 120b she spent the whole 200k-token daily Groq
# allowance in roughly forty turns, after which every turn came back 429.
# profiles.py reads this for her chat_model and the prompt below is rendered
# with the same value, so she cannot end up naming a model she isn't on.
MAZDA_CHAT_MODEL = "openai/gpt-oss-20b"

MAZDA_SYSTEM_PROMPT = catalog().definition("mazda").system_prompt().render(
    {
        "roster": MAZDA_ROSTER.roster_text(),
        "allowed_agents": MAZDA_ROSTER.allowed_agents(),
        "chat_model": MAZDA_CHAT_MODEL,
    }
)
