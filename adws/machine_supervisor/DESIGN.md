# Machine Supervisor

One supervisor per machine (BATI, SQMC, R46, and DAD on Codex: see below). She sleeps. A cron ADW with no model in it looks at the
machine every two minutes and wakes her only when a check has failed enough times in a row.

## The ladder

1. `adw_machine_watch.py` (cron, no model) runs the machine's checks from `machines.json`. A check that
   fails `threshold` times in a row opens an incident and starts `adw_machine_supervise.py`, detached.
2. Rung 1: this machine's supervisor (Claude CLI, headless) diagnoses and repairs. The checks are run
   again by code; her own word that it is fixed is not taken. Fixed: one mail to EG saying what went
   wrong and what was done. No alert.
3. Rung 2: an alert on every machine, and the first peer's supervisor is woken over SSH to help.
4. Rung 3: the same with the second peer.
5. Nobody fixed it: an alert, and a HIGH PRIORITY mail holding a prompt for a more capable agent. The
   check is then left alone for `cooldown_minutes` so the ladder does not run all night.

A check already passing when she wakes is closed as "recovered by itself" with no agent run.
`adw_machine_supervise.py --drill` climbs every rung in look-only mode, tagged `[DRILL]`.

## What the watch looks at, with no model

Two families of check, both plain code, both counted the same way. A model is started only when one of them
keeps failing.

- **The machine's services** (`adapters/probes.py`): `http`, `unit`, `tcp` and `command` checks.
- **The supervisor's own machinery** (`adapters/self_probes.py`, added 2026-11-27). These catch the failures
  that would otherwise leave a machine unwatched:
  - `todos`: a todo that may be worked has waited more than `minutes` in `inbox/new`, counted from its
    `not_before` when it is waiting out a usage limit. Or a todo has sat in `in_process` longer than
    `agent_timeout` plus `minutes`.
  - `held-mail`: the oldest held mail is older than `minutes`. DAD has no such check, because DAD has no route
    out and its mail is always held.
  - `adw-log`: the latest run of any ADW in `background.log` (`todo --work`, `mail --flush`, `supervise`, ...)
    ended in a traceback. The check passes once that ADW's next run ends cleanly.
  - `disk`: less than `free_gb` free under `target`.
  - `heartbeat`: another machine's `watch_state.json` has not been written in `minutes`, which means its cron
    or its Python is broken and nobody is watching it. SQMC watches BATI, R46 and DAD. BATI watches SQMC.
    A machine that is not `always_on` and does not answer over SSH counts as switched off, so the check
    passes.

## Rules enforced in code

- **Bash guard** (`guards/bash_guard.py`, a PreToolUse hook): denies lasting damage whatever the prompt
  says: recursive delete, git push/reset/commit, reboot, `wsl --shutdown`, credential files, disk tools,
  destructive SQL, crontab replace, package installs, `tailscale down`. Look-only mode (drills, answers
  to a peer) also denies every change. It is a deny table, not a sandbox. It fails closed.
- **Mail guard** (`guards/mail_guard.py`): the mailing agent may call one tool, with one recipient (from
  `~/.config/machine_supervisor/settings.json`, never the repo), one exact subject, and a body whose
  SHA-256 matches. Mail counts as sent only when the connector's tool result says so.
- **Wake limit**: `max_wakes_per_day`; after that a mail is held and the check left alone.
- **One supervisor at a time** per machine (lock `supervisor`); the watch has its own lock.
- Gmail has no priority field. "High priority" is a subject prefix.
- **Usage limit is a wait, not a failure** (`adapters/usage_limit.py`, behind `IUsageLimit`): when the Claude CLI
  says "You've hit your 5-hour limit · resets 3:50pm (...)", the todo goes back to `inbox/new` with `not_before`
  set to the reset time (plus 5 minutes) and the watch does not start the worker for it before then; after 6 such
  deferrals it is failed. A todo filed as failed for that reason earlier is tried again only when named:
  `adw_machine_todo.py --work --id ID` (any other failure is refused). Added 2026-11-26 after four Sheila todos
  on BATI were filed as failed for good on 2026-11-24/25.
- **An interrupted todo is resumed once** (`interrupted_resumes`, `MAX_RESUMES = 1`): a todo left in
  `in_process` with nobody holding the lock goes back to `new`, and the watch starts the worker for it; a second
  interruption fails it. Its brief says to check what the first attempt already did. Added 2026-11-27.
- **One Bridge task, one todo**: a todo whose `context.bridge_task` matches one this machine already holds from
  the same sender (in any of new, in_process, completed, failed) is not filed again; the reply names the first as
  `filed_as`, and `TodoPoster` reports that id. Added 2026-11-27, so a Bridge task handed out twice cannot run twice.

## A supervisor on Codex (DAD)

A machine's `cli` in `machines.json` is `claude` (the default) or `codex`. DAD (Dad's Ubuntu computer) is `codex`:
the supervisor runs `codex exec` on Dad's ChatGPT login (`adapters/codex_agent.py`, `CodexCli`), not the Claude CLI.
Both satisfy `IAgentCli`; `kit.py` picks one, and the usage-limit reader (`CodexUsageLimit`: "try again at 3:50 PM")
with it. What differs, all enforced in code:

