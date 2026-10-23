"""Conversation/workflow boundary regression tests; no network or database."""
import asyncio

from agent_memory.models import MemoryQuery
from tests.fakes import ScriptedModel
from tests.test_agent import build_agent
from ..operations.assignment_policies import InstalledPolicies
from ..operations.conversation import OperationalConversationAgent
from ..operations.models import WorkReply, WorkView
from ..operations.request_context import ScopedTurnContext
from ..operations.tax_intent import TaxIssuesPolicy
from ..operations.workflow_model import WorkflowModel


class Workflows:
    def __init__(self):
        self.calls = []
        self.current = WorkView(status='ready', expense_id=900001, category='Walgreens (RM)')

    async def state(self):
        return self.current

    async def execute(self, intent, request_id=None):
        self.calls.append((intent, request_id))
        return WorkReply(ok=True, state=WorkView(status='ready', message='Checked through an ADW.'))


class Observer:
    def __init__(self):
        self.events = []

    def publish(self, work):
        self.events.append(work)


def build(replies, workflows=None, policies=None):
    workflows = workflows or Workflows()
    model, context, observer = ScriptedModel(replies), ScopedTurnContext(), Observer()
    policies = policies or InstalledPolicies({'tax_issues': TaxIssuesPolicy()})
    workflow_model = WorkflowModel(model, workflows, context, policies, observer)
    engine, store, memory_observer = build_agent(workflow_model)
    return OperationalConversationAgent(engine, context), model, workflows, observer, store, memory_observer


async def test_exact_failed_capability_question_gets_current_capabilities_and_state():
    agent, model, workflows, _, _, _ = build(['I can help with tax-issues training and the editor.'])
    await agent.handle_turn('What do you have the ability to do?')
    prompt = model.prompts[0]
    assert 'CURRENT OPERATIONAL CAPABILITIES' in prompt
    # The capability text used to call the whole environment synthetic, on
    # every turn, which is most of why she believed it of the real books
    # too. Now only tax_issues is named as practice data.
    assert 'Only tax_issues is practice data' in prompt
    assert 'Walgreens (RM)' in prompt
    # And she is told, in the same breath, what she CAN read for real.
    assert 'ROUTE: live_books' in prompt
    assert '900001' in prompt and 'normal expense editor' in prompt
    assert workflows.calls == []  # A capability question never starts work.


async def test_semantic_commands_use_the_workflow_port_with_original_instruction_and_id():
    agent, _, workflows, observer, _, _ = build(['WORK: {"action":"start"}', 'WORK: {"action":"evidence"}'])
    reply = await agent.handle_turn("Let's tackle the tax problems", request_id='generation-0001')
    assert reply.text == 'Checked through an ADW.'
    await agent.handle_turn('Show me the paperwork for this one')
    assert [call[0].action for call in workflows.calls] == ['start', 'evidence']
    assert workflows.calls[0][0].instruction == "Let's tackle the tax problems"
    assert workflows.calls[0][1] == 'generation-0001'
    assert len(observer.events) == 2


async def test_operational_turns_are_persisted_and_recalled_through_normal_memory_engine():
    agent, model, _, _, store, observer = build(['WORK: {"action":"status"}', 'We checked your tax work.'])
    await agent.handle_turn('Where did we leave off?', request_id='turn-memory-0001')
    hits = store.search(MemoryQuery(vector=[0.0] * 256, session_id='s1', limit=10))
    messages = {hit.turn.message for hit in hits}
    assert messages == {'Where did we leave off?', 'Checked through an ADW.'}
    assert all('CURRENT OPERATIONAL CAPABILITIES' not in message for message in messages)
    await agent.handle_turn('What did we just check?')
    assert 'Checked through an ADW.' in model.prompts[1]
    assert len(observer.calls) == 2


