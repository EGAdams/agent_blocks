"""Toyota's persona and delegation acknowledgments.

No SDK or database imports. Finance agent roles come from finance_team.roles;
this module supplies descriptions and natural spoken names for those roles.

Toyota's own system prompt is NOT written here any more. It lives in
agents/toyota/system_prompt_template/system_prompt.md with two placeholders,
{{roster}} and {{allowed_agents}}, and this module is what fills them -- it is
the only place that knows the roster's wording, so it stays the renderer. See
agents/README.md; `TOYOTA_SYSTEM_PROMPT` below is byte-identical to the
f-string it replaced (tests/test_agent_catalog.py pins that).
"""

from finance_team.roles import ROLES as _FINANCE_ROLES

from .agents import catalog
from .rosters import Roster
from .extra_roles import EXTRA_ROLES

# Specialists Toyota can route to: finance_team's own roles, plus any defined
# here because finance_team/roles.py can't be edited (see extra_roles.py).
# adapters/routing_model.py imports this merged dict, not finance_team's bare
# one, so a ROUTE line naming an EXTRA_ROLES entry actually resolves.
ROLES = {**_FINANCE_ROLES, **EXTRA_ROLES}


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
    "category_spreadsheet": (
        "makes a spreadsheet of every expense in ONE named category, with its "
        "sub-categories, from the real books; read-only"
    ),
    "spreadsheet_reshaper": (
        "reshapes an existing combined expense spreadsheet into one column "
        "per person; reads the file you name and writes a new one, never "
        "the database and never the original file"
    ),
    "agent_creator": (
        "builds a brand new agent for this team from a description of what it "
        "should do, including the workflow it runs, and registers it so it can "
        "be routed to"
    ),
    "browser_opener": (
        "opens a URL or a search phrase in the Windows browser on this box; "
        "it never drives the page once open and never installs or uses a "
        "browser-automation tool"
    ),
    "toyota_mechanic": (
        "diagnoses or improves Toyota's OWN Python code in a sandbox; applies "
        "a change automatically only if every test passes"
    ),
    "mechanic_translator": (
        "turns a vague request about Toyota's own behavior or code into one "
        "precise instruction phrased the way toyota_mechanic expects; never "
        "diagnoses, changes, or dispatches to toyota_mechanic itself"
    ),
    "progress_check": (
        "checks which finance reports already exist and how fresh they are; "
        "only reads existing reports, has no database access, "
        "and does not run a new analysis"
    ),
    "dashboard_expert": (
        "answers questions about and makes requested code edits to the "
        "Letta-Code Dashboard codebase at ~/letta-code/dashboard/; never "
        "touches rol_finances or a database, and never restarts or deploys "
        "the live dashboard service without asking first"
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
    "category_spreadsheet": "our spreadsheet maker",
    "spreadsheet_reshaper": "our spreadsheet reshaper",
    "agent_creator": "our agent creator",
    "browser_opener": "our browser opener",
    "toyota_mechanic": "our mechanic",
    "mechanic_translator": "our mechanic's translator",
    "progress_check": "our progress checker",
    "dashboard_expert": "our dashboard expert",
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
    "category_spreadsheet": "building your category spreadsheet",
    "spreadsheet_reshaper": "reshaping your combined spreadsheet into separate columns per person",
    "agent_creator": "building your new agent",
    "browser_opener": "opening that in your browser",
    "toyota_mechanic": "looking at my own code",
    "mechanic_translator": "turning that into a precise instruction for my mechanic",
    "progress_check": "checking the status of your latest finance reports",
    "dashboard_expert": "looking into the dashboard codebase for you",
}

# Toyota's roster: the roles plus the three descriptions, validated as one
# object. Roster.__post_init__ raises at import if any agent is missing a
# blurb, spoken name or start task -- the same fail-loud check the three
# hand-written dict loops here used to do, now shared with Mazda.
TOYOTA_ROSTER = Roster(
    owner="Toyota",
    roles=ROLES,
    blurbs=ROLE_BLURBS,
    spoken_names=ROLE_SPOKEN_NAMES,
    start_tasks=ROLE_START_TASKS,
)


def delegation_acknowledgment(
    agent_name: str,
    task_description: str | None = None,
) -> str:
    """Toyota's delegation acknowledgment. See Roster.acknowledgment.

    Kept as a module-level function because it is Toyota's long-standing
    public name here and several tests and callers import it directly.
    """
    return TOYOTA_ROSTER.acknowledgment(agent_name, task_description)


# The template is the authority for the wording; the roster is the authority
# for who is on it. A placeholder with no value raises MissingPlaceholderValue
# at import, same fail-loud posture as the roster's own checks.
TOYOTA_SYSTEM_PROMPT = catalog().definition("toyota").system_prompt().render(
    {
        "roster": TOYOTA_ROSTER.roster_text(),
        "allowed_agents": TOYOTA_ROSTER.allowed_agents(),
    }
)
