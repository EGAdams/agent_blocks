"""LanguageModel over Groq's chat-completions API.

Reuses the `openai` SDK already installed for openai_embedder.py -- Groq's API
is OpenAI-compatible, so pointing its `base_url` at Groq is enough; no extra
dependency. This bypasses claude_agent_sdk.query() entirely for plain
conversation: no per-turn Claude Code CLI subprocess spawn, just one HTTP call,
the same reason voice_io.py talks to Groq directly for transcription.

Needs GROQ_API_KEY set (see receptionist_team/.env). Swap the model by passing
`model=` -- DEFAULT_MODEL is the only line to touch for a repo-wide default.
"""
import asyncio
import os

from openai import OpenAI

from ..models import AgentReply

DEFAULT_MODEL = "openai/gpt-oss-120b"
# "openai/gpt-oss-20b" tested.  use if the 120b is too slow.

DEFAULT_SYSTEM_PROMPT = (
    "You are Toyota, a friendly conversational assistant with long-term memory. "
    "Your replies will be spoken aloud, so write the way a person naturally "
    "talks, not the way a document is written. "
    "Use plain text only. Never use Markdown, asterisks, headings, bullet points, "
    "numbered lists, or other formatting meant for a screen. "
    "Answer the user's question directly using contractions and short, natural "
    "sentences. Usually give two to four sentences, and provide more detail "
    "only when the user asks for it. For broad questions, start with a brief overview "
    "instead of listing every detail. "
    "When mentioning agents, describe their roles in everyday language. "
    "For example, say 'our receipt scanner' instead of 'receipt_scanner', "
    "unless the user specifically asks for the exact technical name. "
    "Avoid stock phrases like 'Great question!' and don't end every reply with "
    "a question or a menu of options. Ask a follow-up only when it helps move "
    "the conversation forward. "
    "Use only the current message and recalled conversation as factual context. "
    "If information is missing, say so naturally rather than inventing it."
)


class GroqModelError( RuntimeError ):
    pass


class GroqLanguageModel:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        self._client = OpenAI(
            api_key=os.environ.get( "GROQ_API_KEY" ),
            base_url="https://api.groq.com/openai/v1",
        )
        self._model = model
        self._system_prompt = system_prompt

    async def complete( self, prompt: str ) -> AgentReply:
        try:
            response = await asyncio.to_thread(
                self._client.chat.completions.create,
                model=self._model,
                messages=[
                    { "role": "system", "content": self._system_prompt },
                    { "role": "user", "content": prompt },
                ],
            )
        except Exception as oops:
            raise GroqModelError( f"Groq API error: { oops }" ) from oops

        choice = response.choices[ 0 ]
        usage = response.usage

        return AgentReply(
            text=( choice.message.content or "" ).strip(),
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
        )
