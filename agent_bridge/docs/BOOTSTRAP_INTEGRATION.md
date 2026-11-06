# Machine Ops, Toyota identity and development workspace

Machine Ops is an `ITaskExecutor`, composed by `factory.default_builders()`.
Bridge clients submit `inspect` in workspace `machine_ops`. The prompt must
be a strict JSON object, with no prose or extra fields:

```json
{"operation":"status","unit":"adw-supervisor.service"}
```

Other operations: `journal_tail` (optional integer `lines`, 1–200), `check`,
and `run_test` (required named `suite`: `post-office-operations` or
`object-debugger`). Approved units: `dashboard-server.service`,
`adw-supervisor.service`, `agent-bridge-worker.service`,
`agent-blocks-server.service`, `toyota-receptionist.service`.

```json
{"operation":"run_test","unit":"agent-bridge-worker.service","suite":"post-office-operations"}
```

Commands are fixed arrays with `shell=False`, a clean environment, deadlines,
bounded combined output, process-group cleanup, and credential redaction.
Status restricts properties to Id, LoadState, ActiveState, SubState, MainPID,
ControlGroup, Result, NRestarts, ExecMainStatus, ActiveEnterTimestamp.
HTTP probes discard response bodies. Restarts have no operation or execution
path. A prompt, voice confirmation or confirmation field cannot enable one.

## Authentication and Toyota

The gateway's `client_scopes` binds actual authenticated token/grant IDs to
`chatgpt-bridge` or `toyota-bridge`. Labels and prompt text grant no identity.
Unknown identities cannot use Machine Ops, Object Debugger or the development
workspace. Current ChatGPT grant and legacy client identities are explicitly
bound. A newly consented ChatGPT connection requires an explicit new binding.

Toyota uses a separately issued client credential, stored in a mode-0600
file outside both repositories. Its value is never in source or reports.
Since 2026-11-06 (EG's instruction) its gateway binding carries the same scope
as ChatGPT's: the live `client_scopes` entries for both identities set only the
`sender` and no operation, workspace or agent limits. Toyota's own safeguards
stay in force: her `star_trek_bridge` desk sends read-only requests only, and
every write goes through a specialist (`bridge_writer`, `bridge_coder`,
`bridge_printer`, `bridge_scanner`, `bridge_notes`) that reads the request back
and waits for a spoken "yes" first. `bridge_notes` lists and reads approved
notes, and updates them by exact find/replace against the revision she last
read; the Notes server's approved list and the worker's revision check still
decide what can change.

Toyota keeps the existing `BridgeClient`, `BridgeTaskWorkflow`,
`BridgeDelegationRunner`, `PENDING` and `NOTICES` infrastructure. The read-only
runner returns after submission, then completion notices refresh on a
background thread when alerts are polled. It does not sleep through worker
execution on the voice thread. At most one refresh runs; watches expire after
one hour and are bounded to 100. Status-derived event windows contain at most
50 events. Monitors suppress duplicate/old sequence numbers, preserve task IDs
and explicitly say when lifecycle events are unavailable. No new events
endpoint was added. Cancellation uses the existing owner-checked cancel API.
Ambiguous retries of the same request retain the submission idempotency key
within the runner's lifetime. A process restart loses that retry context;
read the prior task ID back instead of resubmitting unknown work.

Examples at Toyota's bridge desk:

```text
machine-ops@machine_ops: {"operation":"status","unit":"adw-supervisor.service"}
object-debugger@object_trace: status
status t_<24 lowercase hexadecimal digits>
result t_<24 lowercase hexadecimal digits>
cancel t_<24 lowercase hexadecimal digits>
```

## Isolated development and deployment

`agent_blocks` remains read-only. `agent_blocks_dev` names
`/home/adamsl/bridge_workspaces/agent_blocks_dev`, a separately initialized
Git worktree without production Git history, credentials or a production
remote. The initial source snapshot preserves current production work;
no commit was required. Approved coding agents (`codex-writer` and
`claude-sonnet`) can implement there in `workspace_write` mode, using the
existing workspace resolver, bubblewrap jail, write journal and change tracker.
The live checkout and credential/configuration directories are outside that
jail. Its own `.git` is read-only during coding execution.

Post-write checks run with no network, a read-only development checkout,
and trusted production tests/interpreter mounted read-only under `/opt`.
The gate runs Machine Ops and lifecycle-monitor regressions, checks Python
syntax in affected packages, and verifies production/configuration isolation.
Tests actually run and their output appear in the bridge's result details.
This focused gate does not prove all future changes correct: review the diff
and add relevant tests for each requested change.

Deployment is a separate local operation, unavailable through bridge tasks:

```bash
cd /home/adamsl/agent_blocks
lancedb_memory/.venv/bin/python -B agent_bridge/development/deploy.py plan
```

Review every displayed change and its digest, then run locally:

```bash
lancedb_memory/.venv/bin/python -B agent_bridge/development/deploy.py apply --reviewed-digest <reviewed-sha256>
```

The trusted deployer refuses configuration, credential paths, symlinks,
deletions, bridge infrastructure and unexpected production changes. It runs
the jailed gate again, verifies hashes, saves backups, and installs only the
reviewed source files. It never restarts services. A concurrent change during
a multiple-file deployment stops processing and reports the backup location;
inspect that manifest before retrying. Protected infrastructure changes need
separate local review and deployment. Source/deployment baselines live outside
the writable workspace under `~/.cache/agent_bridge/`.

To request future code changes, ChatGPT discovers agents, submits once to
`codex-writer` or `claude-sonnet`, workspace `agent_blocks_dev`, operation
`implement`, then polls the same task ID and retrieves its full result.
No manual forwarding of a coding prompt is needed. Production activation
still requires review and a safe service-specific maintenance window.

The ADW Supervisor continues to disclose `coverage_complete: false` because
gateway-only queued work is not part of its polling adapter. Reading the
gateway queue during this deployment does not implement continuous coverage.

See `BOOTSTRAP_VERIFICATION.md` for actual activation evidence and test counts.
