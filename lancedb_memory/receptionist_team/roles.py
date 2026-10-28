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
from .role_desks import IRoleAssignment, NamedRolesOrDefault, roles_for
from .rosters import Roster
from .extra_roles import EXTRA_ROLES

# Specialists Toyota can route to: finance_team's own roles, plus any defined
# as agent folders (see extra_roles.py), toyota_mechanic among them.
# adapters/routing_model.py imports this merged dict, not finance_team's bare
# one, so a ROUTE line naming an EXTRA_ROLES entry actually resolves.
ROLES = {**_FINANCE_ROLES, **EXTRA_ROLES}


# The agent that reads the real accounting records. Named here rather than
# spelled as a literal in profiles.py, adapters/ and the tests, because three
# of them have to agree: the roster entry, the RunnerMap key and the route
# override's forced agent. A constant makes a rename one edit instead of a
# silent miss where a forced route names an agent the map has no runner for.
LIVE_BOOKS_AGENT_NAME = "live_books"

# The agent that writes a spreadsheet of one category out of those same
# records. Named here for the same reason as live_books above: the roster
# entry, the RunnerMap key and the route override's forced agent all have to
# agree, and a forced route naming an agent the map has no runner for fails
# as a silent fall-through to the default tool agent rather than as an error.
CATEGORY_SPREADSHEET_AGENT_NAME = "category_spreadsheet"


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
    "search_link_opener": (
        "searches the web for a site by name, reads the results page with a "
        "headless browser, and opens the result that IS that site in the real "
        "Windows browser so you can log in by hand; it never types a password "
        "and never claims the site came up when it only opened the search page"
    ),
    "live_books": (
        "reads real figures straight out of the organization's live accounting "
        "records -- totals, month and category breakdowns, top vendors, what is "
        "missing a receipt -- read-only, and reports only what the query "
        "returned; it never estimates a number and never changes a record"
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
    "gifts_love_offerings_report": (
        "writes an HTML web page listing every Gifts & Love Offerings "
        "expense, with its sub-categories, for one year of the real books, "
        "showing each row's source details and flagging possible "
        "duplicate gifts; read-only"
    ),
    "web_search_expert": (
        "translates a request into an accurate search query -- correcting "
        "likely mishearings or typos of a proper noun from context -- and "
        "runs a real web search via SERP API, Google, or Brave, reporting "
        "only the results the search actually returned"
    ),
    "memory_compactor": (
        "prunes conversation turns older than a retention window out of the "
        "shared LanceDB memory store and reclaims the freed space; it never "
        "drops or clears a whole table, and refuses on its own if a cutoff "
        "would remove every row"
    ),
    "adw_guide": (
        "explains which ADW workflow fits a job and how to start it; "
        "read-only, never runs one"
    ),
    "ask_opus": "one question to Opus, the deep model",
    "ask_sonnet": "one question to Sonnet, the quick model",
    "tax_progress_page": "builds the 2025 tax progress page onto ADW Generated",
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
    "search_link_opener": "our site finder",
    "live_books": "the books",
    "toyota_mechanic": "our mechanic",
    "mechanic_translator": "our mechanic's translator",
    "progress_check": "our progress checker",
    "dashboard_expert": "our dashboard expert",
    "gifts_love_offerings_report": "our gifts and love offerings reporter",
    "web_search_expert": "our web search expert",
    "memory_compactor": "our memory compactor",
    "adw_guide": "our workflow guide",
    "ask_opus": "Opus",
    "ask_sonnet": "Sonnet",
    "tax_progress_page": "our tax progress page builder",
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
    "search_link_opener": "finding that site and opening it for you to log in",
    "live_books": "pulling that figure out of the live books",
    "toyota_mechanic": "looking at my own code",
    "mechanic_translator": "turning that into a precise instruction for my mechanic",
    "progress_check": "checking the status of your latest finance reports",
    "dashboard_expert": "looking into the dashboard codebase for you",
    "gifts_love_offerings_report": "building your gifts and love offerings report",
    "web_search_expert": "searching the web for that",
    "memory_compactor": "pruning and compacting the shared memory store",
    "adw_guide": "looking up that workflow for you",
    "ask_opus": "thinking about your question",
    "ask_sonnet": "thinking about your question",
    "tax_progress_page": "building your tax progress page",
}

# Toyota is the finance desk. Every finance_team role is hers automatically,
# plus the agent-folder specialists below, whose whole job is the books.
# Everything else in ROLES -- building, testing and deploying code, agent
# creation, the mechanic, the browser and web search, the ADW guide -- sits on
# Sydney's desk. Both rosters ask ROLE_ASSIGNMENT rather than each other's
# lists (see role_desks.py), so a new agent folder lands on Sydney's desk by
# default and a new FINANCE agent is one more name below.
TOYOTA_EXTRA_ROLE_NAMES = (
    "category_spreadsheet", "spreadsheet_reshaper", "live_books",
    "gifts_love_offerings_report", "tax_progress_page",
)
TOYOTA_ROLE_NAMES = (*_FINANCE_ROLES, *TOYOTA_EXTRA_ROLE_NAMES)
ROLE_ASSIGNMENT: IRoleAssignment = NamedRolesOrDefault(
    TOYOTA_ROLE_NAMES, named_desk="toyota", default_desk="sydney"
)
TOYOTA_ROLES = roles_for("toyota", ROLES, ROLE_ASSIGNMENT)

# Toyota's roster: her roles plus the three descriptions, validated as one
# object. Roster.__post_init__ raises at import if any agent is missing a
# blurb, spoken name or start task -- the same fail-loud check the three
# hand-written dict loops here used to do, now shared with Mazda and Sydney.
TOYOTA_ROSTER = Roster(
    owner="Toyota",
    roles=TOYOTA_ROLES,
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


# Which Groq model speaks as Toyota. profiles.py reads this for her chat_model.
#
# Back on the 120b after a detour through the 20b. The detour was based on a
# misreading: the 429s looked like the 120b exhausting a daily allowance, but
# Groq's x-ratelimit headers show the binding limit is 8000 tokens per MINUTE,
# one bucket shared by both gpt-oss sizes. So the size of one turn decides
# whether the next turn goes through, and the model does not -- which makes the
# 120b free to use, since it costs the same tokens and is the better model.
# ReceptionistProfile.recall_max_chars is the lever that actually moves this.
TOYOTA_CHAT_MODEL = "openai/gpt-oss-120b"


# The template is the authority for the wording; the roster is the authority
# for who is on it. A placeholder with no value raises MissingPlaceholderValue
# at import, same fail-loud posture as the roster's own checks.
TOYOTA_SYSTEM_PROMPT = catalog().definition("toyota").system_prompt().render(
    {
        "roster": TOYOTA_ROSTER.roster_text(),
        "allowed_agents": TOYOTA_ROSTER.allowed_agents(),
    }
)
