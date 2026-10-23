---
name: new-receptionist-agent
description: Add a new agent to a receptionist team (Toyota's or Mazda's) as a folder under agents/ — system prompt, user prompt, model config, tool stubs, documentation stubs — then register it so the receptionist can route to it, and verify with the catalog CLI and an offline test. Use when EG says "add an agent", "new specialist for Toyota", "make an agent that <does X>", or wants an existing hardcoded role migrated out of Python into a folder.
---

# Add an agent to a receptionist team

An agent is a **folder**, never a Python prompt literal. The loader that reads
these folders lives in `lancedb_memory/receptionist_team/agents/`; read its
`README.md` before the first `mkdir` — it is the contract this skill follows.

Everything below runs from `lancedb_memory/` with `.venv/bin/python`. Bare
`python3` has none of the dependencies.

## 1. Settle what the agent is, before writing anything

Three answers are required, and guessing any of them produces a useless agent:

- **One sentence of job.** If it needs "and", it is two agents.
- **Specialist or conversationalist?** A specialist is delegated to, runs
  headless with tools, and writes a deliverable — it needs an `output_path`. A
  conversationalist talks to a human and has none.
- **What it must never do.** Every specialist here has a destructive
  neighbour: read-only against the books, never overwrite the input file,
  never touch the database. Say it in the system prompt, not in a comment.

Ask EG if the job sentence is ambiguous. Do not invent a category name, a
year, a filename, or a database table.

## 2. Create the folder

```bash
cd lancedb_memory/receptionist_team/agents
AGENT=<snake_case_name>
mkdir -p "$AGENT"/{system_prompt_template,user_prompt_template,model_confiurations,tools}
mkdir -p "$AGENT/documentation/mermaid diagrams"/{flowchart,sequence,class}
mkdir -p "$AGENT/documentation/html_manual"/{toc,public_links}
```

`model_confiurations` is spelled exactly like that on disk. Do not "fix" it —
`layout.py` is the one place the name lives in code, and renaming it orphans
the folders that already exist.

## 3. Write the four files

**`system_prompt_template/system_prompt.md`** — who it is and its hard limits.
Second person, present tense, no Markdown headings. Copy the posture of
`spreadsheet_reshaper`'s: what it reads, what it writes, and the sentence
beginning "Never".

**`user_prompt_template/user_prompt.md`** — the job as numbered steps, ending
with what single sentence to reply with, and what to do when a required detail
is missing ("ask which file; never guess it"). For a *conversationalist*, this
file is the turn template instead and must keep `<<CONVERSATION>>` and
`<<MESSAGE>>` intact — those belong to `agent_memory.prompts`, not to this
package.

`{{placeholder}}` is this package's syntax, filled by whichever module owns
that wording (`roles.py` fills Toyota's `{{roster}}`). An unfilled placeholder
raises at import, by design — so do not add one unless something renders it.

**`model_confiurations/model.json`**

```json
{
  "description": "one line, shown by `cli list`",
  "model": null,
  "max_turns": 20,
  "allowed_tools": [ "Bash", "Read", "Write", "Glob", "Grep" ],
  "permission_mode": "bypassPermissions",
  "output_path": "/home/adamsl/rol_finances/readable_documents/reports",
  "notes": "anything the next reader needs and the fields above cannot say"
}
```

`model: null` means the composition root picks (`--role-model`, default
`claude-sonnet-5`) — prefer that over pinning. `output_path` is required for a
specialist and must be `null` for a conversationalist. Trim `allowed_tools` to
what the job actually needs; a read-only agent does not get `Write`.

**`tools/<name>/tool.json`** only if the agent declares a capability. A folder
with no `tool.json` reports as a stub named after the directory, which is a
fine way to record intent. Tool folders are declarations; nothing loads them as
code.

Leave `documentation/` empty. Generating diagrams and the HTML manual is a
separate job for a separate agent — copy another agent's
`documentation/README.md` into it so the next reader knows that.

## 4. Register it so routing works

Only needed for a specialist. Three edits, all in
`lancedb_memory/receptionist_team/`:

1. `extra_roles.py` — add the name to `EXTRA_ROLE_NAMES`.
2. `roles.py` — add an entry to **all three** of `ROLE_BLURBS`,
   `ROLE_SPOKEN_NAMES`, `ROLE_START_TASKS`. Toyota's import raises
   `RuntimeError` until every one exists; that is the intended failure, not
   something to work around. Blurbs are what the LLM routes on, spoken names
   are what the user hears, start tasks follow "tell them to start".
3. `agents/toyota/system_prompt_template/system_prompt.md` — add a short
   section with one or two literal `ROUTE: <name> | ...` examples, if choosing
   this agent needs more than its blurb. The roster and the allowed-names list
   are generated from `ROLES`; do not hand-edit those.

These files follow the *non*-spaced style. The `agents/` subtree follows the
SSSF bracket-spacing convention. Match the file you are editing; never
normalize across the boundary.

## 5. Verify — all of it, in this order

```bash
cd lancedb_memory
.venv/bin/python -m receptionist_team.agents.cli validate          # every part present?
.venv/bin/python -m receptionist_team.agents.cli show "$AGENT"     # prompts, config, tools
.venv/bin/python -c "from receptionist_team.roles import ROLES; print(ROLES['$AGENT'].max_turns)"
.venv/bin/python -m receptionist_team.agents.tests.test_agent_catalog
```

Then add a test beside the others in `receptionist_team/tests/` — the existing
`test_spreadsheet_reshape_role.py` is the pattern: assert the role exists with
the right tools, assert the prompt still carries its "never" guarantee, and
assert Toyota's system prompt knows when to route to it. Run every
`receptionist_team/tests/test_*.py`, not just the new one; `roles.py` is
imported by most of them, so a registration mistake surfaces there.

Restart the sidecar only if EG asks: `systemctl --user restart
toyota-receptionist`. It holds one warm agent and re-reads the folders on
start.

## Mazda's team

`mazda_team/` has no `agents/` directory yet; its specialists are literals in
`specialist_roles.py`. Mirroring this layout there is a real piece of work
(its own `build_catalog` wiring, and `adapters/routing_model.py` pointed at
it). Say so and get agreement before starting — do not half-migrate it.

## Migrating a hardcoded role into a folder

Same steps, plus: copy the prompt strings **verbatim** (write them from Python
rather than retyping, so whitespace survives), make the old module load from
the catalog instead of declaring the literal, and pin the result with a test
asserting the loaded role still has the fields it used to. If the owning file
is off-limits to edits — `finance_team/roles.py` is — stop and leave a README
in the folder saying what the migration needs, the way
`agents/toyota_mechanic/README.md` does. A second copy of a prompt that nothing
renders is worse than no folder at all.
