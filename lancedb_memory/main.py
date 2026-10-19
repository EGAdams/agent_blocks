# unset ANTHROPIC_API_KEY
# unset ANTHROPIC_BASE_URL
"""Composition root and REPL.

This is the ONLY module allowed to name concrete implementations. Everything
above it depends on Protocols. To swap the vector store, the embedder, or the
model, edit build_agent() and nothing else.
"""
import argparse
import asyncio

from agent_memory.adapters.claude_model import ClaudeAgentModel
from agent_memory.adapters.decorators import RetryingModel
from agent_memory.adapters.embedding_profiles import build_embedder
from agent_memory.adapters.in_memory_store import InMemoryMemoryStore
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
from agent_memory.ports import MemoryStore
from agent_memory.recall import RecencyFloorRecall, SimilarityRecall, TokenBudgetedRecall


def build_agent( settings: Settings, persist: bool = True ) -> tuple[ ConversationAgent, CostObserver ]:
    embedder, profile_table_name = build_embedder( settings.embedding_profile )
    table_name = settings.table_name or profile_table_name

    store: MemoryStore
    if persist:
        store = LanceDBMemoryStore(
            uri=settings.lancedb_uri,
            table_name=table_name,
            dimensions=embedder.dimensions,
        )
    else:
        store = InMemoryMemoryStore()

    recall = TokenBudgetedRecall(
        RecencyFloorRecall(
            SimilarityRecall(
                store,
                limit=settings.recall_limit,
                session_scoped=settings.session_scoped_recall,
            ),
            store,
            floor=settings.recall_recency_floor,
        ),
        max_chars=settings.recall_max_chars,
    )

    cost = CostObserver()
    observers = [ ConsoleObserver(), cost ]
    if persist:
        observers.append( NexusArchiveObserver( settings.nexus_dir ))

    agent = ConversationAgent(
        session_id=settings.session_id,
        embedder=embedder,
        store=store,
        recall=recall,
        composer=TemplatePromptComposer.from_file( settings.prompt_template_path ),
        model=RetryingModel( ClaudeAgentModel( model=settings.model )),
        clock=SystemClock(),
        observer=CompositeObserver( observers ),
    )
    return agent, cost


async def repl( settings: Settings, persist: bool ) -> None:
    agent, cost = build_agent( settings, persist=persist )
    print(
        f"session: { settings.session_id }   model: { settings.model }   "
        f"embedding_profile: { settings.embedding_profile }   persist: { persist }"
    )
    print( "ctrl-c to quit\n" )

    while True:
        try:
            user_input = input( "\n\nUSER: " )
        except ( EOFError, KeyboardInterrupt ):
            break

        if not user_input.strip():
            continue

        print( "\nAGENT: ", end="", flush=True )
        reply = await agent.handle_turn( user_input )
        if not reply.text:
            print( "( empty reply)" )

    print( f"\n\nSession cost: ${ cost.total_cost_usd:.6f }   "
           f"in={ cost.total_input_tokens } out={ cost.total_output_tokens }" )


if __name__ == "__main__":
    parser = argparse.ArgumentParser( description="LanceDB-backed conversational agent" )
    parser.add_argument( "--session", default="default", help="session id for memory scoping" )
    parser.add_argument( "--model", default=Settings().model )
    parser.add_argument(
        "--embedding-profile",
        default=Settings().embedding_profile,
        choices=[ "hash", "sentence", "openai" ],
        help="hash: free/instant, for tests. sentence: free local semantic model, "
             "for human chats. openai: costs money, best quality.",
    )
    parser.add_argument( "--no-persist", action="store_true", help="run with in-memory storage" )
    args = parser.parse_args()

    asyncio.run(
        repl(
            Settings(
                session_id=args.session,
                model=args.model,
                embedding_profile=args.embedding_profile,
            ),
            persist=not args.no_persist,
        )
    )
