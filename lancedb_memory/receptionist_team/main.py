# unset ANTHROPIC_API_KEY
# unset ANTHROPIC_BASE_URL
"""The receptionists -- Toyota and Mazda, one engine, two profiles.

Conversational front door for a team of headless specialist agents: the same
LanceDB-memory conversational engine as agent_memory/main.py (this is the
composition root -- the only module here allowed to name concrete
implementations), wrapped with a RoutingModel decorator so a request that
names a specialist's task gets delegated to that role and its result relayed
back in conversation.

Which receptionist you get is `--agent`, and everything that differs between
them lives in profiles.py (prompts folder, roster, how a delegation runs,
session, nexus subdirectory, peer recall, TTS voice, which decorators apply).

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.main                    # Toyota, typed chat
    .venv/bin/python -m receptionist_team.main --voice             # mic in / spoken out
    .venv/bin/python -m receptionist_team.main --agent mazda       # Mazda, typed chat
    .venv/bin/python -m receptionist_team.main --role-model claude-haiku-4-5-20251001

Voice reuses the dashboard's already-built pipeline (whisper/Groq STT via
POST /api/voice, edge-tts via POST /api/tts) instead of re-implementing
speech I/O here -- dashboard-server.service must already be running on
localhost:8765; if it isn't, --voice fails closed to typed input per turn
rather than guessing.

A receptionist's own conversational/routing replies go straight to Groq's chat
API (GroqLanguageModel, see agent_memory/adapters/groq_model.py) -- a plain
HTTP call, no claude_agent_sdk.query() subprocess spawn, since that overhead
isn't worth paying for simple conversation. Delegated finance_team work still
runs on Claude (RoutingModel's `model=role_model`, via
adapters/tool_agent_runner.py), same auth rule as every other adapter in
agent_blocks: ANTHROPIC_API_KEY and ANTHROPIC_BASE_URL must stay UNSET so that
call uses local Claude Code subscription (OAuth) auth, no separate paid API key
-- it needs real tool use (Bash/Read/Write) against the DB/receipts, which is
what the SDK is for. GROQ_API_KEY must be set (see receptionist_team/.env) for
both the receptionists' own replies and voice_io.py's STT.
"""
import argparse
import asyncio
import os
import re
from collections.abc import Callable
from pathlib import Path

from agent_memory.adapters.decorators import RetryingModel
from agent_memory.adapters.embedding_profiles import build_embedder
from agent_memory.adapters.groq_model import GroqLanguageModel
from agent_memory.adapters.lancedb_store import LanceDBMemoryStore
from agent_memory.agent import ConversationAgent
from agent_memory.clock import SystemClock
from agent_memory.config import Settings
from agent_memory.observers import (
    CompositeObserver,
    ConsoleObserver,
    CostObserver,
    NexusArchiveObserver,
)
from agent_memory.ports import Embedder
from agent_memory.prompts import RecallOnlyComposer, TemplatePromptComposer
from agent_memory.recall import (
    PeerSessionRecall,
    RecencyFloorRecall,
    SimilarityRecall,
    TokenBudgetedRecall,
)

from .agents import catalog as agent_catalog
from .adapters.handoff_model import AgentHandoffModel
from .adapters.metrics import Stopwatch
from .adapters.routing_model import RoutingModel
from .adapters.voice_io import AcknowledgmentSpeaker, DashboardVoiceIO, VoiceUnavailableError
from .memory_desk import MemoryDesk
from .profiles import PROFILES, ReceptionistProfile, TOYOTA, profile as named_profile
from .operations.conversation import OperationalConversationAgent
from .operations.http_client import CallbackWorkObserver, DashboardWorkflows
from .operations.interfaces import RequestConversation
from .operations.assignment_policies import InstalledPolicies
from .operations.tax_intent import TaxIssuesPolicy
from .operations.reports_intent import ReportsPolicy
from .operations.request_context import ScopedTurnContext
from .operations.workflow_model import WorkflowModel


