"""ReceptionistProfile -- everything that differs between the receptionists.

Toyota, Mazda and Sydney are the same engine. What varies between them is a short
list of values: which agents/ folder holds their prompts, which roster they
can route to, how a delegation actually runs, which session and nexus
subdirectory their memory uses, whose turns they recall as a peer, which TTS
voice speaks for them, and whether the dashboard-only decorators
(AgentHandoffModel, WorkflowModel) apply at all.

So that list is a frozen dataclass and main.py's build_receptionist() reads
it. Role config, not a wire contract -- same reasoning as finance_team's
AgentRole, so a dataclass rather than a pydantic BaseModel.

Sydney was the third receptionist, and she was exactly one more PROFILES
entry plus her agents/ folder; a fourth is the same, not another build_*()
function.
"""
from collections.abc import Callable
from dataclasses import dataclass

from .delegation import IDelegationRunner, IRouteOverride, RunnerMap, UnwiredRunner
from .mazda_roles import (
    BROWSER_AGENT_NAME as MAZDA_BROWSER_AGENT,
    MAZDA_CHAT_MODEL,
    MAZDA_ROSTER,
    MAZDA_SYSTEM_PROMPT,
    ROLE_SPOKEN_NAMES as MAZDA_SPOKEN_NAMES,
)
from .adapters.figure_route import FigureQuestionRoute
from .adapters.route_panel import FirstRouteOpinion
from .adapters.spreadsheet_route import SpreadsheetRequestRoute
from .adapters.status_route import StatusQuestionRoute
from .adapters.tax_progress_route import TaxProgressPageRoute
from .progress.mickey import AGENT_NAME as MICKEY_AGENT_NAME
from .roles import (
    CATEGORY_SPREADSHEET_AGENT_NAME,
    LIVE_BOOKS_AGENT_NAME,
    TOYOTA_CHAT_MODEL,
    TOYOTA_ROSTER,
    TOYOTA_SYSTEM_PROMPT,
)
from .rosters import Roster
from .sydney_roles import SYDNEY_CHAT_MODEL, SYDNEY_ROSTER, SYDNEY_SYSTEM_PROMPT


def _mazda_runner() -> IDelegationRunner:
    """Mickey and the browser workflow really run; the other five don't yet.

    Imported here rather than at module scope so adapters/mickey_runner.py's
    GroqLanguageModel import stays off the path of anything that only reads
    the profiles.
    """
    from .adapters.adw_runner import AdwBrowserRunner
    from .adapters.mickey_runner import MickeyRunner

    return RunnerMap(
        {
            MICKEY_AGENT_NAME: MickeyRunner(),
            # A subprocess and the line it wrote -- no agent, no model, so
            # nothing in the path can embellish what happened. See adw_runner.
            MAZDA_BROWSER_AGENT: AdwBrowserRunner(),
        },
        default=UnwiredRunner(MAZDA_SPOKEN_NAMES),
    )


def _toyota_runner() -> IDelegationRunner:
    """Her finance roles run through the Claude SDK; the live books do not.

    Imported here, not at module scope, on purpose:
    adapters/tool_agent_runner.py pulls in finance_team (and through it
    claude_agent_sdk and the rol_finances repo root). Mazda routes to nobody
    real yet, so building her profile must not drag that in -- and tests that
    only check her wiring shouldn't need the SDK installed.

    live_books and category_spreadsheet are mapped to ADW runners rather than
    to their agent folders because both answers are made of real accounting
    records -- a figure, and a row count plus a total plus a path -- and a
    model in the path is a chance for any of them to come back rephrased.
    Same deterministic-first argument as Mickey and Mazda's browser workflow.
    Swapping either for ToolAgentRunner -- for a request that needs looking
    around first -- is deleting one line of this map; the agents/live_books
    and agents/category_spreadsheet folders are already written for that case.
    """
    from .adapters.category_spreadsheet_runner import AdwCategorySpreadsheetRunner
    from .adapters.live_finance_runner import AdwLiveFinanceRunner
    from .adapters.tool_agent_runner import ToolAgentRunner

    return RunnerMap(
        {
            LIVE_BOOKS_AGENT_NAME: AdwLiveFinanceRunner(),
            CATEGORY_SPREADSHEET_AGENT_NAME: AdwCategorySpreadsheetRunner(),
        },
        default=ToolAgentRunner(),
    )


