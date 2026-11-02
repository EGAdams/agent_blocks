# company_todos

Toyota's company to-do list. Added 2026-10-30 at EG's request ("We need a
company todo list").

## What runs

`adws/adw_interface_company_todos.py`, three phases, no model:
`read_request -> apply_change -> report`. The logic lives in
`lancedb_memory/company_todos/` (store, request reader, desk), with its own
offline tests: `.venv/bin/python -m company_todos.tests.test_company_todos`.

`profiles.py` maps this agent to `CompanyTodoRunner`, which repeats the ADW's
line verbatim. `adapters/todo_route.CompanyTodoRoute` forces the route for any
latest user message that names the to-do list, ahead of Toyota's other routes,
so the list is never answered from recalled conversation. It leaves "we need a
to-do list" (talk about having one) to the model.

## Where the list is

`~/company_todos/todos.jsonl` (mode 600, folder 700; `COMPANY_TODOS_HOME`
overrides). Append-only: every add and check-off is a new line holding the
item's whole new version, so the file is also the history. Items are numbered
and are completed, never deleted.

## What it understands

- add: "add X to the to-do list", "put X on the company todo list", "to-do: X"
- read: anything else naming the list ("what's on the to-do list?")
- check off: "mark number 3 done", "check off #2", "the roofer is done, cross it off the to-do list"

A check-off by name must match exactly one open item; otherwise nothing changes
and the candidates are listed. A sentence with "don't", "never" or "how do I"
changes nothing.

Diagrams and an HTML manual are a separate job for a separate agent.