def _load_dotenv() -> None:
    """Load receptionist_team/.env into os.environ if not already set.

    No python-dotenv in this venv, and forgetting to `source` the file is a
    recurring footgun (GroqLanguageModel/voice_io.py both read GROQ_API_KEY /
    PIPECAT_PILOT_AGENT_ID straight from the environment) -- so just load it
    ourselves rather than relying on the caller's shell.
    """
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


# Mazda's session in the shared store, as the dashboard's Letta Mazda writes
# it (build_mazda_desk below reads and writes that session on her behalf).
# profiles.MAZDA.default_session is the same string for the in-process Mazda.
MAZDA_SESSION = "mazda"


def build_shared_memory(settings: Settings) -> tuple[Embedder, LanceDBMemoryStore]:
    """One embedder and one LanceDB table, shared by Toyota and Mazda's desk."""
    embedder, profile_table_name = build_embedder(settings.embedding_profile)
    store = LanceDBMemoryStore(
        uri=settings.lancedb_uri,
        table_name=settings.table_name or profile_table_name,
        dimensions=embedder.dimensions,
    )
    return embedder, store


def build_mazda_desk(
    embedder: Embedder,
    store: LanceDBMemoryStore,
    toyota_session: Callable[[], str],
) -> MemoryDesk:
    """Recall for Mazda's turns: her own recorded turns plus Toyota's current ones.

    Smaller than Toyota's budget -- Letta already carries Mazda's recent
    history, and every recalled line becomes part of her Letta conversation.
    """
    recall = TokenBudgetedRecall(
        PeerSessionRecall(
            RecencyFloorRecall(SimilarityRecall(store, limit=8), store, floor=4),
            store,
            peer_session=toyota_session,
            label="with Toyota",
        ),
        max_chars=4000,
    )
    return MemoryDesk(embedder, store, recall, RecallOnlyComposer(), SystemClock())


def build_receptionist(
    profile: ReceptionistProfile,
    settings: Settings,
    role_model: str,
    on_delegation: Callable[[str], None] | None = None,
    on_work: Callable[[dict], None] | None = None,
    on_handoff: Callable[[dict], None] | None = None,
    memory: tuple[Embedder, LanceDBMemoryStore] | None = None,
) -> tuple[RequestConversation, CostObserver]:
    """One engine, one profile. Toyota and Mazda differ only in the profile.

    Everything receptionist-specific -- prompts, roster, how a delegation
    runs, whose turns she recalls as a peer, which decorators apply -- comes
    from `profile` (see profiles.py). This stays the composition root: the
    only module here allowed to name concrete implementations.
    """
    embedder, store = memory or build_shared_memory(settings)
    similarity = SimilarityRecall(
        store, limit=settings.recall_limit, session_scoped=settings.session_scoped_recall
    )
    recall = RecencyFloorRecall(similarity, store, floor=settings.recall_recency_floor)
    if profile.peer_session is not None:
        # What the user said to the other receptionist is this one's to
        # remember too -- they share one LanceDB table.
        recall = PeerSessionRecall(
            recall,
            store,
            peer_session=lambda: profile.peer_session,
            label=profile.peer_label or f"with {profile.peer_session}",
        )
    recall = TokenBudgetedRecall(recall, max_chars=settings.recall_max_chars)

    cost = CostObserver()
    # Her own conversational replies go straight to Groq -- one HTTP call, no
    # per-turn claude_agent_sdk CLI subprocess spawn (see groq_model.py).
    # RoutingModel still sees the whole reply before deciding on a ROUTE
    # directive; a delegated role runs on whatever the profile's runner uses
    # (Claude, for Toyota's finance roles), since that needs real tool use
    # against the DB/receipts, not just chat.
    inner_model = GroqLanguageModel(model=settings.model, system_prompt=profile.system_prompt)
    # on_delegation stays None for the REPL (RoutingModel just prints); the
    # sidecar passes a sink so the acknowledgment reaches the browser's
    # speaker while the delegated role is still running. See service.py.
    model = RoutingModel(
        RetryingModel(inner_model),
        model=role_model,
        roster=profile.roster,
        runner=profile.build_runner(),
        speaker=profile.display_name,
        on_delegation=on_delegation,
    )
    context = ScopedTurnContext()
    if profile.handoff:
        # OPEN_AGENT: mazda -> the browser opens her window (handoff_model.py).
        model = AgentHandoffModel(model, on_handoff=on_handoff)
    if profile.operations:
        model = WorkflowModel(model, DashboardWorkflows(os.getenv('TOYOTA_DASHBOARD_URL', 'http://127.0.0.1:8765')),
                              context, InstalledPolicies({'tax_issues': TaxIssuesPolicy(), 'reports': ReportsPolicy()}),
                              CallbackWorkObserver(on_work))

    agent = ConversationAgent(
        session_id=settings.session_id,
        embedder=embedder,
        store=store,
        recall=recall,
        # Her turn template is agents/<name>/user_prompt_template/user_prompt.md,
        # not prompt_template_path -- one folder per agent holds both halves of
        # her prompt. The file is byte-identical to
        # agent_memory.prompts.DEFAULT_TEMPLATE (pinned by
        # agents/tests/test_agent_catalog.py), so this is the same prompt as
        # before, now stated where it can be edited without touching Python.
        composer=TemplatePromptComposer(agent_catalog().definition(profile.name).user_prompt().raw()),
        model=model,
        clock=SystemClock(),
        observer=CompositeObserver([ConsoleObserver(), cost, NexusArchiveObserver(settings.nexus_dir)]),
    )
    # OperationalConversationAgent wraps unconditionally: it satisfies
    # RequestConversation, which service.py and turn_runner.py both type
    # against, and it is a no-op when no WorkflowModel is writing to `context`.
    return OperationalConversationAgent(agent, context), cost


