"""ReceptionistProfile -- everything that differs between two receptionists.

Toyota and Mazda are the same engine. What varies between them is a short
list of values: which agents/ folder holds their prompts, which roster they
can route to, how a delegation actually runs, which session and nexus
subdirectory their memory uses, whose turns they recall as a peer, which TTS
voice speaks for them, and whether the dashboard-only decorators
(AgentHandoffModel, WorkflowModel) apply at all.

So that list is a frozen dataclass and main.py's build_receptionist() reads
it. Role config, not a wire contract -- same reasoning as finance_team's
AgentRole, so a dataclass rather than a pydantic BaseModel.

Adding a third receptionist is one more PROFILES entry plus her agents/
folder; it is not another build_*() function.
"""
from collections.abc import Callable
from dataclasses import dataclass

from .delegation import IDelegationRunner, RunnerMap, UnwiredRunner
from .mazda_roles import (
    MAZDA_CHAT_MODEL,
    MAZDA_ROSTER,
    MAZDA_SYSTEM_PROMPT,
    ROLE_SPOKEN_NAMES as MAZDA_SPOKEN_NAMES,
)
from .progress.michi import AGENT_NAME as MICHI_AGENT_NAME
from .roles import TOYOTA_ROSTER, TOYOTA_SYSTEM_PROMPT
from .rosters import Roster


def _mazda_runner() -> IDelegationRunner:
    """Michi really runs; the other four aren't connected yet.

    Imported here rather than at module scope so adapters/michi_runner.py's
    GroqLanguageModel import stays off the path of anything that only reads
    the profiles.
    """
    from .adapters.michi_runner import MichiRunner

    return RunnerMap(
        {MICHI_AGENT_NAME: MichiRunner()},
        default=UnwiredRunner(MAZDA_SPOKEN_NAMES),
    )


def _toyota_runner() -> IDelegationRunner:
    """Imported here, not at module scope, on purpose.

    adapters/tool_agent_runner.py pulls in finance_team (and through it
    claude_agent_sdk and the rol_finances repo root). Mazda routes to nobody
    real yet, so building her profile must not drag that in -- and tests that
    only check her wiring shouldn't need the SDK installed.
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
    # Her conversational/routing model, served by Groq. A plain string rather
    # than an import from adapters/groq_model.py, so reading a profile never
    # drags the openai client in -- same reasoning as the deferred runner
    # imports above. Both entrypoints' --model override this when given.
    chat_model: str = "openai/gpt-oss-120b"
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
    handoff=True,
    operations=True,
    service_port=8767,
    serves_peer_memory=True,
)

MAZDA = ReceptionistProfile(
    name="mazda",
    display_name="Mazda",
    default_session="mazda",
    nexus_subdir="mazda",
    system_prompt=MAZDA_SYSTEM_PROMPT,
    roster=MAZDA_ROSTER,
    # Michi answers for real; the other five are Letta agents this package
    # can't reach yet, so they fall to UnwiredRunner, which says so plainly
    # instead of implying work happened. Wiring one in is a change to
    # _mazda_runner's map, nothing else.
    build_runner=_mazda_runner,
    peer_session="toyota",
    peer_label="with Toyota",
    tts_voice="en-US-AriaNeural",
    service_port=8768,
    # The 20b, not Toyota's 120b -- see mazda_roles.MAZDA_CHAT_MODEL, which her
    # system prompt is also rendered with so she names the right one.
    chat_model=MAZDA_CHAT_MODEL,
)

PROFILES: dict[str, ReceptionistProfile] = {TOYOTA.name: TOYOTA, MAZDA.name: MAZDA}

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