async def test_clear_context_forgets_recall_without_deleting_history():
    """The dashboard's 'compact conversation' button calls agent.clear_context():
    the next turn must not recall anything said before, but nothing already
    persisted is deleted -- the old session's turns are still in the store."""
    agent, model, _, _, store, _ = build(['Noted.', 'Nothing comes to mind.'])
    await agent.handle_turn('the secret passphrase is orangutan')

    new_session = agent.clear_context()
    assert new_session != 's1'

    await agent.handle_turn('what is the secret passphrase?')
    assert 'orangutan' not in model.prompts[1]

    hits = store.search(MemoryQuery(vector=[0.0] * 256, session_id='s1', limit=10))
    assert any('orangutan' in hit.turn.message for hit in hits)


async def test_unrequested_and_ambiguous_category_targets_do_not_run():
    cases = [
        ('Fix Walgreens', '{"action":"category","category":"Walgreens (RM)"}', 'exact category'),
        ('Change expense #900001 and expense #557 to Walgreens (RM)', '{"action":"category","category":"Walgreens (RM)"}', 'single expense'),
        ('Open this one', '{"action":"select","expense_id":557}', 'guess a record'),
        ('Open expense #557', '{"action":"select","expense_id":900001}', 'does not match'),
    ]
    for text, control, expected in cases:
        agent, _, workflows, _, _, _ = build(['WORK: ' + control])
        assert expected in (await agent.handle_turn(text)).text
        assert workflows.calls == []


async def test_explicit_category_uses_selected_expense_and_policy_preserves_original_request():
    agent, _, workflows, _, _, _ = build(['WORK: {"action":"category","category":"Walgreens (RJ)"}'])
    await agent.handle_turn('Please put this one under Walgreens (RJ)')
    intent = workflows.calls[0][0]
    assert intent.category == 'Walgreens (RJ)' and intent.expense_id is None
    assert intent.instruction == 'Please put this one under Walgreens (RJ)'


async def test_category_change_is_accepted_without_the_canonical_parentheses():
    # The user never has to type the model's own "(RJ)" punctuation back verbatim;
    # saying the name and the distinguishing initials is enough to count as explicit.
    phrasings = [
        'Change the category to Walgreens RJ for Walgreens Robert Gerald.',
        "Walgreens R.J. Right now it's Walgreens R.M. as in Rosemary. We need Walgreens R.J. as in Robert Gerald.",
    ]
    for text in phrasings:
        agent, _, workflows, _, _, _ = build(['WORK: {"action":"category","category":"Walgreens (RJ)"}'])
        reply = await agent.handle_turn(text)
        assert workflows.calls, f'policy rejected an explicit category phrased as: {text!r} ({reply.text})'
        assert workflows.calls[0][0].category == 'Walgreens (RJ)'


async def test_category_change_is_accepted_when_the_model_emits_the_full_path():
    # The checkpoint shows categories as "Church / Robert — Benefits & Medical / Walgreens (RJ)";
    # the model copies that path, but the user only ever says the leaf name.
    full = 'Church / Robert — Benefits & Medical / Walgreens (RJ)'
    for text in ['Change it to Walgreens R.J.', 'Put it under Walgreens RJ please']:
        agent, _, workflows, _, _, _ = build(['WORK: {"action":"category","category":"%s"}' % full])
        reply = await agent.handle_turn(text)
        assert workflows.calls, f'full-path category rejected for {text!r} ({reply.text})'


async def test_category_change_is_still_refused_when_the_model_guesses():
    # Guarding against the opposite regression: loosening the match must not let the
    # model's own guessed category sneak through when the user never named one at all.
    agent, _, workflows, _, _, _ = build(['WORK: {"action":"category","category":"Walgreens (RM)"}'])
    reply = await agent.handle_turn('Fix Walgreens')
    assert 'exact category' in reply.text and workflows.calls == []


async def test_offline_work_service_allows_honest_conversation_but_no_operations():
    class Offline(Workflows):
        async def state(self):
            raise OSError('offline')
    agent, model, workflows, _, _, _ = build(['The workflow service is unavailable.', 'WORK: {"action":"status"}'], Offline())
    await agent.handle_turn('What can you do?')
    assert 'Workflow service unavailable' in model.prompts[0]
    reply = await agent.handle_turn('Check its category')
    assert 'unavailable' in reply.text and workflows.calls == []


