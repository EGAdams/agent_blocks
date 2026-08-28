"""Tests for the embedding-profile Factory Method. No network, no torch import
-- only exercises the "hash" branch and the registry's error path, so this
stays fast even before sentence-transformers/openai are installed.

Run:  .venv/bin/python tests/test_embedding_profiles.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parent.parent ))

from agent_memory.adapters.embedding_profiles import PROFILE_BUILDERS, build_embedder


async def main() -> int:
    failures = 0

    embedder, table_name = build_embedder( "hash" )
    if embedder.dimensions == 256 and table_name == "conversation_memory_hash256":
        print( "PASS  hash profile resolves to a matched embedder/table pair" )
    else:
        print( "FAIL  hash profile resolves to a matched embedder/table pair" )
        failures += 1

    vector = await embedder.embed( "hello world" )
    if len( vector ) == embedder.dimensions:
        print( "PASS  built embedder actually produces vectors of its own width" )
    else:
        print( "FAIL  built embedder actually produces vectors of its own width" )
        failures += 1

    # Same profile, twice -- caching is applied inside the factory, not left
    # to every call site to remember.
    same_text_vector = await embedder.embed( "hello world" )
    if same_text_vector == vector:
        print( "PASS  factory wraps the embedder in caching" )
    else:
        print( "FAIL  factory wraps the embedder in caching" )
        failures += 1

    try:
        build_embedder( "not-a-real-profile" )
        print( "FAIL  unknown profile should raise" )
        failures += 1
    except ValueError:
        print( "PASS  unknown profile fails loud" )

    if { "hash", "sentence", "openai" }.issubset( PROFILE_BUILDERS.keys()):
        print( "PASS  registry lists all three profiles" )
    else:
        print( "FAIL  registry lists all three profiles" )
        failures += 1

    print( f"\n{ 5 - failures }/5 passed" )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit( asyncio.run( main()))
