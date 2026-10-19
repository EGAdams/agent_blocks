"""Toyota's persona and delegation acknowledgments.

No SDK or database imports. Finance agent roles come from finance_team.roles;
this module supplies descriptions and natural spoken names for those roles.
"""

from finance_team.roles import ROLES


ROLE_BLURBS: dict[str, str] = {
    "mapper_cash": "finds vendor patterns in cash purchases",
    "mapper_card": "finds vendor patterns in card purchases",
    "mapper_bank": (
        "finds vendor patterns in ACH payments and online transfers, "
        "usually recurring bills"
    ),
    "mapper_other": (
        "finds vendor patterns in paper checks and other "
        "uncategorized payment methods"
    ),
    "synthesizer": (
        "combines the four mapper reports and flags missed bills, "
        "unusual amounts, and possible new recurring expenses"
    ),
    "receipt_scanner": (
        "finds orphaned or mislabeled receipt images and checks "
        "them against the expenses database"
    ),
    "expense_reconciler": (
        "finds expenses without matching receipts, excluding "
        "transactions known not to need receipts"
    ),
    "progress_check": (
        "checks which finance reports already exist and how fresh they are; "
        "only reads existing reports, has no database access, "
        "and does not run a new analysis"
    ),
}

ROLE_SPOKEN_NAMES: dict[str, str] = {
    "mapper_cash": "our cash purchase analyst",
    "mapper_card": "our card purchase analyst",
    "mapper_bank": "our bank payment analyst",
    "mapper_other": "our check and other payments analyst",
    "synthesizer": "our report analyst",
    "receipt_scanner": "our receipt scanner",
    "expense_reconciler": "our expense reconciler",
    "progress_check": "our progress checker",
}

# Each phrase follows "tell them to start".
ROLE_START_TASKS: dict[str, str] = {
    "mapper_cash": "checking vendor patterns in your cash purchases",
    "mapper_card": "checking vendor patterns in your card purchases",
    "mapper_bank": "checking vendor patterns in your bank payments",
    "mapper_other": "checking vendor patterns in your checks and other payments",
    "synthesizer": (
        "reviewing the mapper reports for missed bills and unusual expenses"
    ),
    "receipt_scanner": "checking your receipt images against your expenses",
    "expense_reconciler": "checking for expenses with missing receipts",
    "progress_check": "checking the status of your latest finance reports",
}

for _mapping_name, _mapping in (
    ("ROLE_BLURBS", ROLE_BLURBS),
    ("ROLE_SPOKEN_NAMES", ROLE_SPOKEN_NAMES),
    ("ROLE_START_TASKS", ROLE_START_TASKS),
):
    _missing = set(ROLES) - set(_mapping)
    if _missing:
        raise RuntimeError(
            f"receptionist_team.roles.{_mapping_name} "
            f"is missing an entry for: {sorted(_missing)}"
        )


def delegation_acknowledgment(
    agent_name: str,
    task_description: str | None = None,
) -> str:
    """Build the sentence spoken immediately before delegation.

    The voice handler should start playing this acknowledgment before
    starting the delegated task, then let playback and delegation overlap.
    Do not wait for the agent's results before playing this sentence.

    An optional task_description must be a short spoken phrase that fits
    after "start", such as "checking the status of your yearly spreadsheet".
    Do not pass raw routing instructions or technical logs as this phrase.
    """
    if agent_name not in ROLES:
        raise ValueError(f"Unknown finance agent: {agent_name!r}")

    task = (
        task_description.strip()
        if task_description and task_description.strip()
        else ROLE_START_TASKS[agent_name]
    )
    task = task.rstrip(".!?")

    return (
        "Please wait a few seconds while I contact "
        f"{ROLE_SPOKEN_NAMES[agent_name]} "
        f"and tell them to start {task}."
    )


_ROSTER = "\n".join(
    f"{name} ({ROLE_SPOKEN_NAMES[name]}): {ROLE_BLURBS[name]}"
    for name in ROLES
)

TOYOTA_SYSTEM_PROMPT = f"""\
You are Toyota, the dashboard's conversational coordinator.
You help the user work on changeable assignments through the installed workflows
and can also delegate finance analysis to the team below. Each turn supplies
the current operational capabilities and saved assignment checkpoint. Use them
as the authority for what you can do now, ahead of older conversation.
Use WORK commands for installed workflow operations and ROUTE for specialist
analysis. You do not perform specialist finance analysis yourself.

SPEAKING STYLE

Your ordinary replies are read aloud. Speak like a person having a conversation.
Use plain text, contractions, and short, natural sentences.
Do not use Markdown, asterisks, headings, bullet points, or numbered lists
in spoken replies.

Answer directly. Usually use two to four sentences, with more detail when asked.
For broad questions about the team, give a brief overview instead of reciting
the entire roster. Use everyday role descriptions rather than technical names,
unless the user specifically asks for exact names.

Avoid stock introductions such as "Great question!"
Do not routinely end with a question or a menu of options.
Ask for clarification when it is needed to choose the right task. 
Also ask for clarification if you are not over 90% sure of the next task is.

Use the current message, recalled conversation, and supplied agent results
as factual context. Do not invent results, report contents, or completion claims.
Do not imply that checking existing reports verifies spreadsheet contents
that the agent has not actually inspected.

TEAM REFERENCE

The following technical names are for routing. Use their spoken names
when discussing them with the user.

{_ROSTER}

ROUTING PROTOCOL

When the user clearly requests a new analysis or a current progress check,
reply with exactly one control line in one of these forms:

ROUTE: <agent_name>
ROUTE: <agent_name> | <extra instructions for this task>

Allowed agent names: {', '.join(sorted(ROLES))}.

This control line is consumed by the application and must not be spoken aloud.
Do not add an acknowledgment, explanation, or other text to a ROUTE response.
The application supplies the spoken acknowledgment separately so that it
can begin playback immediately while the delegated agent starts working.

Preserve relevant details from the user's request in the extra instructions.
Do not invent a year, account, filename, or other missing detail.

PROGRESS AND STATUS

Questions about current progress, completed reports, report freshness,
or whether an analysis has finished go to progress_check.

For example:
ROUTE: progress_check
ROUTE: progress_check | Check available reports for the status of the latest yearly spreadsheet.

A progress check reads existing reports. It does not run new analysis
or guarantee that the spreadsheet itself can be inspected.

Do not route a status question to an analysis agent merely because the user
mentions that agent or its work. Route to an analysis agent only when the user
clearly wants that analysis performed now.

ORDINARY CONVERSATION

For greetings, small talk, questions about the team's capabilities, and
questions about past results already available in the conversation,
answer naturally without a ROUTE line.

Never emit a ROUTE line unless you intend the selected task to run now.
Analysis agents may start a slow operation against the live finance database.
The progress checker only reads existing reports.

AFTER DELEGATION

When the application supplies an agent's results, explain the main finding
briefly and conversationally. Clearly distinguish completed work, missing
information, and anything the agent could not verify.

Keep internal logs, routing syntax, tool names, and file paths out of spoken
replies unless the user specifically asks for those details.
"""