async def test_context_is_restored_and_malformed_commands_are_not_spoken_or_run():
    agent, _, workflows, _, _, _ = build(['WORK: {"action":"delete"}', 'WORK: oops'])
    for _ in range(2):
        reply = await agent.handle_turn('Check it')
        assert not reply.text.startswith('WORK:')
    assert workflows.calls == []
    try:
        agent.context.current()
        assert False, 'request context leaked beyond the turn'
    except LookupError:
        pass


async def test_an_assignment_that_is_not_installed_is_refused_by_name_and_runs_nothing():
    agent, _, workflows, observer, _, _ = build(['WORK: {"action":"start","assignment":"payroll"}'])
    reply = await agent.handle_turn("Let's do payroll")
    # The refusal has to say what IS installed; 'not installed yet' alone leaves the
    # user guessing which words would have worked.
    assert 'not installed' in reply.text and 'tax_issues' in reply.text
    assert workflows.calls == [] and observer.events == []


async def test_each_installed_assignment_is_validated_by_its_own_policy():
    class SpyPolicy:
        def __init__(self, name):
            self.name, self.seen = name, []

        def validate(self, intent, request, state):
            self.seen.append(request.text)
            return intent.model_copy(update={'instruction': request.text})

    tax, other = SpyPolicy('tax_issues'), SpyPolicy('receipts')
    policies = InstalledPolicies({'tax_issues': tax, 'receipts': other})
    agent, _, workflows, _, _, _ = build(['WORK: {"action":"status","assignment":"receipts"}'], policies=policies)
    await agent.handle_turn('How are the receipts going?')
    # Routed by the intent's own assignment: the tax policy must not see another
    # assignment's request, and the receipts policy must not be bypassed.
    assert other.seen == ['How are the receipts going?'] and tax.seen == []
    assert workflows.calls[0][0].assignment == 'receipts'


async def test_the_spoken_capabilities_list_installed_assignments_rather_than_a_fixed_string():
    policies = InstalledPolicies({'tax_issues': TaxIssuesPolicy(), 'receipts': TaxIssuesPolicy()})
    agent, model, _, _, _, _ = build(['I can help with those.'], policies=policies)
    await agent.handle_turn('What can you do?')
    # The prompt's claim of what is installed is derived from the registry, so it
    # cannot keep advertising an assignment that was removed from the composition root.
    assert 'tax_issues' in model.prompts[0] and 'receipts' in model.prompts[0]


async def test_the_prompt_tells_her_to_offer_the_closest_thing_when_refusing():
    agent, model, _, _, _, _ = build(['Not yet.'])
    await agent.handle_turn('Can you make me a spreadsheet of the real books?')
    assert 'closest thing' in model.prompts[0]
    assert 'ROUTE: category_spreadsheet' in model.prompts[0]


async def test_the_registry_reports_what_is_installed_without_running_a_turn():
    policies = InstalledPolicies({'tax_issues': TaxIssuesPolicy()})
    assert policies.installed() == ('tax_issues',)
    try:
        policies.for_assignment('payroll')
        assert False, 'an uninstalled assignment resolved to a policy'
    except ValueError as error:
        assert 'tax_issues' in str(error)


async def test_the_turn_context_advertises_every_agent_toyota_can_route_to():
    agent, model, _, _, _, _ = build(['Sure.'])
    await agent.handle_turn('What can you do?')
    prompt = model.prompts[0]
    # The capability block is labelled authoritative for the turn, so a role that
    # is registered in roles.py but missing here gets denied no matter what
    # Toyota's system prompt says -- which is exactly how browser_opener was
    # invisible to her after it was created.
    for name in ('browser_opener', 'spreadsheet_reshaper', 'agent_creator'):
        assert f'ROUTE: {name}' in prompt, name
    assert 'browse arbitrary websites' not in prompt


if __name__ == '__main__':
    from tests.runner import main
    main(dict(globals()))
