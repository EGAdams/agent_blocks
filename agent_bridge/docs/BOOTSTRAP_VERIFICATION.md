# Bootstrap verification — 2026-11-06

Completed on DESKTOP-2OBSQMC. ChatGPT and the running Toyota service both
passed live bridge tests under separate authenticated identities.

## Live tasks

| Client / purpose | Task ID | Actual outcome |
|---|---|---|
| ChatGPT / Machine Ops status | `t_adbe09a223bac08418a3bb8d` | succeeded; supervisor active, PID 952892 |
| ChatGPT / named test | `t_e4ed83b1c0522cb564402061` | succeeded; real `post-office-operations` output, exit 0, 6 passed |
| Toyota / Machine Ops status | `t_6246290597763dfb492f5223` | succeeded; independently submitted and retrieved by Toyota |
| Toyota / Object Debugger evidence | `t_4b1ae7fbffbd8e7164c94cb0` | succeeded; all 11 phases of Toyota's status task, no missing evidence |
| ChatGPT / isolated development write | `t_925b1a6dc4e4d36e722e9191` | succeeded; exactly one requested development file; trusted 14-test gate passed |
| Toyota / idempotency and cancel | `t_10af5942f67ea598a0090108` | same ID returned on replay; cancelled before claim, attempts 0 |
| ChatGPT / arbitrary command rejection | `t_08fb7d39b5740ee22386812b` | expected failure; no command executed |
| ChatGPT / restart rejection | `t_1d1d785417167ac988279685` | expected failure; no restart executed |

Every successful executed task above completed on its first attempt. The two
negative Machine Ops tasks failed intentionally. The original failed supervisor
task `t_cb29dcb55654efa68f29fee9` was never replayed.

Toyota's live `/turn` request submitted her Machine Ops task in 1.48 seconds
without waiting for execution. Her Object Debugger submission took 1.08 seconds.
The existing completion inbox announced the finished task on the next turn.
Her subsequent `result` and `events` turns returned the correct IDs, real
results and lifecycle states. Full bounded evidence was retrieved separately
with her own client credential, without a model or a prompt-forwarding step.

The gateway's task rows prove ownership:

- ChatGPT: authenticated OAuth principal `g_ae4749291c8a4223421a6d64`.
- Toyota: separate client principal `k_b0547118960a314b9d966297`.

Toyota's lifecycle contains `task.sent (toyota-bridge)`. Object Debugger
correlated task `t_6246290597763dfb492f5223`, its ADW run `02fa0334`, all 11
phases through `finalize_task`, and Toyota's request/turn
`bootstrap_machine_ops_20261106`. It returned `missing: []`.

## Real Machine Ops output

Sanitized service-status excerpt:

```text
Id=adw-supervisor.service
LoadState=loaded
ActiveState=active
SubState=running
MainPID=952892
Result=success
NRestarts=0
ExecMainStatus=0
```

The actual result also includes the approved cgroup property, `returncode: 0`,
`timed_out: false`, `truncated: false`, and `restart_enabled: false`.
The named-test result reports `6 passed` and exit 0. Some long test-name lines
are conservatively redacted by the output filter; the count and remaining
real output are preserved. Direct execution through the same executor also
verified bounded supervisor journal inspection and a dashboard health probe
returning `HTTP 200`.

## Denial checks

- A separately authenticated, unapproved client received HTTP 403 for Machine
  Ops, Object Debugger and development writes, despite sender/approval claims
  in its prompt. Its temporary probe credential was immediately revoked.
- Toyota received HTTP 404 for a ChatGPT-owned task and HTTP 403 for an
  `implement` request containing a voice-confirmation claim.
- ChatGPT's attempted production write in `agent_blocks` was rejected by
  gateway policy without queueing a task.
- Arbitrary commands and restart/confirmation fields were rejected by the
  live Machine Ops parser. Unit tests additionally reject paths, arbitrary
  Python/SQL, duplicate JSON fields, oversized input and out-of-range limits.

The voice confirmation/read-back tests remain green. Toyota's independent
bridge credential stays read-only; a yes does not enlarge its gateway scopes.

## Services and continuity

| User service | Old PID | Final PID | Final status |
|---|---:|---:|---|
| agent-bridge-worker | 892743 | 1052505 | active; controlled reload |
| toyota-receptionist | 574 | 1052793 | active; controlled idle restart |
| dashboard-server | 978832 | 978832 | active; unchanged |
| agent-blocks-server | 14906 | 14906 | active; unchanged |
| adw-supervisor | 952892 | 952892 | active; unchanged |
| object-inspector | 578 | 578 | active; unchanged |