- Every Codex feature that adds a tool beyond the shell is switched off with `--disable` (apps, plugins, browser and
  computer use, images, goals, sub-agents) and web search is off. `code_mode_host` must stay on: Codex calls its
  tools through it, and with it off nothing runs at all.
- The guard is a PreToolUse hook with matcher `*`, and `bash_guard.py` now denies any tool that is not `Bash`
  (apply_patch, `collaboration*`, anything a later Codex adds). The Claude supervisors' matcher is `Bash`, so
  nothing changes for them.
- TTY execution is off (`unified_exec`, `unified_exec_tty`). With it on, `write_stdin` typed into an open `bash`
  and that typing never reached the hook (seen on DAD, 2026-11-27); with it off, stdin is closed.
- Codex blocks on a hook's exit 2 only when stderr carries a reason; with none it logs "Failed" and runs the
  command. The hook command therefore writes a reason when the guard cannot run.
- `--ignore-user-config`, `--ephemeral`, and `--dangerously-bypass-hook-trust` for this one run, so Codex's own
  config never gets a persisted hook trust.
- No OS sandbox: Codex's bubblewrap sandbox fails on DAD (restricted unprivileged user namespaces, bubblewrap not
  installed; both need sudo). That is the Claude supervisors' footing too: a deny table, not a sandbox.
- No Gmail connector: `NoConnectorMailer` (a Null Object) never sends, so DAD's mail is held in its outbox. DAD has
  no peers.
- Codex reports no cost, and has no system-prompt flag, so the rules go first in the prompt.

## Communication (Swarm-Forge's handoffs)

Messages are `agent_comms.models.Envelope` files; a message's state is the folder it sits in:
`handoffs/outbox -> sent | failed`, `handoffs/inbox/new -> in_process -> completed | failed`.
Between machines an envelope is carried by SSH to `adw_machine_receive.py`, which files it, runs its
handler and prints the reply envelope. Types: `alert`, `help`, `ask`, `mail` (peer supervisors only)
and `todo` (peers and `policy.todo_senders`). `adw_machine_talk.py` is the real-time question one
supervisor asks another during a repair; the answerer is look-only and may not ask onward.

Mail falls back: this machine's Gmail connector, then each peer's, then `mail_outbox/`, retried by the
watch every `mail_retry_minutes`.

## The ADWs (each a thin file over `kit.py`, the only composition root)

| ADW | Does | Model |
|---|---|---|
| `adw_machine_watch.py` | cron: look, count, wake | no |
| `adw_machine_cron.py --job N -- cmd` | wrap any other cron job; a non-zero exit is reported like a failed check | no |
| `adw_machine_supervise.py` | the ladder for one incident, or `--drill` | yes |
| `adw_machine_receive.py` | take one handoff on stdin | by type |
| `adw_machine_handoff.py` | send one handoff | no |
| `adw_machine_talk.py` | ask another supervisor now | far side |
| `adw_machine_alert.py` | toast on every machine | no |
| `adw_machine_mail.py` | send or `--flush` held mail | mail agent |
| `adw_machine_mailbox.py` | `machines`, `mailbox`, `read`, `status` for any machine | no |
| `adw_machine_todo.py` | queue a todo, or `--work` the queue | `--work` only |

## The Star Trek Bridge

Bridge agent and workspace `machine-supervisor` (`lancedb_memory/agent_bridge_worker/
machine_supervisor_agent.py`), off until `~/.config/machine_supervisor/bridge_enabled` exists on the
worker's machine. Prompt JSON: `{"supervisor": "machines"|"status"|"mailbox"|"read"}` as `inspect`,
`{"supervisor": "todo", "machine": ..., "text": ...}` as `implement`. A todo task succeeds when the todo is
*queued*, so its result carries `details.handoff` (the `read` that proves it done) and the gateway's
`RequestWatch` keeps a request's deliverable `submitted` until a later `read` finds the todo in `completed`
(met) or `failed` (failed). Before 2026-11-27 a request closed the moment its todo was queued
(rq_0fc36ccf500810416e5ac792). Gateway registry entry, to add
to `bridge.php` and seed with `bin/migrate.php`:

    ['name' => 'machine-supervisor', 'display_name' => 'Machine Supervisors',
     'description' => 'Read the BATI, SQMC and R46 supervisors\' mailboxes and status; give one a todo.',
     'operations' => ['inspect', 'implement'], 'workspaces' => ['machine-supervisor'],
     'write_workspaces' => ['machine-supervisor']]

## Data

`~/.local/share/machine_supervisor/`: `run.log`, `background.log`, `cron.log`, `watch_state.json`,
`incidents/`, `mail_outbox/`, `handoffs/`, `locks/`. `MACHINE_SUPERVISOR_ROOT`, `_MACHINES` and
`_SETTINGS` move the three inputs, for tests and trials.

## Tests

    cd adws && uv run --quiet --with pydantic --with pytest pytest -q machine_supervisor/tests
    cd lancedb_memory && .venv/bin/python -m agent_bridge_worker.tests.test_machine_supervisor_agent

All offline: no model, no SSH, no mail.
