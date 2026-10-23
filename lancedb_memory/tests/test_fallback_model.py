"""FallbackModel -- one provider's wall must not be the end of the turn.

Offline: no provider is contacted. The point of the class is what it does with
an exception, so fakes that raise are the whole subject.
"""
import os
import sys
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parent.parent ))

from agent_memory.adapters.decorators import FallbackModel
from agent_memory.errors import ModelUnavailable
from agent_memory.models import AgentReply


class _Answers:
    def __init__( self, text: str ) -> None:
        self.text = text
        self.calls = 0

    async def complete( self, prompt: str ) -> AgentReply:
        self.calls += 1
        return AgentReply( text=self.text )


class _Raises:
    def __init__( self, error: Exception ) -> None:
        self.error = error
        self.calls = 0

    async def complete( self, prompt: str ) -> AgentReply:
        self.calls += 1
        raise self.error


async def test_the_primary_answering_costs_nothing_downstream():
    groq = _Answers( "from groq" )
    openai = _Answers( "from openai" )
    reply = await FallbackModel( groq, [ openai ]).complete( "hello" )

    assert reply.text == "from groq"
    # The paid backend must not be touched on a good day.
    assert openai.calls == 0


async def test_a_spent_daily_budget_falls_through_to_the_backup():
    groq = _Raises( ModelUnavailable( "daily budget is used up", retry_after=1300 ))
    openai = _Answers( "from openai" )
    reply = await FallbackModel( groq, [ openai ]).complete( "hello" )

    assert reply.text == "from openai"
    assert groq.calls == 1 and openai.calls == 1


async def test_retryingmodel_giving_up_also_falls_through():
    # RetryingModel converts an exhausted backoff loop into a plain
    # RuntimeError, and both provider error classes subclass it. A dead
    # provider has to fail over too, not just a rate-limited one.
    groq = _Raises( RuntimeError( "Model failed after 5 attempts" ))
    openai = _Answers( "from openai" )
    reply = await FallbackModel( groq, [ openai ]).complete( "hello" )

    assert reply.text == "from openai"


async def test_a_bug_in_our_own_code_is_not_quietly_paid_for_twice():
    # TypeError means we built the prompt wrong. Falling back would hide it
    # and buy a second API call per turn to produce the same crash.
    groq = _Raises( TypeError( "prompt must be str" ))
    openai = _Answers( "from openai" )
    try:
        await FallbackModel( groq, [ openai ]).complete( "hello" )
    except TypeError:
        pass
    else:
        raise AssertionError( "a programming error must surface, not fail over" )
    assert openai.calls == 0


async def test_no_backups_behaves_exactly_like_the_bare_model():
    # A machine with no OPENAI_API_KEY gets this, and must not be worse off
    # than before the fallback existed: the provider's own message reaches
    # the user unchanged.
    wall = ModelUnavailable( "daily budget is used up" )
    try:
        await FallbackModel( _Raises( wall ), []).complete( "hello" )
    except ModelUnavailable as caught:
        assert caught is wall
    else:
        raise AssertionError( "with no backup the refusal has to reach the user" )


async def test_the_last_failure_reaches_the_user_when_everything_is_down():
    # Not the first one. If OpenAI is the thing that is broken, "Groq is rate
    # limited" would send the next hour down the wrong path.
    last = ModelUnavailable( "this OpenAI account is out of credit" )
    try:
        await FallbackModel(
            _Raises( ModelUnavailable( "groq daily budget is used up" )), [ _Raises( last )]
        ).complete( "hello" )
    except ModelUnavailable as caught:
        assert "out of credit" in str( caught )
    else:
        raise AssertionError( "an all-down chain must still raise" )


async def test_it_tries_every_hop_in_order():
    groq = _Raises( ModelUnavailable( "spent" ))
    openai = _Raises( RuntimeError( "down" ))
    claude = _Answers( "from claude" )
    reply = await FallbackModel( groq, [ openai, claude ]).complete( "hello" )

    assert reply.text == "from claude"
    assert ( groq.calls, openai.calls, claude.calls ) == ( 1, 1, 1 )


