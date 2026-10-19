"""Reports assignment Policy: validate model-selected report requests."""
from ..models import ConversationRequest, WorkIntent, WorkView


class ReportsPolicy:
    def validate(self, intent: WorkIntent, request: ConversationRequest, state: WorkView) -> WorkIntent:
        if intent.assignment != 'reports':
            raise ValueError('That assignment is not installed yet. Reports is available.')
        if intent.action != 'status' and not intent.report_type.strip():
            raise ValueError('Which report? Please name one of the installed reports.')
        return intent.model_copy(update={'instruction': request.text})