def _sydney_runner() -> IDelegationRunner:
    """Every one of her specialists is an agent folder run through the Claude SDK.

    Same runner Toyota falls back to for her own agent folders -- Sydney is a
    clone, so a delegation runs exactly the way it does for Toyota. Imported
    inside the function for the same reason as _toyota_runner's.
    """
    from .adapters.tool_agent_runner import ToolAgentRunner

    return ToolAgentRunner()


@dataclass(frozen=True)
class ReceptionistProfile:
    """One receptionist's identity and wiring choices. Pure data."""

    name: str                       # the agents/<name>/ folder, and --agent's value
    display_name: str               # what the console and the user call her
    default_session: str
    nexus_subdir: str
    system_prompt: str
    roster: Roster
    build_runner: Callable[[], IDelegationRunner]
    # Whose recorded turns she recalls alongside her own, and how that block is
    # labelled in the recalled transcript. The two receptionists share one
    # LanceDB table, so each can hear what the user told the other.
    peer_session: str | None = None
    peer_label: str | None = None
    # Further (session, label) peers, recalled the same way and in this order.
    # Toyota hears both Mazda and Sydney; each extra peer is one more
    # PeerSessionRecall wrapped around the first, so each labels only its own.
    extra_peers: tuple[tuple[str, str], ...] = ()
    # Her conversational/routing model, served by Groq. A plain string rather
    # than an import from adapters/groq_model.py, so reading a profile never
    # drags the openai client in -- same reasoning as the deferred runner
    # imports above. Both entrypoints' --model override this when given.
    # Both receptionists now name their own; the default is the cheap one, so
    # a third receptionist starts inside the Groq allowance rather than
    # draining it.
    chat_model: str = "openai/gpt-oss-120b"
    # How many characters of recalled transcript her turn prompt may carry.
    # None means settings.recall_max_chars (12000). This is a profile field
    # because Groq's real ceiling is 8000 tokens *per minute*, shared across
    # models, not a daily allowance -- so what matters is the size of one turn.
    # Her rendered system prompt is ~9900 characters and the operations
    # capabilities block adds ~7000 more on every turn, so the shared 12000
    # recall budget pushed her single turn past the ceiling and the next one
    # came back 429.
    recall_max_chars: int | None = None
    # None means "whatever voice the dashboard's /api/tts defaults to"
    # (en-GB-SoniaNeural). Mazda names her own so the two sound different.
    # main.py lets <NAME>_EDGE_TTS_VOICE in the environment override it, which
    # is why this is a plain default and not an os.environ read here -- .env
    # isn't loaded yet at import time.
    tts_voice: str | None = None
    # OPEN_AGENT (AgentHandoffModel) and the operations/WorkflowModel stack are
    # both dashboard-side features Toyota owns; Mazda has neither yet.
    handoff: bool = False
    operations: bool = False
    # The loopback port her service.py sidecar listens on, so the browser
    # dashboard can hold a warm agent instead of paying the embedder's ~30s
    # load per turn. One port per receptionist: 8765 is the dashboard, 8766 is
    # thought_bridge.py. Unique across PROFILES -- asserted below.
    service_port: int = 0
    # Whether this sidecar also answers /memory/compose and /memory/record on
    # its peer's behalf. Toyota does, because the dashboard can still run Mazda
    # as a Letta agent whose turns happen outside this package and would
    # otherwise have no LanceDB recall. Mazda's own sidecar has no such guest.
    serves_peer_memory: bool = False
    # A route taken from the user's own words, before the model gets a say --
    # see delegation.IRouteOverride. None leaves every routing decision to the
    # model; both receptionists now name one, for the same failure in two
    # places: a question the model answered from recalled conversation when it
    # should have asked a tool. Stateless and import-light, so the instance
    # sits here rather than behind a factory like build_runner.
    route_override: IRouteOverride | None = None


