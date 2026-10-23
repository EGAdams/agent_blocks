"""LanguageModel over OpenAI's chat-completions API.

The paid sibling of groq_model.py. Both speak the same wire protocol through
the same `openai` SDK -- the difference is only that Groq needs a base_url
override and this one doesn't -- so the two files look alike on purpose rather
than sharing a base class: a provider's quirks belong next to that provider,
and the quirks are what actually differ (see reasoning_effort below).

Unlike Groq there is no free daily allowance here, which is the whole point
of it: this is what a receptionist falls back to when Groq's
200000-tokens-per-day budget for her model is spent. See
decorators.FallbackModel.

The key is resolved by resolve_api_key() and handed to the client EXPLICITLY,
never left to the SDK's ambient OPENAI_API_KEY lookup. That is deliberate.
openai_embedder.py constructs a bare OpenAI(), so an OPENAI_API_KEY sitting in
the environment would silently bill a paid embedding run that nobody asked
for. RECEPTIONIST_OPENAI_API_KEY is checked first precisely so the key can
exist for the receptionists' fallback replies and for nothing else on the
machine -- the embedder cannot see a variable it does not read.
"""
import asyncio
import os
import re

from openai import OpenAI

from ..errors import ModelUnavailable
from ..models import AgentReply

DEFAULT_MODEL = "gpt-5-mini"
# gpt-5-mini and not gpt-5.4-mini: three times cheaper ($0.25/$2.00 against
# $0.75/$4.50) for the same job, and measured at 1.25s a turn against 5.4-mini's
# 0.53s warm -- a difference nobody notices behind a 2s Groq path that only
# hands over on overflow anyway.
#
# A mini, not a nano. A receptionist's hardest instruction is "reply with a
# bare ROUTE: line and nothing else", and the one time this repo ran a model of
# nano's weight (Groq's gpt-oss-20b) it answered status questions from recalled
# chat instead of routing them -- which is why adapters/status_route.py had to
# exist at all. Not worth re-fighting to save a few dollars a year.
#
# Prices per million tokens as of 2026-10, in/out: gpt-5.4-mini $0.75/$4.50,
# gpt-5-mini $0.25/$2.00, gpt-5.4-nano $0.20/$1.25, gpt-5 $1.25/$10.00. Only
# the overflow past Groq's free daily budget is ever billed here, so the figure
# that matters is a fraction of a full month. Pass model= to override.

# GPT-5 models think before answering, and those hidden reasoning tokens are
# billed at the OUTPUT rate and paid for in latency. A receptionist writing
# three conversational sentences has nothing to reason about, so asking for
# the floor is both cheaper and faster. This is the one setting that makes the
# difference between a few dollars a month and three times that.
#
# The floor is spelled differently across model generations -- gpt-5 calls it
# "minimal" and rejects "none"; gpt-5.4 calls it "none" and rejects "minimal",
# answering 400 either way. Rather than hardcode a model-name table that the
# next release invalidates, the request is sent once and, if the value is
# refused, the supported list is read straight out of the error and the
# cheapest one retried. _EFFORT_FLOOR is that preference order.
DEFAULT_REASONING_EFFORT = "minimal"
_EFFORT_FLOOR = ( "none", "minimal", "low" )
_SUPPORTED_EFFORTS_RE = re.compile( r"Supported values are:\s*([^.]+)" )


def _cheapest_supported_effort( message: str ) -> str | None:
    """The least thinking this model will agree to, read off its own 400.

    None when the error is not about reasoning_effort at all, or names nothing
    we recognise -- in which case the caller drops the parameter instead of
    guessing, and lets the model use its own default.
    """
    if "reasoning_effort" not in message:
        return None
    found = _SUPPORTED_EFFORTS_RE.search( message )
    if not found:
        return None
    offered = set( re.findall( r"'([a-z]+)'", found.group( 1 )))
    for effort in _EFFORT_FLOOR:
        if effort in offered:
            return effort
    return None

# Checked in order. The scoped name comes first so a key bought for her
# fallback replies stays unusable by anything else here -- openai_embedder.py
# reads only the ambient OPENAI_API_KEY, so a key stored under the scoped name
# simply does not exist as far as it is concerned. Plain OPENAI_API_KEY still
# works for a machine that already has one and does not care.
API_KEY_VARS = ( "RECEPTIONIST_OPENAI_API_KEY", "OPENAI_API_KEY" )