def build_toyota(
    settings: Settings,
    role_model: str,
    on_delegation: Callable[[str], None] | None = None,
    on_work: Callable[[dict], None] | None = None,
    on_handoff: Callable[[dict], None] | None = None,
    memory: tuple[Embedder, LanceDBMemoryStore] | None = None,
) -> tuple[RequestConversation, CostObserver]:
    """Toyota, specifically. Kept as a named function because turn_runner.py
    and the sidecar tests reference it by name."""
    return build_receptionist(
        TOYOTA, settings, role_model,
        on_delegation=on_delegation, on_work=on_work, on_handoff=on_handoff, memory=memory,
    )


# "/compact" alone is not reachable with your hands off the keyboard -- voice
# input is transcribed speech, and a mic never produces a literal slash. This
# also matches the natural phrases a person actually says ("compact the
# conversation", "clear your memory", ...), so --voice sessions can trigger
# the same clear_context() path without typing. Matched with re.fullmatch,
# not search, same rule as RoutingModel's ROUTE line (see CLAUDE.md): ordinary
# conversation that merely mentions "context" must not misfire.
_COMPACT_COMMAND_PATTERN = re.compile(
    r"/compact|(?:compact|clear)\s+(?:the\s+|your\s+)?(?:conversation|context|memory)",
)


