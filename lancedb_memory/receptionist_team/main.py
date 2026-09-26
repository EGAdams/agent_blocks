# unset ANTHROPIC_API_KEY
# unset ANTHROPIC_BASE_URL
"""Toyota -- the finance team's receptionist.

Conversational front door for the 6-agent finance_team (mapper_cash,
mapper_card, mapper_bank, mapper_other, synthesizer, receipt_scanner): the
same LanceDB-memory conversational engine as agent_memory/main.py (this is
the composition root -- the only module here allowed to name concrete
implementations), wrapped with a RoutingModel decorator so a request that
names a finance task gets delegated to the matching finance_team role and its
result relayed back in conversation.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.main                   # typed chat
    .venv/bin/python -m receptionist_team.main --voice            # mic in / spoken out
    .venv/bin/python -m receptionist_team.main --role-model claude-haiku-4-5-20251001

Voice reuses the dashboard's already-built pipeline (whisper/Groq STT via
POST /api/voice, edge-tts via POST /api/tts) instead of re-implementing
speech I/O here -- dashboard-server.service must already be running on
localhost:8765; if it isn't, --voice fails closed to typed input per turn
rather than guessing.

Toyota's own conversational/routing replies go straight to Groq's chat API
(GroqLanguageModel, see agent_memory/adapters/groq_model.py) -- a plain HTTP
call, no claude_agent_sdk.query() subprocess spawn, since that overhead isn't
worth paying for simple conversation. Delegated finance_team work still runs
on Claude (RoutingModel's `model=role_model`, via run_role/tool_agent.py),
same auth rule as every other adapter in agent_blocks: ANTHROPIC_API_KEY and
ANTHROPIC_BASE_URL must stay UNSET so that call uses local Claude Code
subscription (OAuth) auth, no separate paid API key -- it needs real tool use
(Bash/Read/Write) against the DB/receipts, which is what the SDK is for.
GROQ_API_KEY must be set (see receptionist_team/.env) for both Toyota's own
replies and voice_io.py's STT.
"""
import argparse
import asyncio
import os
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
from agent_memory.prompts import TemplatePromptComposer
from agent_memory.recall import SimilarityRecall, TokenBudgetedRecall

from .adapters.metrics import Stopwatch
from .adapters.routing_model import RoutingModel
from .adapters.voice_io import DashboardVoiceIO, VoiceUnavailableError
from .roles import TOYOTA_SYSTEM_PROMPT


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


def build_toyota(settings: Settings, role_model: str) -> tuple[ConversationAgent, CostObserver]:
    embedder, profile_table_name = build_embedder(settings.embedding_profile)
    store = LanceDBMemoryStore(
        uri=settings.lancedb_uri,
        table_name=settings.table_name or profile_table_name,
        dimensions=embedder.dimensions,
    )
    recall = TokenBudgetedRecall(
        SimilarityRecall(store, limit=settings.recall_limit, session_scoped=settings.session_scoped_recall),
        max_chars=settings.recall_max_chars,
    )

    cost = CostObserver()
    # Toyota's own conversational replies go straight to Groq -- one HTTP call,
    # no per-turn claude_agent_sdk CLI subprocess spawn (see groq_model.py).
    # RoutingModel still sees the whole reply before deciding on a ROUTE
    # directive; a delegated finance_team role still runs on Claude (below),
    # since that needs real tool use against the DB/receipts, not just chat.
    inner_model = GroqLanguageModel(model=settings.model, system_prompt=TOYOTA_SYSTEM_PROMPT)
    model = RoutingModel(RetryingModel(inner_model), model=role_model)

    agent = ConversationAgent(
        session_id=settings.session_id,
        embedder=embedder,
        store=store,
        recall=recall,
        composer=TemplatePromptComposer(),
        model=model,
        clock=SystemClock(),
        observer=CompositeObserver([ConsoleObserver(), cost, NexusArchiveObserver(settings.nexus_dir)]),
    )
    return agent, cost


async def repl(settings: Settings, role_model: str, voice: bool) -> None:
    agent, cost = build_toyota(settings, role_model)
    voice_io = DashboardVoiceIO() if voice else None
    print(f"Toyota -- session: {settings.session_id}  model: {settings.model}  voice: {voice}")
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

        print("[thinking...]")
        with Stopwatch("thinking", detail=f"chars={len(user_input)}"):
            reply = await agent.handle_turn(user_input)
        print(f"\nTOYOTA: {reply.text}")
        if voice_io is not None:
            voice_io.speak(reply.text)

    print(
        f"\nSession cost: ${cost.total_cost_usd:.6f}   "
        f"in={cost.total_input_tokens} out={cost.total_output_tokens}"
    )


def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session", default="toyota", help="session id for memory scoping")
    parser.add_argument(
        "--model",
        default="openai/gpt-oss-120b",
        help=(
            "Toyota's own conversational/routing model, served by Groq (not Claude) -- "
            "swap to a faster Groq model here, e.g. openai/gpt-oss-20b, with no code change"
        ),
    )
    parser.add_argument(
        "--role-model",
        default="claude-sonnet-5",
        help="model used when a request is delegated to a finance_team role",
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

    settings = Settings(session_id=args.session, model=args.model, embedding_profile=args.embedding_profile)
    settings = settings.model_copy(update={"nexus_dir": settings.nexus_dir / "toyota"})

    asyncio.run(repl(settings, role_model=args.role_model, voice=args.voice))


if __name__ == "__main__":
    main()
