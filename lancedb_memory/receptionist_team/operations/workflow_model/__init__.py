"""LanguageModel Decorator: dynamic capabilities, checked Commands and ADW results.

The memory engine sees one completion and persists the original user turn and
the actual workflow result, just as it does for ordinary conversation.
"""
import re

from agent_memory.models import AgentReply
from agent_memory.ports import LanguageModel

from ..interfaces import AssignmentPolicies, TurnContext, Workflows, WorkObserver
from ..models import WorkIntent


from .capabilities import CAPABILITIES, LIVE_CAPABILITIES, capabilities_text

class WorkflowModel:
    def __init__(self, inner: LanguageModel, workflows: Workflows, context: TurnContext,
                 policies: AssignmentPolicies, observer: WorkObserver):
        self.inner, self.workflows, self.context = inner, workflows, context
        self.policies, self.observer = policies, observer

    async def complete(self, prompt: str) -> AgentReply:
        request = self.context.current()
        installed = self.policies.installed()
        if installed:
            try:
                state = await self.workflows.state()
            except OSError:
                state = None
            context = state.model_dump_json(exclude={'history', 'run_ids', 'command'}) if state else 'Workflow service unavailable; no operations can run.'
        else:
            # Live mode: the practice checkpoint (#900001) is never shown.
            state, context = None, 'None; no practice assignments are installed.'
        # The actual latest message follows recalled history. Old capability
        # answers and instructions cannot masquerade as today's user request.
        capabilities = capabilities_text(installed)
        reply = await self.inner.complete(
            f'{prompt}\n\n{capabilities}\nCURRENT CHECKPOINT: {context}\n'
            f'PENDING BROWSER OPERATION: {state.command.get("action") if state and state.command else "none"}\n'
            f'LATEST USER MESSAGE: {request.model_dump_json(include={"text"})}\nTOYOTA:')
        match = re.fullmatch(r'WORK:\s*(\{.*\})', reply.text.strip(), re.DOTALL)
        if not match:
            if reply.text.strip().startswith('WORK:'):
                return reply.model_copy(update={'text': "I couldn't validate that operation. No workflow was started or changed."})
            return reply
        try:
            if not installed:
                raise ValueError('No practice assignments are installed, so no workflow was started or changed.')
            if state is None:
                raise OSError('Workflow unavailable')
            requested = WorkIntent.model_validate_json(match[1])
            # Resolve the policy from the intent's own assignment, inside the try: an
            # uninstalled assignment is a refusal the user hears, not a crashed turn.
            policy = self.policies.for_assignment(requested.assignment)
            intent = policy.validate(requested, request, state)
            result = await self.workflows.execute(intent, request_id=request.request_id)
            self.observer.publish(result)
            text = result.state.message if result.ok else result.error or result.state.message
        except ValueError as error:
            text = str(error)
        except OSError:
            text = 'The workflow service is unavailable. Your assignment is retained; no operation was retried.'
        return reply.model_copy(update={'text': text})
