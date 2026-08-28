"""Factory Method: turns one profile name into a matched (embedder, table) pair.

A vector's width is baked into it -- a 256-number fingerprint and a 1536-number
fingerprint are not comparable, and LanceDBMemoryStore raises rather than write
mixed-width rows into one table. So "which embedder" and "which table" are
never a free choice independently; this is the one place that pairing is
decided, instead of every caller re-deriving it.

Open/Closed in practice: PROFILE_BUILDERS is a registry, not an if/elif chain.
Adding a fourth profile means adding one entry here -- nothing in main.py, or
in this module's existing entries, has to change.
"""
from typing import Callable, NamedTuple

from ..ports import Embedder
from .decorators import CachingEmbedder
from .hashing_embedder import HashingEmbedder


class EmbedderBundle( NamedTuple ):
    """Not a Turn/EmbeddedTurn-style data contract -- it carries a live,
    non-serialisable Embedder instance, so it stays a plain NamedTuple rather
    than a pydantic BaseModel. Internal to the composition root; nothing
    outside adapters/ or main.py ever sees this type.
    """
    embedder: Embedder
    table_name: str


def _build_hash() -> EmbedderBundle:
    return EmbedderBundle(
        embedder=HashingEmbedder( dimensions=256 ),
        table_name="conversation_memory_hash256",
    )


def _build_sentence() -> EmbedderBundle:
    from .sentence_embedder import SentenceTransformerEmbedder  # heavy import: torch

    embedder = SentenceTransformerEmbedder()
    return EmbedderBundle(
        embedder=embedder,
        table_name=f"conversation_memory_sentence{ embedder.dimensions }",
    )


def _build_openai() -> EmbedderBundle:
    from .openai_embedder import OpenAIEmbedder  # needs OPENAI_API_KEY set

    embedder = OpenAIEmbedder()
    return EmbedderBundle(
        embedder=embedder,
        table_name=f"conversation_memory_openai{ embedder.dimensions }",
    )


PROFILE_BUILDERS: dict[ str, Callable[ [], EmbedderBundle ]] = {
    "hash": _build_hash,
    "sentence": _build_sentence,
    "openai": _build_openai,
}


def build_embedder( profile: str ) -> EmbedderBundle:
    """The Factory Method itself: one call site, hides which concrete class
    gets built. Callers depend on EmbedderBundle, never on HashingEmbedder,
    SentenceTransformerEmbedder, or OpenAIEmbedder directly.
    """
    try:
        builder = PROFILE_BUILDERS[ profile ]
    except KeyError:
        raise ValueError(
            f"Unknown embedding_profile '{ profile }'. "
            f"Choose one of: { ', '.join( sorted( PROFILE_BUILDERS ))}"
        ) from None

    bundle = builder()
    return EmbedderBundle(
        embedder=CachingEmbedder( bundle.embedder ),
        table_name=bundle.table_name,
    )