async def test_the_scoped_key_name_is_checked_before_the_ambient_one():
    # The whole point of RECEPTIONIST_OPENAI_API_KEY: openai_embedder.py reads
    # only OPENAI_API_KEY and builds a bare OpenAI(), so a key stored under the
    # scoped name cannot be spent on a paid embedding run nobody asked for.
    from agent_memory.adapters import openai_model

    assert openai_model.API_KEY_VARS[ 0 ] == "RECEPTIONIST_OPENAI_API_KEY"
    saved = { name: os.environ.get( name ) for name in openai_model.API_KEY_VARS }
    try:
        for name in openai_model.API_KEY_VARS:
            os.environ.pop( name, None )
        assert openai_model.resolve_api_key() is None

        os.environ[ "OPENAI_API_KEY" ] = "ambient"
        assert openai_model.resolve_api_key() == "ambient"

        os.environ[ "RECEPTIONIST_OPENAI_API_KEY" ] = "scoped"
        assert openai_model.resolve_api_key() == "scoped"
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop( name, None )
            else:
                os.environ[ name ] = value


async def test_a_missing_key_names_both_variables_rather_than_guessing():
    from agent_memory.adapters.openai_model import OpenAIModelError, OpenAILanguageModel

    try:
        OpenAILanguageModel( api_key="" )
    except OpenAIModelError as oops:
        assert "RECEPTIONIST_OPENAI_API_KEY" in str( oops )
    else:
        raise AssertionError( "no key must not silently build a client" )


async def test_the_reasoning_floor_is_read_off_the_model_s_own_refusal():
    # gpt-5 spells the floor "minimal" and rejects "none"; gpt-5.4 does the
    # reverse, and both answer 400. A model-name table would go stale on the
    # next release, so the supported list is parsed out of the error instead.
    from agent_memory.adapters.openai_model import _cheapest_supported_effort

    gpt54 = (
        "Unsupported value: 'reasoning_effort' does not support 'minimal' with "
        "this model. Supported values are: 'none', 'low', 'medium', 'high', "
        "and 'xhigh'."
    )
    assert _cheapest_supported_effort( gpt54 ) == "none"

    gpt5 = (
        "Unsupported value: 'reasoning_effort' does not support 'none' with "
        "this model. Supported values are: 'minimal', 'low', 'medium', 'high'."
    )
    assert _cheapest_supported_effort( gpt5 ) == "minimal"

    # Cheapest offered, not merely the first listed.
    assert _cheapest_supported_effort(
        "'reasoning_effort' ... Supported values are: 'high', 'low', 'medium'."
    ) == "low"

    # Nothing to do with reasoning_effort, or nothing recognisable offered:
    # the caller drops the parameter rather than guessing another 400.
    assert _cheapest_supported_effort( "Error code: 429 rate_limit_exceeded" ) is None
    assert _cheapest_supported_effort(
        "'reasoning_effort' ... Supported values are: 'turbo', 'ludicrous'."
    ) is None


async def test_a_malformed_request_is_not_retried_five_times():
    # RetryingModel sits on anything that is not ModelUnavailable. A 400 is
    # never transient, and five attempts at one turned a precise message
    # ("does not support 'minimal' with this model") into "Model failed after
    # 5 attempts" -- which is what hid a one-word config bug.
    from agent_memory.adapters.openai_model import _refusal

    four_hundred = (
        "Error code: 400 - {'error': {'message': \"Unsupported value: "
        "'reasoning_effort'...\", 'type': 'invalid_request_error'}}"
    )
    refusal = _refusal( four_hundred )
    assert refusal is not None
    assert "malformed" in str( refusal )
    # And it must carry OpenAI's own words through, not replace them.
    assert "reasoning_effort" in str( refusal )


async def test_out_of_credit_is_not_reported_as_a_rate_limit():
    # Waiting fixes a rate limit and never fixes an empty balance. Saying the
    # wrong one cost an afternoon already, on the Groq side.
    from agent_memory.adapters.openai_model import _refusal

    assert "out of credit" in str( _refusal( "Error code: 429 - insufficient_quota" ))
    assert "rate limiting" in str( _refusal( "Error code: 429 - rate_limit_exceeded" ))


if __name__ == "__main__":
    from tests.runner import main

    main( dict( globals()))
