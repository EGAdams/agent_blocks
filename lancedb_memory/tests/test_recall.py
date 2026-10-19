"""RecallStrategy regression tests. No network, no disk, no LanceDB.

Run:  .venv/bin/python tests/test_recall.py
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parent.parent ))

from agent_memory.adapters.hashing_embedder import HashingEmbedder
from agent_memory.adapters.in_memory_store import InMemoryMemoryStore
from agent_memory.models import EmbeddedTurn, Turn
from agent_memory.recall import RecencyFloorRecall, SimilarityRecall


async def _seed( store, session_id, texts, embedder, start ):
    """Insert turns one second apart, oldest first."""
    for i, text in enumerate( texts ):
        vector = await embedder.embed( text )
        store.add([ EmbeddedTurn(
            turn=Turn(
                session_id=session_id,
                speaker="USER" if i % 2 == 0 else "AGENT",
                message=text,
                created_at=start + timedelta( seconds=i ),
            ),
            vector=vector,
        )])


async def test_pure_similarity_recall_can_lose_the_truly_recent_turn_to_an_old_crowd():
    # This reproduces the live defect directly: 40 old turns share wording with
    # the query, so they fill every one of similarity recall's top-K slots and
    # the one genuinely relevant, most-recent exchange never gets in.
    store = InMemoryMemoryStore()
    embedder = HashingEmbedder( dimensions=256 )
    start = datetime( 2026, 1, 1, tzinfo=timezone.utc )
    await _seed( store, "s1", [ "status check on the ticket" ] * 40, embedder, start )
    await _seed( store, "s1", [
        "Change the category to Walgreens RJ for Robert Gerald.",
        "Which exact category should I use?",
    ], embedder, start + timedelta( seconds=1000 ))

    recall = SimilarityRecall( store, limit=10 )
    query = await embedder.embed( "Can you check the conversation history?" )
    turns = recall.recall( "s1", query )

    assert all( "Walgreens" not in t.message for t in turns ), \
        "expected the defect: the recent exchange is crowded out by old lookalikes"


async def test_recency_floor_guarantees_the_most_recent_turns_are_always_recalled():
    store = InMemoryMemoryStore()
    embedder = HashingEmbedder( dimensions=256 )
    start = datetime( 2026, 1, 1, tzinfo=timezone.utc )
    await _seed( store, "s1", [ "status check on the ticket" ] * 40, embedder, start )
    await _seed( store, "s1", [
        "Change the category to Walgreens RJ for Robert Gerald.",
        "Which exact category should I use?",
    ], embedder, start + timedelta( seconds=1000 ))

    recall = RecencyFloorRecall( SimilarityRecall( store, limit=10 ), store, floor=5 )
    query = await embedder.embed( "Can you check the conversation history?" )
    turns = recall.recall( "s1", query )

    assert any( "Walgreens RJ" in t.message for t in turns ), \
        f"recency floor should have pulled in the recent exchange, got { [ t.message for t in turns ] }"


async def test_recency_floor_result_stays_chronological_and_deduplicated():
    store = InMemoryMemoryStore()
    embedder = HashingEmbedder( dimensions=256 )
    start = datetime( 2026, 1, 1, tzinfo=timezone.utc )
    await _seed( store, "s1", [ "alpha one", "alpha two", "alpha three", "alpha four" ], embedder, start )

    recall = RecencyFloorRecall( SimilarityRecall( store, limit=10 ), store, floor=3 )
    query = await embedder.embed( "alpha" )
    turns = recall.recall( "s1", query )

    times = [ t.created_at for t in turns ]
    assert times == sorted( times ), times
    assert len( turns ) == len({ t.id for t in turns }), "duplicates between similarity hits and the floor"


async def test_recency_floor_respects_session_scoping():
    store = InMemoryMemoryStore()
    embedder = HashingEmbedder( dimensions=256 )
    start = datetime( 2026, 1, 1, tzinfo=timezone.utc )
    await _seed( store, "alice", [ "alice secret passphrase orangutan" ], embedder, start )
    await _seed( store, "bob", [ "unrelated bob chatter" ], embedder, start )

    recall = RecencyFloorRecall( SimilarityRecall( store, limit=10 ), store, floor=5 )
    query = await embedder.embed( "what did we just say?" )
    turns = recall.recall( "bob", query )

    assert all( "orangutan" not in t.message for t in turns ), \
        "the recency floor must not leak another session's turns"


async def main():
    tests = [ value for name, value in sorted( globals().items()) if name.startswith( "test_" )]
    failures = 0
    for test in tests:
        try:
            await test()
            print( f"PASS  { test.__name__ }" )
        except AssertionError as oops:
            failures += 1
            print( f"FAIL  { test.__name__ }: { oops }" )
    print( f"\n{ len( tests ) - failures }/{ len( tests )} passed" )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit( asyncio.run( main()))
