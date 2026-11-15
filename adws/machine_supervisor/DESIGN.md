# Machine Supervisor

One supervisor per machine (BATI, SQMC, R46). She sleeps. A cron ADW with no model in it looks at the
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
`{"supervisor": "todo", "machine": ..., "text": ...}` as `implement`. Gateway registry entry, to add
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
