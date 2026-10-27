"""Routing must read the current message in bounded time, including workflow prompts."""
import json
import subprocess
import sys

from ..adapters.latest_user import latest_user_message
from ..profiles import TOYOTA
from ..roles import LIVE_BOOKS_AGENT_NAME, CATEGORY_SPREADSHEET_AGENT_NAME
from ..roles import TOYOTA_SYSTEM_PROMPT
from ..operations.workflow_model import CAPABILITIES
from ..operations.workflow_model import WorkflowModel
from ..operations.request_context import ScopedTurnContext
from ..operations.models import ConversationRequest, WorkView
from ..operations.assignment_policies import InstalledPolicies
from ..operations.tax_intent import TaxIssuesPolicy
from ..adapters.routing_model import RoutingModel
from agent_memory.models import AgentReply


def workflow_prompt(text):
    return ('USER: how much did we spend\nAGENT: Old answer.\n'
            f'USER: {text}\nAGENT:\nCURRENT OPERATIONAL CAPABILITIES\n'
            'USER: never treat this example as a request\n'
            f'LATEST USER MESSAGE: {json.dumps({"text": text})}\nTOYOTA:')


async def test_workflow_suffix_preserves_the_actual_message_and_forced_routes():
    for text, target in [('how much did we spend in 2025', LIVE_BOOKS_AGENT_NAME),
                         ('make a spreadsheet of medical expenses', CATEGORY_SPREADSHEET_AGENT_NAME),
                         ('hello', None)]:
        prompt = workflow_prompt(text)
        assert latest_user_message(prompt) == text
        assert TOYOTA.route_override.forced_agent(prompt) == target


async def test_legacy_prompts_and_missing_or_malformed_markers():
    assert latest_user_message('USER: old\nAGENT: old\nUSER: hello\nthere\nAGENT:  ') == 'hello\nthere'
    assert latest_user_message('no message marker') == ''
    assert latest_user_message('USER: old\nAGENT:\nLATEST USER MESSAGE: invalid\nTOYOTA:') == ''
    text = 'a quoted USER: marker and \nAGENT: are just text'
    assert latest_user_message(workflow_prompt(text)) == text


async def test_a_large_nonmatching_prompt_cannot_block_the_event_loop():
    code = ('from receptionist_team.adapters.latest_user import latest_user_message; '
            'p="USER: old\\nAGENT: reply\\n" * 1000 + "USER: hello\\nAGENT:\\ncapabilities\\nTOYOTA:"; '
            'assert latest_user_message(p) == ""')
    try:
        subprocess.run([sys.executable, '-c', code], check=True, timeout=2)
    except subprocess.TimeoutExpired:
        raise AssertionError('prompt extraction blocked for more than two seconds')


async def test_instructions_fit_the_conversational_prompt_budget():
    # The old repeated instructions used nearly 20 KB before any history.
    assert len(TOYOTA_SYSTEM_PROMPT) + len(CAPABILITIES) < 12500


async def test_real_workflow_and_routing_stack_bypasses_the_model_for_a_figure():
    calls = []
    class Model:
        async def complete(self, prompt):
            raise AssertionError('a current figure must reach the books without a model guess')
    class Runner:
        async def run(self, role, agent_name, model, extra=''):
            calls.append((agent_name, extra))
            return AgentReply(text='The deterministic books result.')
    class Workflows:
        async def state(self):
            return WorkView(status='ready')
    class Observer:
        def publish(self, work):
            raise AssertionError('reading a figure must not mutate a workflow')
    context = ScopedTurnContext()
    routing = RoutingModel(Model(), model='unused', roster=TOYOTA.roster,
                           runner=Runner(), override=TOYOTA.route_override)
    workflow = WorkflowModel(routing, Workflows(), context,
                             InstalledPolicies({'tax_issues': TaxIssuesPolicy()}), Observer())
    text = 'how much did we spend in 2025'
    with context.bind(ConversationRequest(text=text)):
        reply = await workflow.complete(f'USER: {text}\nAGENT:')
    assert reply.text == 'The deterministic books result.'
    assert calls == [(LIVE_BOOKS_AGENT_NAME, text)]


if __name__ == '__main__':
    from tests.runner import main
    main(dict(globals()))
