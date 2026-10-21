"""Shared-memory tests: PeerSessionRecall and RecallOnlyComposer. No network, no disk.

Run:  .venv/bin/python tests/test_shared_memory.py
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parent.parent ))

from agent_memory.adapters.hashing_embedder import HashingEmbedder
from agent_memory.adapters.in_memory_store import InMemoryMemoryStore
from agent_memory.models import EmbeddedTurn, Turn
from agent_memory.prompts import RecallOnlyComposer
from agent_memory.recall import PeerSessionRecall, SimilarityRecall


async def _seed( store, session_id, texts, embedder, start ):
    for i, text in enumerate( texts ):
        store.add([ EmbeddedTurn(
            turn=Turn(
                session_id=session_id,
                speaker="USER" if i % 2 == 0 else "AGENT",
                message=text,
                created_at=start + timedelta( seconds=i ),
            ),
            vector=await embedder.embed( text ),
        )])


async def _fixture():
    store = InMemoryMemoryStore()
    embedder = HashingEmbedder( dimensions=256 )
    start = datetime( 2026, 1, 1, tzinfo=timezone.utc )
    await _seed( store, "mazda", [ "Import the Chase statement for June." ], embedder, start )
    await _seed( store, "toyota-1", [ "Remember the Walgreens receipt is medical." ],
                 embedder, start + timedelta( seconds=100 ))
    return store, embedder


async def test_peer_turns_are_added_and_labelled():
    store, embedder = await _fixture()
    recall = PeerSessionRecall(
        SimilarityRecall( store, limit=8 ), store,
        peer_session=lambda: "toyota-1", label="with Toyota",
    )
    turns = recall.recall( "mazda", await embedder.embed( "Walgreens receipt" ))

    messages = [ t.message for t in turns ]
    assert "Import the Chase statement for June." in messages, messages
    assert "[with Toyota] Remember the Walgreens receipt is medical." in messages, messages
    assert turns == sorted( turns, key=lambda t: t.created_at ), "expected oldest first"


async def test_peer_recall_follows_the_peer_session_as_it_rotates():
    store, embedder = await _fixture()
    current = [ "toyota-0" ]
    recall = PeerSessionRecall(
        SimilarityRecall( store, limit=8 ), store,
        peer_session=lambda: current[ 0 ], label="with Toyota",
    )
    query = await embedder.embed( "Walgreens" )
    assert not any( "[with Toyota]" in t.message for t in recall.recall( "mazda", query ))
    current[ 0 ] = "toyota-1"
    assert any( "[with Toyota]" in t.message for t in recall.recall( "mazda", query ))


async def test_same_or_empty_peer_returns_own_turns_only():
    store, embedder = await _fixture()
    query = await embedder.embed( "statement" )
    for peer in ( "", "mazda" ):
        recall = PeerSessionRecall(
            SimilarityRecall( store, limit=8 ), store,
            peer_session=lambda peer=peer: peer, label="with Toyota",
        )
        turns = recall.recall( "mazda", query )
        assert [ t.message for t in turns ] == [ "Import the Chase statement for June." ], turns


async def test_recall_only_composer_passes_text_through_when_nothing_recalled():
    composer = RecallOnlyComposer()
    assert composer.compose( [], "hello Mazda" ) == "hello Mazda"


async def test_recall_only_composer_puts_memory_before_the_message():
    store, embedder = await _fixture()
    turns = SimilarityRecall( store, limit=8 ).recall( "mazda", await embedder.embed( "x" ))
    prompt = RecallOnlyComposer().compose( turns, "what next?" )

    assert prompt.endswith( "what next?" ), prompt
    assert "Import the Chase statement for June." in prompt
    assert prompt.index( "[End of recalled memory]" ) < prompt.index( "what next?" )


if __name__ == "__main__":
    from tests.runner import main

    main( dict( globals()))
