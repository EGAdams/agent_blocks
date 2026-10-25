# agent_creator — the agent that builds agents

Routed to as `agent_creator`; Toyota calls it "our agent creator". To be
renamed **Scissari** once the name is settled — that is a rename of the folder,
the three entries in `../../roles.py`, the name in `../../extra_roles.py`, and
the section in `../toyota/system_prompt_template/system_prompt.md`, in one
commit.

## What it delivers

One request ("create an agent that opens a browser") produces up to three
things:

1. **An ADW** at `/home/adamsl/agent_blocks/adws/adw_<name>.py`, written in
   `adw_interface_prompt.py`'s interface style — a class per phase, a phase
   executor, a composition root at the bottom. It imports the interface
   vocabulary from `adws/sssf_roles.py` rather than restating it.
2. **An agent folder** beside this one, built by following
   `.claude/skills/new-receptionist-agent/SKILL.md`, whose user prompt names
   the ADW's exact command line.
3. **Registration**, so Toyota can route to it: `extra_roles.py`,
   three dicts in `roles.py`, and a `ROUTE:` section in Toyota's own prompt.

Step 1 is skipped when the capability has no phases worth tracing — pure
analysis gets an agent and no ADW. The agent decides and says which it chose.

## Why it points at documents instead of restating them

Its system prompt names five files to read rather than summarising them. That
is deliberate and it is the same reason `sssf_interfaces/` exists: a procedure
copied into a prompt is a second definition that drifts from the first. When
`SKILL.md` or the ADW interface shape changes, this agent follows
automatically because it reads them at run time.

The corollary: **if you change the folder layout, the registration steps, or
the ADW interface, you do not need to edit this agent** — but you do need the
documents it reads to stay true.

## The verification gate

Registration edits three fail-loud files. A bad entry in `roles.py` means
Toyota raises `RuntimeError` at import and does not start at all, so the agent
is required to run the whole gate before reporting success, and to revert its
own registration edits (keeping the folder and the ADW) if any step fails.

`cli validate` reporting other agents as missing only a `tools/` folder is
expected, and the prompt says so, so a correct run is not mistaken for a
failure. (It used to name `toyota_mechanic`, whose prompts were not on disk
until 2026-10-24.)

## Known limits

- **cwd is not its workspace.** Every routed role runs with
  `cwd=/home/adamsl/rol_finances` (hardcoded in
  `../../adapters/routing_model.py`, and `AgentRole` has no `cwd` field
  because `finance_team/roles.py` is locked). The prompts therefore use
  absolute paths throughout. If `AgentRole` ever gains a `cwd`, this is the
  agent that most wants it.
- **Toyota must restart** to see a newly registered agent; `roles.py` renders
  her system prompt at import.
- **It can write `adws/adw_*.py`.** Those paths are in the factory config's
  `protected_files`, which constrains agents running *inside* an ADW session —
  not this role, which runs through `finance_team/adapters/tool_agent.py`. Its
  prompt carries that limit as an instruction instead, which is weaker than
  enforcement; that gap is worth closing if it ever misbehaves.
- **No sandbox.** Unlike `toyota_mechanic`, it edits the live tree directly.
  The gate plus revert-on-failure is the safety story, chosen because adding
  an agent is additive where the mechanic's job is to modify running code.
