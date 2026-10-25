# `receptionist_team/agents/` — one folder per agent

Every agent on Toyota's team is a **directory**, not a Python literal. Toyota
herself is one of them; she is not a special case.

```
agents/
  <agent_name>/
    system_prompt_template/system_prompt.md   # who the agent is
    user_prompt_template/user_prompt.md       # the task, or the turn template
    model_confiurations/model.json            # model, turns, tools, output path
    tools/<tool_name>/tool.json               # declared capabilities
    documentation/                            # generated later (stub)
      mermaid diagrams/{flowchart,sequence,class}/
      html_manual/{toc,public_links}/
```

`model_confiurations` is spelled exactly that way on disk. It is the layout
the folders were created with, so it is preserved; `layout.py` is the single
place the name appears in code.

## Add an agent

1. `mkdir -p agents/<name>/{system_prompt_template,user_prompt_template,model_confiurations,tools,documentation}`
2. Write `system_prompt_template/system_prompt.md` — who it is, what it must
   never do.
3. Write `user_prompt_template/user_prompt.md` — the numbered job, and what one
   sentence to reply with.
4. Write `model_confiurations/model.json`. `output_path` is required for a
   delegated specialist (that is where its deliverable lands); leave it `null`
   for a conversationalist like Toyota.
5. To make it **routable**, add the name to `EXTRA_ROLE_NAMES` in
   `../extra_roles.py`, and give it an entry in `ROLE_BLURBS`,
   `ROLE_SPOKEN_NAMES` and `ROLE_START_TASKS` in `../roles.py` — Toyota's
   import fails loud until all three exist, on purpose.
6. `.venv/bin/python -m receptionist_team.agents.cli validate`

The `/new-receptionist-agent` skill walks exactly these steps.

**Or ask Toyota.** `agent_creator/` is an agent that performs this procedure
itself — it reads that same skill file at run time, builds the ADW the new
agent will drive, registers the result, and runs the verification gate before
reporting success. Say "create an agent that opens a browser" to Toyota. See
`agent_creator/README.md` for what it delivers and what it cannot do.

## Placeholders

System and user prompt templates may use `{{name}}`. The renderer raises
`MissingPlaceholderValue` if nobody supplies a value — a prompt shipped with a
visible `{{roster}}` in it is worse than a crash.

`<<CONVERSATION>>` and `<<MESSAGE>>` are **not** this package's syntax; they
belong to `agent_memory.prompts.TemplatePromptComposer`, which receives
Toyota's user prompt template unrendered. Two delimiters, two owners, no
collision.

## Who reads what

| consumer | what it takes from here |
|---|---|
| `../roles.py` | `toyota`'s system prompt, rendered with `{{roster}}` / `{{allowed_agents}}` |
| `../main.py` | `toyota`'s user prompt template, as the `TemplatePromptComposer` template |
| `../extra_roles.py` | every name in `EXTRA_ROLE_NAMES`, as a `finance_team` `AgentRole` |
| `cli.py` | everything, read-only |

## Design

One responsibility per module, and every collaborator arrives through an
interface (`interfaces.py`, `typing.Protocol`, `I`-prefixed — never `*Port`):

| module | its one reason to change |
|---|---|
| `layout.py` | the folder and file names |
| `prompt_templates.py` | `{{placeholder}}` syntax |
| `model_configurations.py` | that the config is JSON |
| `tool_catalog.py` | how a tool declares itself |
| `documentation.py` | what counts as documentation (**stub**) |
| `definitions.py` | how the five parts assemble into one agent |
| `catalog.py` | which directories are agents |
| `role_adapter.py` | the folder → `finance_team.AgentRole` mapping |
| `lifecycle.py` | where object lifecycles get reported |
| `cli.py` | the command line |
| `__init__.py` | the wiring (**composition root** — the only module naming every concrete at once) |

`IApplication`, `IArgumentParser`, `ILifecycleLog` and `LifecycleLogged` are
**imported**, not restated, from the SSSF factory's own
`adws/adw_interface_prompt.py` via `lancedb_memory/sssf_interfaces/`. That is
the standardization direction: one vocabulary across the workspace.

`IPhase`/`IPhaseExecutor` are not used yet — their contract is
`execute( run: Run, phase )`, and a read-only folder listing should not open an
SSSF session and tracer database. They become the right shape once these
commands are steps of a real ADW.

## Deliberately unfinished

- **`documentation/`** — empty everywhere. Generating diagrams and an HTML
  manual from an agent's prompts is its own job for its own agent; the loader
  only reports which directories have files. See `documentation.py`.
- **`tools/*/tool.json`** — mostly declarations, not code. Mazda's three are
  still `"kind": "stub"`: `bank_login` needs a credential story first, and
  `web_search` is probably an `allowed_tools` entry rather than a folder.
  `search_link_opener/tools/` and `live_books/tools/` are the exceptions and
  the pattern to copy — their files are `"status": "implemented"`, each naming
  a thing that exists (`adws/adw_interface_search_open.py` and
  `/home/adamsl/selenium-headless/browser_manager/`;
  `adws/adw_interface_live_finance.py` and the live `nonprofit_finance`
  schema, the one `"kind": "database"` declaration here). A `tool.json` claiming
  `implemented` is a promise the agent's prompt is allowed to rely on, so only
  write it once the thing on the other end runs.
- **`mechanic_coder/`** — a complete folder that is deliberately *not*
  routable. It is the coder `adws/adw_toyota_mechanic.py` runs inside a
  sandbox; listed in `EXTRA_ROLE_NAMES` it would edit live code with no
  sandbox, gate or rollback. It and `toyota_mechanic/` are the guard's
  `PROTECTED` folders — see `toyota_mechanic/README.md`.

## Style

This subtree follows the SSSF bracket-spacing convention (`f( x )`, `[ 0.0 ]`,
stacked `]))`), unlike the rest of `receptionist_team/`. Match the file you are
editing and never normalize across the boundary.

One trap, learned the hard way: the convention **cannot** reach inside an
f-string format specifier. `f"{ name:<24 }"` raises `ValueError: Unknown
format code`, and `f"#{ id( obj ):x }"` raises `Invalid format specifier` —
write `f"{ name:<24}"`. The shipped `PrintLifecycleLog` and
`ConsoleLifecycleLog` in `adw_interface_prompt.py` both have this bug and
raise on first call, which is why `lifecycle.py` implements the interface
locally instead of importing those two.

## Tests

```bash
cd lancedb_memory
.venv/bin/python -m receptionist_team.agents.tests.test_agent_catalog
.venv/bin/python -m receptionist_team.agents.cli validate
```

Offline: reads only these folders. No LanceDB, no Groq, no Claude, no torch.
