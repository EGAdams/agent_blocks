"""Embedder over a local sentence-transformers model.

Runs entirely on your own machine (CPU is fine) -- no API key, no per-call
cost, but it produces MEANING-based vectors instead of HashingEmbedder's
shared-vocabulary ones: "the dog ran" and "the puppy sprinted" land close
together here, where HashingEmbedder sees no shared tokens at all.
"""
from sentence_transformers import SentenceTransformer

DEFAULT_MODEL_NAME = "all-MiniLM-L6-v2"


class SentenceTransformerEmbedder:
    def __init__( self, model_name: str = DEFAULT_MODEL_NAME ) -> None:
        self._model = SentenceTransformer( model_name )
        self._dimensions = self._model.get_embedding_dimension()

    @property
    def dimensions( self ) -> int:
        return self._dimensions

    async def embed( self, text: str ) -> list[ float ]:
        # encode() is synchronous CPU work; there is nothing to await inside it.
        vector = self._model.encode( text, normalize_embeddings=True )
        return vector.tolist()