TOYOTA = ReceptionistProfile(
    name="toyota",
    display_name="Toyota",
    default_session="toyota",
    nexus_subdir="toyota",
    system_prompt=TOYOTA_SYSTEM_PROMPT,
    roster=TOYOTA_ROSTER,
    build_runner=_toyota_runner,
    peer_session="mazda",
    peer_label="with Mazda",
    # Sydney does the non-financial work now, so what the user asked her is
    # still Toyota's to remember.
    extra_peers=(("sydney", "with Sydney"),),
    # The 120b, same as Mazda -- see roles.TOYOTA_CHAT_MODEL.
    chat_model=TOYOTA_CHAT_MODEL,
    # Tighter than the 12000 default: her routing prompt and the per-turn
    # capabilities block eat most of the per-minute budget between them.
    # ~9900 chars of system prompt plus 7000 of capabilities plus 6000 of
    # recall is about 5700 tokens a turn, which fits under 8000 with room for
    # the reply. It does not fit twice -- one turn a minute is the real rate.
    recall_max_chars=6000,
    handoff=True,
    operations=True,
    service_port=8767,
    serves_peer_memory=True,
    # Her prompt tells her to send every question about a real figure to the
    # books, and every request for a sheet to the spreadsheet maker. Neither
    # is left to the model to agree to, for the same reason Mazda's status
    # route isn't: with no way to read a real number she answered from
    # recalled conversation instead and invented plausible amounts, and 66
    # stored training turns kept handing the habit back to her as history.
    #
    # Spreadsheet first, deliberately. "What did we spend on utilities as a
    # spreadsheet" is word for word a question about money, and both routes
    # recognize it; the one that produces a file the user can check is the
    # one that should win. See adapters/route_panel.py. Tax progress goes
    # ahead of both: it builds the page (adapters/tax_progress_route.py).
    route_override=FirstRouteOpinion([
        TaxProgressPageRoute("tax_progress_page"),
        SpreadsheetRequestRoute(CATEGORY_SPREADSHEET_AGENT_NAME),
        FigureQuestionRoute(LIVE_BOOKS_AGENT_NAME),
    ]),
)

MAZDA = ReceptionistProfile(
    name="mazda",
    display_name="Mazda",
    default_session="mazda",
    nexus_subdir="mazda",
    system_prompt=MAZDA_SYSTEM_PROMPT,
    roster=MAZDA_ROSTER,
    # Mickey answers for real; the other five are Letta agents this package
    # can't reach yet, so they fall to UnwiredRunner, which says so plainly
    # instead of implying work happened. Wiring one in is a change to
    # _mazda_runner's map, nothing else.
    build_runner=_mazda_runner,
    peer_session="toyota",
    peer_label="with Toyota",
    tts_voice="en-US-AriaNeural",
    service_port=8768,
    # The 120b, same as Toyota -- see mazda_roles.MAZDA_CHAT_MODEL, which her
    # system prompt is also rendered with so she names the right one.
    chat_model=MAZDA_CHAT_MODEL,
    # Her prompt says every status question goes to Mickey. The 20b often
    # answered from recalled conversation instead, re-asserting a year of
    # finished reports that do not exist -- so the route no longer depends on
    # the model agreeing to take it.
    route_override=StatusQuestionRoute(MICKEY_AGENT_NAME),
)

SYDNEY = ReceptionistProfile(
    name="sydney",
    display_name="Sydney",
    default_session="sydney",
    nexus_subdir="sydney",
    system_prompt=SYDNEY_SYSTEM_PROMPT,
    # The complement of Toyota's finance roster: code, agents, the mechanic,
    # the browser and web search, ADWs. See sydney_roles.py.
    roster=SYDNEY_ROSTER,
    build_runner=_sydney_runner,
    # A clone of Toyota, so she hears what the user told Toyota, out of the
    # same shared LanceDB table.
    peer_session="toyota",
    peer_label="with Toyota",
    chat_model=SYDNEY_CHAT_MODEL,
    # Same per-minute arithmetic as Toyota's: her prompt is about the same
    # size, so the same recall budget keeps one turn under Groq's ceiling.
    recall_max_chars=6000,
    tts_voice="en-AU-NatashaNeural",
    # OPEN_AGENT: toyota hands money questions back to the front desk.
    handoff=True,
    # No WorkflowModel: its capability block is Toyota's finance WORK stack.
    # Every control line Sydney can emit is stated in her own system prompt.
    operations=False,
    service_port=8769,
    # No route override: her forced routes would all be Toyota's (figures,
    # spreadsheets, tax progress), and those are answered by handing off.
)

PROFILES: dict[str, ReceptionistProfile] = {
    TOYOTA.name: TOYOTA, MAZDA.name: MAZDA, SYDNEY.name: SYDNEY,
}

# Two receptionists sharing a port would mean the dashboard silently talking to
# whichever sidecar won the bind. Fail at import instead.
_ports = [p.service_port for p in PROFILES.values()]
if len(set(_ports)) != len(_ports):
    raise RuntimeError(f"two receptionists claim the same sidecar port: {sorted(_ports)}")


def profile(name: str) -> ReceptionistProfile:
    """Look up a profile by --agent's value, naming the alternatives on a miss."""
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError(
            f"Unknown receptionist {name!r}. Known: {', '.join(sorted(PROFILES))}."
        ) from None
