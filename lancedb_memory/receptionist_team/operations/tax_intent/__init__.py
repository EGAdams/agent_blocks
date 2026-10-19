"""Tax assignment Policy: validate model-selected operations before running ADWs."""
import re

from ..models import ConversationRequest, WorkIntent, WorkView


def _normalized(text: str) -> str:
    """Letters/digits only, casefolded, so punctuation like "(RJ)" vs "R.J." vs "RJ" lines up."""
    return re.sub(r'[^a-z0-9]', '', text.casefold())


class TaxIssuesPolicy:
    def validate(self, intent: WorkIntent, request: ConversationRequest, state: WorkView) -> WorkIntent:
        if intent.assignment != 'tax_issues':
            raise ValueError('That assignment is not installed yet. Tax-issues training is available.')
        ids = set(re.findall(r'(?:expense\s*#?\s*|#)(\d+)\b', request.text.casefold()))
        if len(ids) > 1:
            raise ValueError('Which one expense should I work on? Please give a single expense number.')
        if intent.expense_id is not None:
            explicit = re.search(rf'\b{intent.expense_id}\b', request.text)
            if not explicit and intent.expense_id != state.expense_id:
                raise ValueError("Which expense do you mean? I won't guess a record number.")
            if ids and str(intent.expense_id) not in ids:
                raise ValueError('The requested expense does not match the operation. Please specify one expense.')
        elif ids:
            intent = intent.model_copy(update={'expense_id': int(next(iter(ids)))})
        if intent.action == 'select' and intent.expense_id is None:
            raise ValueError('Which expense should I open? Please give its number.')
        if intent.action == 'category':
            if not intent.category.strip() or _normalized(intent.category) not in _normalized(request.text):
                raise ValueError('Which exact category should I use? Please name the category in your instruction.')
        return intent.model_copy(update={'instruction': request.text})
