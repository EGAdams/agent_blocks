"""Integration test for the real LanceDB repository. No network required.

Run:  .venv/bin/python tests/test_lancedb_store.py
"""
import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parent.parent ))

from agent_memory.adapters.hashing_embedder import HashingEmbedder
from agent_memory.adapters.lancedb_store import LanceDBMemoryStore
from agent_memory.agent import ConversationAgent
from agent_memory.observers import CompositeObserver, NexusArchiveObserver
from agent_memory.prompts import TemplatePromptComposer
from agent_memory.recall import SimilarityRecall, TokenBudgetedRecall
from tests.fakes import RecordingObserver, ScriptedModel, TickingClock


async def main() -> int:
    workdir = Path( tempfile.mkdtemp( prefix="lancedb_test_" ))
    failures = 0
    try:
        embedder = HashingEmbedder( dimensions=256 )
        store = LanceDBMemoryStore(
            uri=workdir / "lance_data",
            table_name="conversation_memory",
            dimensions=embedder.dimensions,
        )
        nexus = workdir / "nexus"
        recorder = RecordingObserver()
        model = ScriptedModel( [ "Noted.", "Teal.", "Sure." ])

        agent = ConversationAgent(
            session_id="s1",
            embedder=embedder,
            store=store,
            recall=TokenBudgetedRecall( SimilarityRecall( store, limit=30 )),
            composer=TemplatePromptComposer(),
            model=model,
            clock=TickingClock(),
            observer=CompositeObserver( [ NexusArchiveObserver( nexus ), recorder ]),
        )

        await agent.handle_turn( "my favourite colour is teal" )
        await agent.handle_turn( "what is my favourite colour?" )

        recalled = recorder.calls[ 1 ][ 2 ]
        if not any( "teal" in turn.message for turn in recalled ):
            print( f"FAIL  recall through LanceDB: got { [ t.message for t in recalled ]}" )
            failures += 1
        else:
            print( "PASS  recall through LanceDB" )

        if "teal" not in model.prompts[ 1 ]:
            print( "FAIL  recalled text reaches the prompt" )
            failures += 1
        else:
            print( "PASS  recalled text reaches the prompt" )

        archived = list( nexus.glob( "*.json" ))
        if len( archived ) != 4:
            print( f"FAIL  nexus archive: expected 4 files, got { len( archived )}" )
            failures += 1
        else:
            print( "PASS  nexus archives every turn" )

        # Recall must still work with the nexus deleted -- the old failure mode.
        shutil.rmtree( nexus )
        agent_b_recorder = RecordingObserver()
        agent_b = ConversationAgent(
            session_id="s1",
            embedder=embedder,
            store=store,
            recall=TokenBudgetedRecall( SimilarityRecall( store, limit=30 )),
            composer=TemplatePromptComposer(),
            model=ScriptedModel( [ "still here" ]),
            clock=TickingClock(),
            observer=agent_b_recorder,
        )
        await agent_b.handle_turn( "remind me about my colour" )
        if not any( "teal" in t.message for t in agent_b_recorder.calls[ 0 ][ 2 ]):
            print( "FAIL  recall survives a deleted nexus" )
            failures += 1
        else:
            print( "PASS  recall survives a deleted nexus" )

        # A dimension mismatch must fail loud, not write unsearchable rows.
        try:
            LanceDBMemoryStore(
                uri=workdir / "lance_data",
                table_name="conversation_memory",
                dimensions=1536,
            )
            print( "FAIL  dimension mismatch should raise" )
            failures += 1
        except ValueError:
            print( "PASS  dimension mismatch fails loud" )

        print( f"\n{ 5 - failures }/5 passed" )
        return 1 if failures else 0
    finally:
        shutil.rmtree( workdir, ignore_errors=True )


if __name__ == "__main__":
    sys.exit( asyncio.run( main()))
