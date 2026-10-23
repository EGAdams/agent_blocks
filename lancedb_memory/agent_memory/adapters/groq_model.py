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
import re

from openai import OpenAI

from ..errors import ModelUnavailable
from ..models import AgentReply

DEFAULT_MODEL = "openai/gpt-oss-120b"
# Both gpt-oss sizes draw on one 8000-tokens-per-minute bucket, so the smaller
# model buys no headroom against *that* limit and there is no reason not to run
# the better one. What governs a per-minute 429 is the size of a single turn --
# see receptionist_team/profiles.py's recall_max_chars.
#
# The 200000-tokens-per-DAY cap is the other story: it is counted per model, so
# the two sizes are two separate daily budgets and switching is a real (if
# temporary) way out of an exhausted one.  Pass model= to override.

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


# Groq puts the wait in prose: "Please try again in 23m29.616s."
_RETRY_AFTER_RE = re.compile( r"try again in (?:(\d+)m)?([\d.]+)s" )
# How long a wait is still worth sitting through inside one turn. Past that,
# the honest answer is to tell the user when it clears.
WORTH_WAITING_SECONDS = 10.0


def _retry_after_seconds( message: str ) -> float | None:
    found = _RETRY_AFTER_RE.search( message )
    if not found:
        return None
    minutes, seconds = found.groups()
    return float( minutes or 0 ) * 60 + float( seconds )


def _rate_limit_error( message: str ) -> ModelUnavailable | None:
    """A 429 we should stop on, rather than retry into the same wall.

    A per-minute limit can clear in a second or two, so that one still goes
    through the retry loop. A daily token limit cannot, and the provider says
    so -- we pass that sentence on instead of burning the backoff budget.

    The daily cap is 200000 tokens per model per day, counted separately for
    each model (the 8000-per-minute bucket is the shared one), in a rolling
    window rather than a midnight reset. So the wait Groq quotes on a daily
    429 is when enough tokens age out, not when the day ends -- saying "the
    daily allowance is used up, and it clears in about 22 minutes" read as a
    contradiction and sent one diagnosis down the wrong path entirely.
    """
    if "rate_limit_exceeded" not in message and "429" not in message:
        return None
    wait = _retry_after_seconds( message )
    if wait is not None and wait <= WORTH_WAITING_SECONDS:
        return None
    daily = "per day" in message or "TPD" in message
    if daily:
        detail = "This model's daily Groq token budget is used up"
    else:
        detail = "Groq is rate limiting this model"
    # The common case here is the per-minute token ceiling, which clears in
    # well under a minute -- rounding that to minutes used to say "about 0
    # minute(s)". Say seconds while it is still seconds.
    # A daily cap frees up gradually as old usage ages out, so the quoted wait
    # is "enough room again", not "the allowance was restored".
    freed = "there should be room again" if daily else "it clears"
    if not wait:
        when = ""
    elif wait < 60:
        when = f", and { freed } in about { round( wait ) } second(s)"
    else:
        when = f", and { freed } in about { round( wait / 60 ) } minute(s)"
    return ModelUnavailable( f"{ detail }{ when }.", retry_after=wait )


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
            stop = _rate_limit_error( str( oops ))
            if stop is not None:
                raise stop from oops
            raise GroqModelError( f"Groq API error: { oops }" ) from oops

        choice = response.choices[ 0 ]
        usage = response.usage

        return AgentReply(
            text=( choice.message.content or "" ).strip(),
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
        )