No active Executor or receptionist work was interrupted. A newly arriving
bridge task was allowed to finish before activation. Preflight checked the
gateway queue, local task states, cgroup process ownership, active Toyota
HTTP connections and receptionist turn telemetry. Toyota had no active turn
or established connection at her restart. No other service or unit changed.

Documentation PID 14906 remains in
`/user.slice/user-1000.slice/user@1000.service/app.slice/agent-blocks-server.service`.
There was no documentation restart, port cutover, Chrome disconnection or
dashboard restart during this bootstrap.

Final dashboard, Worker Activity, documentation, Toyota health and Object
Debugger health requests all returned HTTP 200. Supervisor heartbeat advanced
from epoch `1793995002.8183014` to `1793995204.3560412`
(`2026-11-06T20:00:04.356041+00:00`). State was available and fresh.
`coverage_complete` remained **false**, with this reason:

> Gateway-only queued tasks are not visible; full queue health is unknown.

The cancelled-before-claim gateway task is deliberately absent from local
post-office telemetry. No continuous remote queue adapter was added. Model
registry entries continue to report `runtime-unverified`; this bootstrap did
not convert one successful coding invocation into general readiness claims.

## Checks actually run

**822 distinct offline tests passed** (repeat executions are not counted twice):

| Scope | Tests |
|---|---:|
| Existing agent_comms, agent_bridge_worker and receptionist_team suites, 62 modules | 609 |
| New Machine Ops and lifecycle monitor tests | 14 |
| Deployment safety tests | 6 |
| Memory suites | 19 |
| Object trace suite | 21 |
| PHP gateway suite, also executed on the real PHP host | 103 |
| Original staged suite | 50 |

The shipped polling-file test had a stale expectation that deployment settings
equal library defaults. It now checks the existing configured 60/10-second
polling and 08:00–23:00 active hours. Runtime settings were preserved. Focused
suites were rerun after the repairs. Diff whitespace checks passed.

The live development task created only
`/home/adamsl/bridge_workspaces/agent_blocks_dev/docs/BRIDGE_BOOTSTRAP_ACCEPTANCE.md`
(48 bytes). The full result contains its exact diff, complete artifact, and
`checks_run` with exit 0 and 14 passed. The coding agent's prose says it ran
no checks itself; the bridge's trusted post-write checker ran them afterward.
That file remains in development and was not deployed to production. The real
deployment planner displays exactly this development-only change for review.

## Changed files and deployment records

The [exact file manifest](BOOTSTRAP_CHANGED_FILES.json) lists all 45 source,
test and documentation paths edited for this integration, the two private
configuration paths, external gateway files and generated artifacts. Several
files already contained unrelated changes; those were preserved. The separate
development source snapshot is enumerated by
`~/.cache/agent_bridge/development_baseline.json`. It has no production Git
history or remote, no copied credentials and no new commits.

The external gateway now registers nine agents:
`claude-sonnet`, `codex`, `codex-writer`, `hello-world`, `machine-ops`, `notes`,
`object-debugger`, `printer`, `scanner`.
Both coding agents advertise `agent_blocks_dev` as writable; production
`agent_blocks` remains read-only. Machine Ops and Object Debugger have no
write workspaces. The local registry additionally contains Toyota's sender
card, with runtime `none` and no execution capabilities.

Remote source/configuration and a consistent database backup are retained at
`/home1/tinman72/agent_bridge_backups/bootstrap_20261106/`.
All pre-existing gateway source hashes were checked before replacement;
unrelated configuration was compared and preserved. Configuration remains
mode 0600. Provider credentials and existing ChatGPT/worker tokens were not
changed. Toyota's new client token was stored mode 0600 and never printed.

Sanitized detailed evidence is retained at `/tmp/bridge-bootstrap-20261106/`,
including `connector-results.json`, `authenticated-ownership-evidence.json`,
Toyota service-turn responses, full own-identity results, denial evidence,
test logs/counts, development gate output and final service state.

## Remaining boundaries

There are no blockers to the verified bootstrap. Production deployment still
requires a reviewed diff and the trusted local gate; protected configuration
and infrastructure changes need separate local review. Machine Ops restarts
remain disabled. Toyota's bridge identity is read-only, and pending voice
confirmations/notices remain process-local. A new OAuth connection requires
an explicit identity binding. Remote gateway queue coverage and model registry
runtime probes remain separate work; coverage is not reported complete.

No commit or push was performed. Copy-ready instructions are in
[BOOTSTRAP_CHATGPT.md](BOOTSTRAP_CHATGPT.md); operating details are in
[BOOTSTRAP_INTEGRATION.md](BOOTSTRAP_INTEGRATION.md).
