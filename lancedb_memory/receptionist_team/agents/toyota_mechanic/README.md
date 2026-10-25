# toyota_mechanic -- Toyota diagnosing or improving her own code

## What it accepts

One request about **Toyota's own Python** (`receptionist_team`, `finance_team`,
`agent_memory`, `sssf_interfaces` under `lancedb_memory/`), in one of two modes:

- **diagnose** -- "what is wrong with X / what would it take to Y". Nothing is
  changed; the reply is a diagnosis and the smallest fix.
- **change** -- "make Toyota do X". The change is made test-first in a sandbox
  copy, every `receptionist_team/tests/` suite is run, and it is applied to the
  live code only if all of them pass with no fewer tests than before. Toyota
  then restarts and rolls herself back if she does not come back healthy.

The more precise the request, the better: name the behaviour, where it shows
up, and what "fixed" looks like. One enhancement per run. It cannot touch the
database, any other repository, the dashboard, or a running service other than
Toyota's own restart, and it never deletes files.

Outcomes the workflow prints, one of: `DIAGNOSIS:`, `DONE:` (with `CHANGED:`
lines), `NO_CHANGE:`, `REFUSED:` (nothing reached live code), `BUSY:`.

## Where its parts live

Everything is in `agent_blocks`:

| part | where |
|---|---|
| the dispatcher Toyota routes to (Bash + Read; runs the workflow, relays its last lines) | this folder |
| the coder that edits the sandbox (`claude-sonnet-5`, pinned in its `model.json`) | `../mechanic_coder/` |
| the workflow: snapshot, baseline, coder, verify/fix, guard, apply, watchdog restart | `adws/adw_toyota_mechanic.py` |
| the guard: what is protected, the gate, apply and rollback | `adws/adw_toyota_mechanic_guard.py` |
| runs, sandboxes, backups, the lock (gitignored) | `adws/adw_data/mechanic/` |

The dispatcher has no model of its own and runs on Toyota's `--role-model`.
It is registered in `../../extra_roles.py` like every other agent folder.
`mechanic_coder` is deliberately **not** registered: outside the workflow it
would edit live code with no sandbox, gate or rollback.

## Why the guard protects these folders

The mechanic edits Toyota's code, and these two folders are its own
instructions. `PROTECTED` in the guard names both (plus
`finance_team/roles.py`), so a run that changes either is refused rather than
applied. The workflow and guard need no entry: they live in `adws/`, which is
never part of the sandbox. Move either folder and update `PROTECTED` in the
same commit -- `receptionist_team/tests/test_toyota_mechanic_guard.py` fails
until you do.

Until 2026-10-24 the definition lived in `finance_team/roles.py` and
`finance_team/mechanic_runner.py`, and the workflow in
`/home/adamsl/rol_finances/adws/`; those copies are retired.
