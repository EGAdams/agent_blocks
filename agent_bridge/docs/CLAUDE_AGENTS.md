# Claude Sonnet and Claude Opus on the Bridge

Two coding agents, one executor (`agent_comms/executors/claude_cli.py`, `ClaudeCliExecutor`), two cards in
`agent_comms/agents.json`. Registered 2026-11-26. Both run on SQMC under `agent-bridge-worker`.

| Bridge agent | Model id (pinned, verified) | CLAUDE_CONFIG_DIR | Operations |
|---|---|---|---|
| `claude-sonnet` | `claude-sonnet-5` | `~/.claude-bridge` | inspect, explain, search_memory (read-only); implement (writes, shell) |
| `claude-opus`   | `claude-opus-5`   | `~/.claude-bridge-opus` | same |

Workspaces: read anywhere the card lists; write only in `bridge_sandbox`, `letta_code`, `digi` and the isolated
`agent_blocks_dev` checkout. Production `agent_blocks` is read-only. Senders accepted: `chatgpt-bridge`,
`toyota-bridge`, `sydney-bridge`.

## Model identifiers -- measured, not assumed (Claude Code 2.1.211 on SQMC)

| `--model` | what actually served the turn |
|---|---|
| `claude-sonnet-5` | `claude-sonnet-5` |
| `claude-sonnet-5-5` | `claude-sonnet-5-5` (works; not used by the card) |
| `sonnet` (alias) | `claude-sonnet-5` |
| `claude-opus-5` | `claude-opus-5` |
| `claude-opus-5-5` | **refused**: "Claude Code 2.1.211 does not support this model; version 2.1.280 or newer is required" |
| `opus` (alias) | **`claude-opus-4-8`** -- an alias is a silent substitution, so no card uses one |

To move Opus to 5.5, upgrade the CLI (`sudo npm i -g @anthropic-ai/claude-code`, needs root on SQMC) and change
the card's `model`. Nothing else changes. The post-office `opus` card (Claude Agent SDK, orchestrator-facing,
unjailed) already runs `claude-opus-5-5`, because the SDK carries its own newer CLI.

The model is **confirmed per task**: the CLI reports `modelUsage`, and a card naming a full `claude-*` id fails the
task as `model_mismatch` when that id did not serve it. Every result carries `model_requested`, `models_used`,
`init_model`, `model_confirmed` (read results in `details`, write results in `details.runtime`).

## What the agents can do, and the boundary

Both are jailed (bubblewrap, `BwrapJail`): `/usr` + `/etc` read-only, the one workspace (writable for `implement`,
its `.git` read-only), private paths masked, environment cleared, an empty `$HOME`.

- Read-only operations: Read, Glob, Grep.
- `implement`: Read, Glob, Grep, Edit, Write **and Bash** (card setting `"shell": true`; readers never get it).
  They can create/modify files, run tests and scripts, write and run ADW scripts, read git history. They cannot
  commit, cannot see EG's other files, and have no web tools, sub-agents or MCP servers.
- After a write the worker runs the workspace's own checks (`VerifyChanges`) and reports them; deployment of a
  development change stays the separate, reviewed `agent_bridge/development/deploy.py`.

**Credential decision (EG asked for a shell).** The jail's network is open (Claude must reach Anthropic), so a shell
plus a readable login would be an exfiltration path -- and Claude's own Bash sandbox setting was measured to give no
isolation inside the jail. So the jail now sees a copy of the login **without its refresh token**
(`ILoginLifetime.write_jail_copy`, rewritten 0600 before every run under `~/.cache/claude_bridge/`). The real login
is never mounted. An exposed access token is good only until it expires, and `run_limit()` already caps every run to
the access token's remaining life. Residual risk: during a run, a prompt-injected shell could still send the access
token out. That is the same exposure `codex-writer` already has with its auth file; it is bounded, not eliminated.

## Progress and errors

Each tool call becomes a `task.progress` event (tool name and file only, never prompt or reply text), relayed to the
gateway and listed per task in the Worker Activity snapshot the dashboard reads. The Object Debugger traces the
task as `bridge.task_run:<task id>`; ask it with agent `object-debugger`, prompt `task t_...`.

## Acceptance evidence (2026-11-26, requester: the `chatgpt-bridge` client identity)

| Step | Result |
|---|---|
| Read a test file | both agents returned the file's exact line |
| Create + modify files, run commands | `adamsl / 42 / 45` written to disk by each agent, verified on disk afterwards |
| Create and run an ADW | four ADW runs (`53f69040`, `b8902759`, `a4ff570a`, `d35b2c11`), each result JSON on disk |
| Post Office | the Bridge path is itself a post-office round trip; orchestrator -> `sonnet` / `opus` cards answered 42 |
| Concurrency | two agents read at once; writers ran concurrently in different workspaces |
| No double execution | one key submitted twice at once -> one task (201 then 200 `replayed`), `attempts: 1`, one claim; re-submitting a finished write's key -> 409, file changed exactly once |
| Visibility | tasks appear in the Worker Activity snapshot and in the Object Debugger |

Known baseline failure: `agent_blocks_dev` is a stale snapshot, so its post-write gate reports three failing
`test_machine_ops` tests on any write there (the trusted tests under `/opt` are newer than the snapshot). The
agents' changes are not the cause; refreshing the dev checkout would overwrite the earlier partial Opus/Sheila work
kept there, so it was left alone.

## Rollback

Gateway: restore `~/agent_bridge_data/bridge.php` and `bridge.sqlite` from
`/home1/tinman72/agent_bridge_backups/pre_claude_opus_20261126_081655/`, then `php ~/agent_bridge/gateway/bin/migrate.php
~/agent_bridge_data/bridge.php`. Worker: `git revert` and redeploy with `adws/adw_deploy.py deploy.deploy --project agent_blocks`.
