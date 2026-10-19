# Changeable receptionist assignments

Toyota stays the conversational coordinator. Every turn goes through the normal
ConversationAgent memory cycle, including turns that run an ADW. The original
user instruction and actual result are stored in LanceDB; the nexus archive is
still write-only for recall.

WorkflowModel is a LanguageModel Decorator. It loads the current checkpoint and
supplies installed capabilities to the conversational model on every turn. The
model can answer capability questions naturally or select one structured WORK
Command. TaxIssuesPolicy validates its expense and category against the actual
instruction before the injected Workflows interface runs it. There is no list
of required magic phrases or fixed console menu numbers.

OperationalConversationAgent only scopes original request text and stable
request identity through the TurnContext interface. ScopedTurnContext is the
ContextVar adapter chosen in main.py. The memory engine imports no new concrete
integrations. The HTTP adapter invokes the dashboard Facade and canonical finance
ADWs; checkpoint state is independent of conversational semantic recall.

The installed assignment is synthetic tax training, expense #900001. Toyota can
start/resume, select a record, show its supporting receipt, open the normal
editor, change a specifically requested category, check progress, pause or cancel.
General file search, arbitrary terminal/browser control and live expense edits
remain unimplemented. The existing finance specialists retain their separate
ROUTE interface.

Patterns: Decorator for conversation/model, Policy for authorization, Command for
operations, Adapter for HTTP and request context, Observer for work events.
Python boundaries use Protocol and Pydantic; browser ports use strict TypeScript.

Runbook: /home/adamsl/rol_finances/adws/RECEPTIONIST_INTEGRATION.md

Offline checks from lancedb_memory/:

```bash
.venv/bin/python -m receptionist_team.tests.test_operations
.venv/bin/python -m receptionist_team.tests.test_service_turns
.venv/bin/python tests/test_agent.py
```
