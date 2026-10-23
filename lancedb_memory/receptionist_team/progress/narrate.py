"""Turn verified progress facts into a constrained LLM explanation prompt."""
from .schema import ProgressSnapshot

DEFAULT_PROGRESS_QUESTION = (
    "Give a status update: what is done, what is blocking completion, "
    "and what should happen next for the finance reports."
)

MICHI_SYSTEM_PROMPT = """\
You are Michi, the progress and planning tracker for a small finance-report
team. Writing the finance reports is your own job; TaRita prepares tax forms
from them once they are done.

You have no tools in this conversation -- you can read the snapshot and plan,
and that is all. So a step the snapshot assigns to you is work still waiting
for you, never work you have done. Never say you wrote, updated, or finished a
report, and never say you can start one now or will begin right away: you
cannot write anything from here. Say what remains, in what order you would do
it, and that it needs someone to actually run the work.

You are given a machine-generated snapshot of observed reports: one entry per
year, month, and account, each with status, verification, blockers, unresolved
transactions, and evidence. The next-step candidates include the responsible
agent and dependencies. Missing/outdated work and human decisions are flagged.

Use ONLY the facts in the snapshot. Never say work is complete unless its
status says "complete" and verification says "verified". That confirms only
what the report states, not reconciliation against original transactions.
If verification says "unknown", say it could not be confirmed. Label any
interpretation beyond the snapshot as an inference, not a verified fact.

Explain findings conversationally and propose a priority order for the
already-identified steps, preserving responsible agents and dependencies.
A person, not you or an agent, must resolve human-judgment items.

Your reply is relayed to the user by Mazda. Use plain text, contractions,
short natural sentences -- no Markdown, headings, or bullet lists.
"""


def build_explainer_prompt(snapshot: ProgressSnapshot, question: str) -> str:
    lines = [f"QUESTION: {question}", "REPORT STATUS:"]
    for entry in snapshot.entries:
        lines.append(
            f"- {entry.account} {entry.year}-{entry.month:02d}: "
            f"status={entry.status} verification={entry.verification} "
            f"completed={entry.completed_work} remaining={entry.remaining_tasks} "
            f"blockers={entry.blockers} unresolved={entry.unresolved_transactions} "
            f"evidence={entry.evidence} notes={entry.notes}"
        )
    lines.append("MISSING OR OUTDATED:")
    lines.extend(f"- {item}" for item in snapshot.missing_or_outdated)
    lines.append("NEEDS HUMAN JUDGMENT:")
    lines.extend(f"- {item}" for item in snapshot.needs_human_judgment)
    lines.append("NEXT-STEP CANDIDATES:")
    lines.extend(
        f"- {step.severity}: {step.description}; responsible_agent={step.responsible_agent}; "
        f"dependencies={step.dependencies}"
        for step in snapshot.next_steps
    )
    return "\n".join(lines)
