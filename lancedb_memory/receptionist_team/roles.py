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
from .role_desks import IRoleAssignment, NamedRolesOrDefault
from .roster_membership import TOYOTA_ROSTER_CONFIG, load_membership
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

# The company to-do list. Same three-must-agree reason as the two above.
COMPANY_TODOS_AGENT_NAME = "company_todos"


ROLE_BLURBS: dict[str, str] = {
    "finance_report": "refreshes the River of Life 2025 nonprofit finance report from the live books and publishes it to ADW Generated",
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
    "scala_questions": (
        "passes EG's answer to one of Scala's filing questions to Scala, who files "
        "the waiting scan in the named folder and remembers it; it never files "
        "a scan in a folder EG did not name"
    ),
    "company_todos": (
        "keeps the company to-do list: adds an item, reads the list back, "
        "or checks an item off; it never invents, removes or rewords an item"
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
    "sydney_post_office": (
        "sends real work -- build, plan, review, fix -- or a message to Sonnet, "
        "Opus, the mechanic or Toyota through the team post office, and reports "
        "the status of work already sent"
    ),
    "toyota_post_office": (
        "sends a message or a request to Sydney, Sonnet, Opus or the mechanic "
        "through the team post office, and reports the status of what was sent"
    ),
    "system_mechanic": (
        "starts, stops, restarts, reloads, enables, disables, or checks the "
        "status of one named systemd service on this box; it never deletes "
        "a file"
    ),
    "star_trek_bridge": (
        "read-only questions to Bridge agents, the agent list, and status, result or cancel of a task"
    ),
    "bridge_writer": (
        "changes files via the Bridge's Codex writer, after a yes"
    ),
    "bridge_coder": (
        "coding task for the Bridge's Claude Sonnet; changes after a yes"
    ),
    "bridge_printer": (
        "prints (after a yes), checks or cancels print jobs, lists printers"
    ),
    "bridge_scanner": (
        "scans on the Window or Freezer scanner, after a yes"
    ),
    "bridge_notes": (
        "Notes: searches and reads every note and its highlights; after a yes edits lines or sections, adds or clears "
        "highlights, creates, renames or moves, trashes and restores notes (permanent delete only from the trash, "
        "on EG's yes); also runs the Notes diagnostic marker"
    ),
    "bridge_browser": (
        "Chrome on your desktop through the Bridge: opens a page, goes somewhere, reads it back, lists a form's "
        "controls with their selectors, chooses in a dropdown, types in a field, presses a control -- each after a "
        "yes; it never types a password and never presses something that looks like paying or submitting"
    ),
    "bridge_chatgpt": (
        "ChatGPT through the Bridge: asks a signed-in ChatGPT session one question and reads the reply back once it "
        "has finished writing, reads a thread, lists the sessions -- each after a yes; it never puts a password in a "
        "message, never closes that browser, and won't start a new chat"
    ),
    "bridge_deploy": (
        "Deployments through the Bridge: lists the projects, checks one's status, health, logs and release history; "
        "after a yes deploys a project (checks first, health after, the last release put back by itself if health "
        "fails), restarts its services, or rolls it back to an earlier release"
    ),
    "bridge_scala_storage": (
        "Scala's filing on BATI, R46 (Mom's) or SQMC: lists folders, checks or finds a filed scan; after a yes "
        "copies a scan into a folder, always a copy"
    ),
    "object_inspector": "answers from the object trace",
}

ROLE_SPOKEN_NAMES: dict[str, str] = {
    "finance_report": "our finance report workflow",
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
    "company_todos": "our to-do keeper",
    "scala_questions": "Scala",
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
    "sydney_post_office": "the post office",
    "toyota_post_office": "the post office",
    "system_mechanic": "our system mechanic",
    "star_trek_bridge": "the Star Trek Bridge",
    "bridge_writer": "the Bridge's Codex writer",
    "bridge_coder": "the Bridge's Claude Sonnet coder",
    "bridge_printer": "the Bridge's printer",
    "bridge_scanner": "the Bridge's scanner",
    "bridge_notes": "the Bridge's Notes",
    "bridge_browser": "the Bridge's browser",
    "bridge_chatgpt": "the Bridge's ChatGPT relay",
    "bridge_deploy": "the Bridge's Deployment ADW",
    "bridge_scala_storage": "Scala's filing",
    "object_inspector": "our object inspector",
}

# Each phrase follows "tell them to start".
ROLE_START_TASKS: dict[str, str] = {
    "finance_report": "refreshing and publishing your 2025 finance report",
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
    "company_todos": "on the company to-do list",
    "scala_questions": "checking on that scan",
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
    "sydney_post_office": "sending that on",
    "toyota_post_office": "sending that on",
    "system_mechanic": "taking care of that service",
    "star_trek_bridge": "a read-only look at that",
    "bridge_writer": "on that change",
    "bridge_coder": "on that coding task",
    "bridge_printer": "on that print job",
    "bridge_scanner": "on that scan",
    "bridge_notes": "on the notes",
    "bridge_browser": "on that browser step",
    "bridge_chatgpt": "on that ChatGPT message",
    "bridge_deploy": "on that deployment",
    "bridge_scala_storage": "on filing that scan",
    "object_inspector": "looking that up in the object trace",
}

# Toyota is the finance desk: the agent-folder specialists below are hers.
# Building, testing and deploying code, the browser and web search, the ADW
# guide sit on Sydney's desk; agent creation and the mechanic are on Sydney's
# desk AND on Toyota's explicit roster (toyota_roster.json). Sydney's roster
# asks ROLE_ASSIGNMENT (see role_desks.py), so a new agent folder lands on
# Sydney's desk by default and reaches Toyota only if her roster names it.
TOYOTA_EXTRA_ROLE_NAMES = (
    "category_spreadsheet", "spreadsheet_reshaper", "live_books",
    "gifts_love_offerings_report", "tax_progress_page", "toyota_post_office",
    "company_todos", "scala_questions",
)
# The DESK assignment, not Toyota's roster. It decides which roles are NOT
# Sydney's (she gets every role this list doesn't name), so it keeps the
# finance roles and is unchanged from before. star_trek_bridge is named here
# only so that registering its folder doesn't land it on Sydney's desk.
# The Star Trek Bridge routes are owned by Toyota's desk and SHARED with Sydney's
# roster (sydney_roles.py adds them): each receptionist reaches the Bridge with her
# own credential and her own sender (bridge_client/desk.py's DESKS), so sharing a
# route never shares a "yes", a revision or an audit identity.
BRIDGE_ROLE_NAMES = ("star_trek_bridge", "bridge_writer", "bridge_coder", "bridge_printer", "bridge_scanner",
                     "bridge_notes", "bridge_browser", "bridge_chatgpt", "bridge_deploy",
                     "bridge_scala_storage", "object_inspector")
TOYOTA_DESK_ROLE_NAMES = (*_FINANCE_ROLES, *TOYOTA_EXTRA_ROLE_NAMES, *BRIDGE_ROLE_NAMES)
ROLE_ASSIGNMENT: IRoleAssignment = NamedRolesOrDefault(
    TOYOTA_DESK_ROLE_NAMES, named_desk="toyota", default_desk="sydney"
)

# Toyota's ROSTER: explicit, from toyota_roster.json, validated at import (see
# roster_membership.py). It lists every finance_team role (a test fails if a
# new one is neither enabled nor explicitly disabled), her agent-folder
# specialists, agent_creator and toyota_mechanic -- those two stay on Sydney's
# roster too, since her desk assignment above is unchanged -- and
# star_trek_bridge. A role is added only by naming an already-defined role there.
TOYOTA_MEMBERSHIP = load_membership(TOYOTA_ROSTER_CONFIG)
TOYOTA_ROLE_NAMES = TOYOTA_MEMBERSHIP.enabled
TOYOTA_ROLES = TOYOTA_MEMBERSHIP.select(ROLES)

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