def _is_compact_command(text: str) -> bool:
    """True for "/compact" or a natural spoken equivalent, false otherwise.

    STT transcripts carry filler punctuation and politeness a typed command
    never would ("Compact the conversation, please."), so the text is
    normalized -- lowercased, stripped of ,.!? , and a leading/trailing
    "please" dropped -- before the fullmatch above decides.
    """
    normalized = re.sub(r"[,.!?]", "", text.strip().lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = re.sub(r"^please\s+|\s+please$", "", normalized)
    return bool(_COMPACT_COMMAND_PATTERN.fullmatch(normalized))


async def repl(profile: ReceptionistProfile, settings: Settings, role_model: str, voice: bool) -> None:
    # <NAME>_EDGE_TTS_VOICE wins over the profile's default, which in turn
    # wins over whatever /api/tts would have picked on its own.
    tts_voice = os.environ.get(f"{profile.name.upper()}_EDGE_TTS_VOICE", profile.tts_voice)
    voice_io = DashboardVoiceIO(voice=tts_voice) if voice else None
    # Wire the speaker into the delegation hook so --voice speaks the
    # acknowledgment the instant a ROUTE is matched, same as the sidecar's
    # relay does for the browser -- see RoutingModel.__init__ and
    # CLAUDE.md's "The delegation acknowledgment" section. It must be
    # AcknowledgmentSpeaker.say and not voice_io.speak directly: speak() is
    # fully blocking, so the sink would play "please wait" *before* the
    # delegation starts instead of during it, which is the opposite of the
    # point. speaker.wait() below joins playback before the reply is spoken so
    # two players never talk over each other. Typed mode has no voice_io, so
    # on_delegation stays None and RoutingModel just prints.
    speaker = AcknowledgmentSpeaker(voice_io) if voice_io is not None else None
    agent, cost = build_receptionist(
        profile, settings, role_model,
        on_delegation=speaker.say if speaker is not None else None,
    )
    name = profile.display_name
    print(f"{name} -- session: {settings.session_id}  model: {settings.model}  voice: {voice}")
    print("ctrl-c to quit\n")

    while True:
        try:
            if voice_io is not None:
                try:
                    user_input = voice_io.listen()
                    print(f"\nUSER (voice): {user_input}")
                except VoiceUnavailableError as exc:
                    print(f"[voice input failed: {exc}]\nfalling back to typed input this turn.")
                    user_input = input("\nUSER: ")
            else:
                user_input = input("\nUSER: ")
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input.strip():
            continue

        if _is_compact_command(user_input):
            # Same free operation as the dashboard's "compact conversation"
            # button (service.py's POST /compact): rotate the warm agent
            # onto a fresh recall scope so the context window stops growing,
            # without a Groq call or a persisted turn. See
            # agent_memory/agent.py's clear_context() docstring for why
            # nothing is deleted.
            new_session = agent.clear_context()
            confirmation = f"Conversation compacted -- new session: {new_session}"
            print(f"\n{name.upper()}: {confirmation}")
            if voice_io is not None:
                voice_io.speak(confirmation)
            continue

        print("[thinking...]")
        with Stopwatch("thinking", detail=f"chars={len(user_input)}"):
            reply = await agent.handle_turn(user_input)
        print(f"\n{name.upper()}: {reply.text}")
        if voice_io is not None:
            if speaker is not None:
                speaker.wait()  # let "please wait..." finish before the answer
            voice_io.speak(reply.text)

    print(
        f"\nSession cost: ${cost.total_cost_usd:.6f}   "
        f"in={cost.total_input_tokens} out={cost.total_output_tokens}"
    )


def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--agent",
        default=TOYOTA.name,
        choices=sorted(PROFILES),
        help="which receptionist to run; see profiles.py",
    )
    parser.add_argument(
        "--session",
        default=None,
        help="session id for memory scoping (default: the agent's own name)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "her own conversational/routing model, served by Groq (not Claude). "
            "Defaults to the chat_model on her profile (Toyota: gpt-oss-120b, "
            "Mazda: gpt-oss-20b); pass one here to override it for this run"
        ),
    )
    parser.add_argument(
        "--role-model",
        default="claude-sonnet-5",
        help="model used when a request is delegated to one of her specialists",
    )
    parser.add_argument(
        "--embedding-profile",
        default="sentence",
        choices=["hash", "sentence", "openai"],
        help="hash: free/instant. sentence: free local semantic model. openai: costs money.",
    )
    parser.add_argument(
        "--voice",
        action="store_true",
        help="mic in / spoken out, via the dashboard's existing voice pipeline (localhost:8765)",
    )
    args = parser.parse_args()

    profile = named_profile(args.agent)
    settings = Settings(
        session_id=args.session or profile.default_session,
        model=args.model or profile.chat_model,
        embedding_profile=args.embedding_profile,
    )
    settings = settings.model_copy(update={"nexus_dir": settings.nexus_dir / profile.nexus_subdir})

    asyncio.run(repl(profile, settings, role_model=args.role_model, voice=args.voice))


if __name__ == "__main__":
    main()
