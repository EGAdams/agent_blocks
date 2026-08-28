"""Embedder over the OpenAI embeddings API.

The only one of the three that costs money per call and needs OPENAI_API_KEY
set -- in exchange it is the strongest semantic model of the three. Reserve it
for the "make this genuinely useful" table, not for routine testing.
"""
import asyncio

from openai import OpenAI

DEFAULT_MODEL_NAME = "text-embedding-3-small"
DEFAULT_DIMENSIONS = 1536


class OpenAIEmbedder:
    def __init__(
        self,
        model_name: str = DEFAULT_MODEL_NAME,
        dimensions: int = DEFAULT_DIMENSIONS,
    ) -> None:
        self._client = OpenAI()
        self._model_name = model_name
        self._dimensions = dimensions

    @property
    def dimensions( self ) -> int:
        return self._dimensions

    async def embed( self, text: str ) -> list[ float ]:
        # The OpenAI SDK's client here is synchronous; run it off the event
        # loop thread so it behaves like the other (already-async) embedders.
        response = await asyncio.to_thread(
            self._client.embeddings.create,
            model=self._model_name,
            input=text,
            dimensions=self._dimensions,
        )
        return response.data[ 0 ].embedding
