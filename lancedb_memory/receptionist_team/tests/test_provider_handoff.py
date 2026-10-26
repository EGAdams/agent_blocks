"""Exercise the composed provider chain without network, retries, or credentials."""
from types import SimpleNamespace
from unittest.mock import patch

from agent_memory.adapters import groq_model, openai_model
from agent_memory.config import Settings
from agent_memory.errors import ModelUnavailable

from ..main import build_chat_model
from ..profiles import TOYOTA


class FakeClient:
    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.requests = answer, error, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.answer))], usage=None)


async def test_every_groq_token_limit_immediately_reaches_the_configured_backup():
    for detail in ['tokens per minute (TPM). Please try again in 3.2s.',
                   'tokens per day (TPD). Please try again in 23m29s.']:
        primary = FakeClient(error=RuntimeError('Error code: 429 rate_limit_exceeded ' + detail))
        backup = FakeClient(answer='OPENAI_HANDOFF_OK')
        with patch.object(groq_model, 'OpenAI', return_value=primary) as groq_factory, \
             patch.object(openai_model, 'OpenAI', return_value=backup) as backup_factory, \
             patch('receptionist_team.main.resolve_fallback_key', return_value='test-only'):
            model = build_chat_model(TOYOTA, Settings(model=TOYOTA.chat_model), None)
            reply = await model.complete('diagnostic')
        assert reply.text == 'OPENAI_HANDOFF_OK'
        assert len(primary.requests) == len(backup.requests) == 1
        assert groq_factory.call_args.kwargs.get('max_retries') == 0
        assert backup_factory.call_args.kwargs.get('max_retries') == 0
        assert groq_factory.call_args.kwargs['timeout'] <= 10
        assert backup_factory.call_args.kwargs['timeout'] <= 30


async def test_a_short_limit_is_a_handoff_signal_even_without_a_backup():
    primary = FakeClient(error=RuntimeError('429 rate_limit_exceeded Please try again in 0.4s.'))
    with patch.object(groq_model, 'OpenAI', return_value=primary):
        model = groq_model.GroqLanguageModel()
        try:
            await model.complete('hello')
        except ModelUnavailable as error:
            assert error.retry_after == 0.4
        else:
            raise AssertionError('every token limit must escape the retry loop')


async def test_success_never_spends_a_backup_request():
    primary, backup = FakeClient(answer='GROQ_OK'), FakeClient(answer='must not run')
    with patch.object(groq_model, 'OpenAI', return_value=primary), \
         patch.object(openai_model, 'OpenAI', return_value=backup), \
         patch('receptionist_team.main.resolve_fallback_key', return_value='test-only'):
        reply = await build_chat_model(TOYOTA, Settings(model=TOYOTA.chat_model), None).complete('hello')
    assert reply.text == 'GROQ_OK'
    assert len(primary.requests) == 1 and not backup.requests


async def test_openai_quota_failure_is_not_retried_as_a_reasoning_setting_error():
    primary = FakeClient(error=RuntimeError('429 rate_limit_exceeded'))
    backup = FakeClient(error=RuntimeError('Error code: 429 insufficient_quota'))
    with patch.object(groq_model, 'OpenAI', return_value=primary), \
         patch.object(openai_model, 'OpenAI', return_value=backup), \
         patch('receptionist_team.main.resolve_fallback_key', return_value='test-only'):
        try:
            await build_chat_model(TOYOTA, Settings(model=TOYOTA.chat_model), None).complete('hello')
        except ModelUnavailable as error:
            assert 'out of credit' in str(error)
        else:
            raise AssertionError('the actual fallback failure must reach the caller')
    assert len(backup.requests) == 1


async def test_reasoning_correction_reports_the_actual_second_failure():
    errors = iter([
        RuntimeError("Error code: 400 Unsupported value 'reasoning_effort'. Supported values are: 'none', 'low'."),
        RuntimeError('Error code: 429 insufficient_quota'),
    ])
    backup = FakeClient()
    def create(**request):
        backup.requests.append(request)
        raise next(errors)
    backup.chat.completions.create = create
    with patch.object(openai_model, 'OpenAI', return_value=backup):
        model = openai_model.OpenAILanguageModel(api_key='test-only')
        try:
            await model.complete('hello')
        except ModelUnavailable as error:
            assert 'out of credit' in str(error)
        else:
            raise AssertionError('a corrected request must report its own failure')
    assert len(backup.requests) == 2
    assert backup.requests[1]['reasoning_effort'] == 'none'


if __name__ == '__main__':
    from tests.runner import main
    main(dict(globals()))
