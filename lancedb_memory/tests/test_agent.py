"""Full turn-loop tests. No network, no disk, no LanceDB.

Run:  .venv/bin/python tests/test_agent.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parent.parent ))

from agent_memory.adapters.hashing_embedder import HashingEmbedder
from agent_memory.adapters.in_memory_store import InMemoryMemoryStore
from agent_memory.agent import ConversationAgent
from agent_memory.models import MemoryQuery
from agent_memory.prompts import TemplatePromptComposer
from agent_memory.recall import SimilarityRecall, TokenBudgetedRecall
from tests.fakes import RecordingObserver, ScriptedModel, TickingClock


def build_agent( model, store=None, recall_limit=30, max_chars=12000, session_id="s1" ):
    store = store if store is not None else InMemoryMemoryStore()
    embedder = HashingEmbedder( dimensions=256 )
    recall = TokenBudgetedRecall(
        SimilarityRecall( store, limit=recall_limit ),
        max_chars=max_chars,
    )
    observer = RecordingObserver()
    agent = ConversationAgent(
        session_id=session_id,
        embedder=embedder,
        store=store,
        recall=recall,
        composer=TemplatePromptComposer(),
        model=model,
        clock=TickingClock(),
        observer=observer,
    )
    return agent, store, observer


async def test_turn_persists_both_speakers():
    model = ScriptedModel( [ "Hello there." ])
    agent, store, _ = build_agent( model )

    reply = await agent.handle_turn( "hi" )

    assert reply.text == "Hello there.", reply.text
    hits = store.search( MemoryQuery( vector=[ 0.0 ] * 256, session_id="s1", limit=10 ))
    speakers = sorted( hit.turn.speaker for hit in hits )
    assert speakers == [ "AGENT", "USER" ], speakers


async def test_first_turn_has_empty_context():
    model = ScriptedModel( [ "ack" ])
    agent, _, observer = build_agent( model )

    await agent.handle_turn( "my favourite colour is teal" )

    assert observer.calls[ 0 ][ 2 ] == [], "first turn must recall nothing"


async def test_memory_is_recalled_on_a_later_turn():
    model = ScriptedModel( [ "Noted.", "Your favourite colour is teal." ])
    agent, _, observer = build_agent( model )

    await agent.handle_turn( "my favourite colour is teal" )
    await agent.handle_turn( "what is my favourite colour?" )

    recalled = observer.calls[ 1 ][ 2 ]
    assert any( "teal" in turn.message for turn in recalled ), \
        f"expected the teal turn back, got { [ t.message for t in recalled ]}"
    assert "teal" in model.prompts[ 1 ], "recalled text must reach the prompt"


async def test_recalled_context_is_chronological():
    model = ScriptedModel( [ "a", "b", "c" ])
    agent, _, observer = build_agent( model )

    await agent.handle_turn( "alpha topic one" )
    await agent.handle_turn( "alpha topic two" )
    await agent.handle_turn( "alpha topic three" )

    recalled = observer.calls[ 2 ][ 2 ]
    times = [ turn.created_at for turn in recalled ]
    assert times == sorted( times ), times


async def test_sessions_are_isolated():
    store = InMemoryMemoryStore()
    agent_a, _, _ = build_agent( ScriptedModel( [ "ok" ]), store=store, session_id="alice" )
    agent_b, _, observer_b = build_agent( ScriptedModel( [ "ok" ]), store=store, session_id="bob" )

    await agent_a.handle_turn( "the secret passphrase is orangutan" )
    await agent_b.handle_turn( "what is the secret passphrase?" )

    recalled = observer_b.calls[ 0 ][ 2 ]
    assert all( "orangutan" not in turn.message for turn in recalled ), \
        "bob must not recall alice's turns"


async def test_token_budget_trims_oldest_first():
    model = ScriptedModel( [ "x" ] * 4 )
    agent, _, observer = build_agent( model, max_chars=60 )

    await agent.handle_turn( "shared keyword alpha " + "one" * 10 )
    await agent.handle_turn( "shared keyword alpha " + "two" * 10 )
    await agent.handle_turn( "shared keyword alpha " + "three" * 10 )

    recalled = observer.calls[ 2 ][ 2 ]
    total = sum( len( t.message ) + len( t.speaker ) + 2 for t in recalled )
    assert total <= 60, total


async def test_current_turn_is_not_its_own_context():
    model = ScriptedModel( [ "a", "b" ])
    agent, _, observer = build_agent( model )

    await agent.handle_turn( "unique marker zulu" )
    await agent.handle_turn( "unique marker zulu" )

    recalled = observer.calls[ 1 ][ 2 ]
    assert len( recalled ) == 2, f"turn 2 should see exactly turn 1's pair, got { len( recalled )}"


async def test_compact_starts_the_next_session_from_a_model_summary():
    model = ScriptedModel( [ "Noted.", "The user's favourite colour is teal.", "Teal." ])
    agent, _, observer = build_agent( model )

    await agent.handle_turn( "my favourite colour is teal" )
    result = await agent.compact()

    assert result.session_id == "s1--ctx1", result.session_id
    assert result.turns_before == 2, result.turns_before
    assert "my favourite colour is teal" in model.prompts[ 1 ], "the summarizer must see the turns"
    await agent.handle_turn( "what is my favourite colour?" )
    recalled = observer.calls[ 1 ][ 2 ]
    assert [ t.message for t in recalled ] == [
        "[Summary of our earlier conversation] The user's favourite colour is teal." ], \
        "after a compact, recall must see only the summary"


async def test_failed_compact_keeps_the_session():
    class _Broken:
        async def complete( self, prompt ):
            raise RuntimeError( "groq 429" )

    agent, _, _ = build_agent( ScriptedModel( [ "ok" ]))
    agent._summarizer = _Broken()
    await agent.handle_turn( "hello" )
    try:
        await agent.compact()
    except RuntimeError:
        pass
    else:
        raise AssertionError( "a failed summary must raise" )
    assert agent._session_id == "s1", "a failed compact must not rotate the session"


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
