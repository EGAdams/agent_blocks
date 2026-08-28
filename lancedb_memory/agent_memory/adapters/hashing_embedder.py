"""A dependency-free Embedder.

Anthropic serves no embeddings endpoint, so choosing the Claude SDK for
completion leaves the vector side open. This hashes tokens into a fixed-width
L2-normalised bag-of-words vector: purely lexical, no network, no API key,
deterministic across runs. It is enough to exercise and test the whole recall
pipeline today, and it is one line in the composition root to replace with a
semantic embedder later.
"""
import hashlib
import math
import re

_TOKEN = re.compile( r"[a-z0-9']+" )


class HashingEmbedder:
    def __init__( self, dimensions: int = 256 ) -> None:
        self._dimensions = dimensions

    @property
    def dimensions( self ) -> int:
        return self._dimensions

    async def embed( self, text: str ) -> list[ float ]:
        vector = [ 0.0 ] * self._dimensions
        for token in _TOKEN.findall( text.lower()):
            digest = hashlib.blake2b( token.encode( "utf-8" ), digest_size=8 ).digest()
            index = int.from_bytes( digest[ :4 ], "big" ) % self._dimensions
            sign = 1.0 if digest[ 4 ] & 1 else -1.0
            vector[ index ] += sign

        norm = math.sqrt( sum( value * value for value in vector ))
        if norm == 0.0:
            return vector
        return [ value / norm for value in vector ]
