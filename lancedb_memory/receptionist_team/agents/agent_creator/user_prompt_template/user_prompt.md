Build a new agent for Toyota's team. What capability it must have is in the
additional instructions below, in the user's own words. If those words do not
say what the agent should DO in one sentence, do not guess and do not build
half of it: reply asking for the one thing you are missing, and stop.

1. Settle the job before writing anything. Decide and state to yourself:
   the agent's snake_case name; its one sentence of job; whether it is a
   delegated specialist or a conversationalist; what it must never do; and
   whether it needs an ADW. A job sentence that needs the word "and" is two
   agents, so split it and build the one that was actually asked for.

2. Decide whether an ADW is warranted. Build one when the capability acts on
   the world or has a repeatable sequence of steps worth tracing: launching
   something, calling a service, a multi-step job whose middle steps can fail
   independently. Skip it when the capability is a single question answered by
   reading files. Say which you chose and why in your final reply.

3. If an ADW is warranted, write it to
   /home/adamsl/agent_blocks/adws/adw_<name>.py.

   Read adw_interface_prompt.py and adw_map_open.py first, then write in the
   interface style: a class per phase implementing IPhase, an IPhaseExecutor
   to run them, small adapter classes for config loading and session creation
   and argument parsing, an IApplication that depends only on those
   interfaces, and a build_application() composition root at the bottom as the
   one place that names concrete classes. No `with` blocks.

   Import the interface vocabulary. Do not restate it:

       from sssf_roles import ( IApplication, IArgumentParser, IPhase,
                                IPhaseExecutor, ILifecycleLog, LifecycleLogged,
                                ManualContextPhaseExecutor, PrintLifecycleLog,
                                ConsoleLifecycleLog, SssfConfigLoader,
                                SessionRunFactory )

   sssf_roles.py sits in /home/adamsl/agent_blocks/adws/ and re-exports the
   factory's definitions, so there is exactly one copy of each interface in
   the workspace. A second copy pasted into your ADW is a defect even when it
   runs.

   Start the file with the `#!/usr/bin/env -S uv run` line and the PEP 723
   dependency header that every other ADW in that directory has. Give it a
   module docstring with a Usage block and a Phases line. A phase whose work
   is a known command is kind="code" and needs no agent; only use kind="agent"
   when a judgement is genuinely required, and then name an agent that is
   actually in sssf.config.yaml.

4. Smoke-test the ADW before you build anything that depends on it:

       cd /home/adamsl/agent_blocks/adws
       /home/adamsl/agent_blocks/lancedb_memory/.venv/bin/python adw_<name>.py --help

   That proves it imports, the interfaces resolved, and the argument parser is
   wired. Then run it once for real, with a dry-run flag if you gave it one.
   Do not proceed to step 5 until the ADW works. An agent whose ADW is broken
   is worse than no agent, because Toyota will route to it.

5. Build the agent folder, following
   /home/adamsl/agent_blocks/.claude/skills/new-receptionist-agent/SKILL.md
   exactly: the five part directories, the two prompt files, model.json, and a
   documentation/README.md copied from another agent so the next reader knows
   it is a stub.

   The agent's user prompt must name the ADW's exact command line, say how to
   choose each argument from what the user asked, and say what single sentence
   to reply with. It must also say what to do when a required argument is
   missing: ask for it, never guess it. In model.json, set output_path for a
   specialist (it is required, and a role cannot be built without it), keep
   model as null so the composition root chooses, and trim allowed_tools to
   what the job actually needs.

6. Register it, if and only if it is a delegated specialist. Three edits:

   extra_roles.py, adding the name to EXTRA_ROLE_NAMES.
   roles.py, adding one entry to each of ROLE_BLURBS, ROLE_SPOKEN_NAMES and
   ROLE_START_TASKS. All three, or Toyota raises at import. The blurb is what
   the model routes on, the spoken name is what the user hears, and the start
   task follows the words "tell them to start".
   agents/toyota/system_prompt_template/system_prompt.md, adding a short
   section with one or two literal `ROUTE: <name> | ...` examples. Do not
   hand-edit the roster or the allowed-names list in that file; both are
   generated from ROLES.

   These three files use the unspaced style. The agents/ subtree does not.

7. Run the gate, all of it, from /home/adamsl/agent_blocks/lancedb_memory
   with .venv/bin/python:

       -m receptionist_team.agents.cli validate
       -m receptionist_team.agents.cli show <name>
       -c "from receptionist_team.roles import ROLES; print(sorted(ROLES))"
       -m receptionist_team.agents.tests.test_agent_catalog
       -m receptionist_team.tests.test_spreadsheet_reshape_role

   `validate` reporting another agent as missing only its `tools` folder is
   expected and is not your failure; your new one must not be missing a prompt
   and must appear in the ROLES list. If any step fails, revert your edits to the three files
   from step 6, keep the folder and the ADW, and say so plainly.

8. Reply with two or three sentences in plain prose: the agent's name, what it
   does, whether you built an ADW and what it is called, and that the gate
   passed. Mention that Toyota picks up the new agent when she next restarts.
   No Markdown, no bullets, no file listings.
