"""Streaming must reduce latency without speaking commands or replaying partial replies."""
from types import SimpleNamespace as NS
from unittest.mock import patch

from agent_memory.adapters.chat_stream import complete_chat
from agent_memory.adapters.decorators import FallbackModel
from agent_memory.adapters.groq_model import GroqLanguageModel
from agent_memory.errors import PartialResponseError

from ..adapters.reply_stream import PublicReplyRelay
from ..service_models import TurnRequest
from ..turn_runner import _TurnRunner, _DelegationRelay
from agent_memory.config import Settings
from agent_memory.models import AgentReply


def chunk(text='', finish=None, usage=None):
    return NS(choices=[NS(delta=NS(content=text), finish_reason=finish)], usage=usage)


class Stream:
    def __init__(self, chunks):
        self.chunks, self.closed = chunks, False

    def __iter__(self):
        return iter(self.chunks)

    def close(self):
        self.closed = True


def client(stream):
    return NS(chat=NS(completions=NS(create=lambda **kw: stream)))


async def test_text_is_delivered_before_the_provider_finishes_and_usage_is_retained():
    received = []
    def chunks():
        yield chunk('Hello. ')
        assert received == ['Hello. '], 'first sentence was buffered until completion'
        yield chunk('Next sentence.')
        yield chunk(finish='stop', usage=NS(prompt_tokens=123, completion_tokens=9))
    stream = Stream(chunks())
    reply = complete_chat(client(stream), {}, received.append)
    assert reply.text == 'Hello. Next sentence.' and stream.closed
    assert reply.input_tokens == 123 and reply.output_tokens == 9


async def test_all_fragmented_control_prefixes_stay_private():
    for command in ['  ROUTE: live_books | how much did we spend',
                    'WORK: {"action":"start"}', 'OPEN_AGENT: mazda']:
        received, relay = [], PublicReplyRelay()
        relay.bind(received.append)
        for char in command:
            relay(char)
        assert received == [] and not relay.delivered


async def test_ordinary_words_and_late_callbacks_are_safe():
    received, relay = [], PublicReplyRelay()
    relay.bind(received.append)
    for char in 'Working on a greeting.':
        relay(char)
    assert ''.join(received) == 'Working on a greeting.' and relay.delivered
    relay.unbind()
    relay('stale text')
    assert ''.join(received) == 'Working on a greeting.'
    relay.bind(received.append)
    assert not relay.delivered


async def test_whitespace_tokens_are_preserved_through_blank_event_filters():
    received, relay = [], PublicReplyRelay()
    relay.bind(received.append)
    for token in ['Hello', ' ', 'there.', '\n', 'Next', ' ', 'sentence.']:
        relay(token)
    assert all(text.strip() for text in received)
    assert ''.join(received) == 'Hello there.\nNext sentence.'


async def test_a_truncated_control_line_is_never_returned_as_a_valid_reply():
    for finish in [None, 'length', 'content_filter']:
        received, relay = [], PublicReplyRelay()
        relay.bind(received.append)
        stream = Stream([chunk('ROUTE: live_books'), chunk(finish=finish)])
        try:
            complete_chat(client(stream), {}, relay)
        except PartialResponseError:
            pass
        else:
            raise AssertionError('an incomplete command must not execute')
        assert stream.closed and not received


async def test_a_failed_partial_reply_never_calls_the_backup_or_repeats_speech():
    received = []
    def chunks():
        yield chunk('Hello. ')
        raise RuntimeError('connection lost')
    stream = Stream(chunks())
    class Backup:
        async def complete(self, prompt):
            raise AssertionError('a partially spoken reply must not fall back')
    with patch('agent_memory.adapters.groq_model.OpenAI', return_value=client(stream)):
        primary = GroqLanguageModel(on_delta=received.append)
        try:
            await FallbackModel(primary, [Backup()]).complete('hello')
        except PartialResponseError:
            pass
        else:
            raise AssertionError('stream failure must reach the caller')
    assert received == ['Hello. '] and stream.closed


async def test_turn_runner_marks_the_final_snapshot_to_prevent_duplicate_speech():
    relay = PublicReplyRelay()
    class Agent:
        async def handle_turn(self, text, request_id=None):
            relay('Hello. ')
            relay('All done.')
            return AgentReply(text='Hello. All done.')
    runner = _TurnRunner(Agent(), NS(total_cost_usd=0), Settings(), 'unused',
                         _DelegationRelay(), reply_relay=relay)
    records = list(runner.stream(TurnRequest(text='hello')))
    assert [r.kind for r in records] == ['delta', 'delta', 'reply', 'result']
    assert records[2].streamed
    assert ''.join(r.text for r in records if r.kind == 'delta') == records[2].text


if __name__ == '__main__':
    from tests.runner import main
    main(dict(globals()))