def resolve_api_key() -> str | None:
    """The first of API_KEY_VARS that is actually set, or None."""
    for name in API_KEY_VARS:
        value = os.environ.get( name )
        if value:
            return value
    return None


class OpenAIModelError( RuntimeError ):
    pass


def _refusal( message: str ) -> ModelUnavailable | None:
    """A failure that waiting will not fix, told to the user in those words.

    Billing is the one that matters here. An exhausted account answers 429
    like a rate limit does, but no amount of backoff will add credit -- and a
    receptionist that says "I'm rate limited" when the real answer is "the
    card needs attention" sends the next hour down the wrong path. That
    mistake has already been made once in this repo, with Groq's daily cap.
    """
    lowered = message.lower()
    if "insufficient_quota" in lowered or "billing" in lowered:
        return ModelUnavailable(
            "This OpenAI account is out of credit, so no amount of waiting "
            "will clear it -- the billing page is the fix."
        )
    if "invalid_api_key" in lowered or "incorrect api key" in lowered:
        return ModelUnavailable( "That OpenAI key is not one OpenAI recognises." )
    if "rate_limit" in lowered or "429" in message:
        return ModelUnavailable( "OpenAI is rate limiting this model." )
    if "invalid_request_error" in lowered or "error code: 400" in lowered:
        # A malformed request is not a transient failure, and RetryingModel
        # must not sit on it: five attempts at a 400 turned a precise message
        # ("reasoning_effort does not support 'minimal' with this model") into
        # "Model failed after 5 attempts", which is how one bad default cost
        # an afternoon. Say what OpenAI said.
        return ModelUnavailable( f"OpenAI rejected the request as malformed: { message }" )
    return None


class OpenAILanguageModel:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        system_prompt: str = "",
        reasoning_effort: str | None = DEFAULT_REASONING_EFFORT,
        api_key: str | None = None,
    ) -> None:
        key = api_key or resolve_api_key()
        if not key:
            # Caught at construction rather than on the first turn, so a
            # missing key is a startup error and not a surprise halfway
            # through a conversation.
            raise OpenAIModelError(
                f"No OpenAI key found in { ' or '.join( API_KEY_VARS )}; "
                "OpenAILanguageModel has nothing to talk to."
            )
        # Explicit, not ambient. See the module docstring.
        self._client = OpenAI( api_key=key )
        self._model = model
        self._system_prompt = system_prompt
        self._reasoning_effort = reasoning_effort

    async def complete( self, prompt: str ) -> AgentReply:
        request = {
            "model": self._model,
            "messages": [
                { "role": "system", "content": self._system_prompt },
                { "role": "user", "content": prompt },
            ],
        }
        # Only the reasoning models accept it, and an older one 400s on an
        # unknown field rather than ignoring it, so this stays opt-out.
        if self._reasoning_effort:
            request[ "reasoning_effort" ] = self._reasoning_effort

        try:
            response = await self._create( request )
        except Exception as oops:
            retried = await self._retry_without_refused_effort( request, str( oops ))
            if retried is not None:
                response = retried
            else:
                stop = _refusal( str( oops ))
                if stop is not None:
                    raise stop from oops
                raise OpenAIModelError( f"OpenAI API error: { oops }" ) from oops

        choice = response.choices[ 0 ]
        usage = response.usage

        return AgentReply(
            text=( choice.message.content or "" ).strip(),
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
        )

    async def _create( self, request: dict ):
        return await asyncio.to_thread(
            self._client.chat.completions.create, **request
        )

    async def _retry_without_refused_effort( self, request: dict, message: str ):
        """One retry, only for a reasoning_effort this model spells differently.

        Returns None for every other failure, so the caller's normal error
        handling runs untouched. The corrected value is remembered on the
        instance: a warm sidecar lives for days, and paying a wasted 400 on
        every turn to rediscover the same answer would be silly.
        """
        if "reasoning_effort" not in request:
            return None
        better = _cheapest_supported_effort( message )
        if better == request.get( "reasoning_effort" ):
            return None

        if better is None:
            # Nothing recognisable offered -- let the model use its own default
            # rather than guess a value and 400 again.
            request.pop( "reasoning_effort", None )
            self._reasoning_effort = None
            note = "dropping reasoning_effort"
        else:
            request[ "reasoning_effort" ] = better
            self._reasoning_effort = better
            note = f"reasoning_effort={ better }"
        print(
            f"[{ self._model } refused reasoning_effort; retrying with { note }]",
            flush=True,
        )
        try:
            return await self._create( request )
        except Exception:
            return None
