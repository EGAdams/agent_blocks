"""A limit that won't clear must not be retried into.

Mazda went quiet for sixteen seconds a turn and then said "Model failed after
5 attempts", when Groq had already told us plainly that the daily token
allowance was spent and would reset in twenty-three minutes. These pin the
difference between a wait worth sitting through and one that isn't.
"""
import sys
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parent.parent ))

from agent_memory.adapters.decorators import RetryingModel
from agent_memory.adapters.groq_model import _rate_limit_error
from agent_memory.errors import ModelUnavailable
from agent_memory.models import AgentReply

_TPD = (
    "Groq API error: Error code: 429 - {'error': {'message': 'Rate limit reached "
    "for model `openai/gpt-oss-120b` in organization `org_01k6` service tier "
    "`on_demand` on tokens per day (TPD): Limit 200000, Used 198318, Requested "
    "4945. Please try again in 23m29.616s.', 'type': 'tokens', 'code': "
    "'rate_limit_exceeded'}}"
)
_PER_MINUTE = (
    "Error code: 429 - {'error': {'message': 'Rate limit reached on tokens per "
    "minute (TPM). Please try again in 3.2s.', 'code': 'rate_limit_exceeded'}}"
)


class _AlwaysRateLimited:
    def __init__( self, error: Exception ) -> None:
        self._error = error
        self.calls = 0

    async def complete( self, prompt: str ) -> AgentReply:
        self.calls += 1
        raise self._error


async def test_a_daily_limit_says_when_it_clears_instead_of_retrying():
    stop = _rate_limit_error( _TPD )
    assert stop is not None
    assert "daily Groq token allowance is used up" in str( stop )
    # 23m29s, so the user hears "about 23 minute(s)" rather than a bare failure.
    assert "23 minute" in str( stop )
    assert stop.retry_after is not None and 1400 < stop.retry_after < 1420


async def test_a_wait_of_seconds_is_still_worth_retrying():
    # A per-minute limit really does clear while we sleep; that one belongs in
    # the retry loop, which is why this is a wait threshold and not "all 429s".
    assert _rate_limit_error( _PER_MINUTE ) is None


async def test_an_ordinary_failure_is_not_mistaken_for_a_limit():
    assert _rate_limit_error( "Groq API error: Connection reset by peer" ) is None


async def test_the_retry_loop_gives_up_at_once_on_an_unclearable_limit():
    inner = _AlwaysRateLimited( ModelUnavailable( "spent", retry_after=1400.0 ))
    try:
        await RetryingModel( inner, max_retry=5, backoff=1.0 ).complete( "hi" )
    except ModelUnavailable as oops:
        assert str( oops ) == "spent"
    else:
        raise AssertionError( "an unclearable limit must not be swallowed" )
    # One attempt, not five -- that is the fifteen seconds the user got back.
    assert inner.calls == 1


async def test_the_retry_loop_still_retries_everything_else():
    inner = _AlwaysRateLimited( RuntimeError( "connection reset" ))
    try:
        await RetryingModel( inner, max_retry=3, backoff=0.0 ).complete( "hi" )
    except RuntimeError as oops:
        assert "after 3 attempts" in str( oops )
    assert inner.calls == 3


if __name__ == "__main__":
    from tests.runner import main

    main( dict( globals()))
