# Deployment ADW

`adws/adw_deploy.py` deploys, restarts, health-checks, reads the logs of, and rolls back a **fixed list of
projects** on EG's machine. ChatGPT reaches it over MCP (`submit_agent_task`, agent `deploy`) and Toyota
through her `bridge_deploy` route; both land on the same Bridge worker adapter
(`lancedb_memory/agent_bridge_worker/deploy_agent.py`), which runs this ADW once per task.

```
ChatGPT ─MCP─┐                                 ┌─ agent_bridge_worker/deploy_agent.py   (checks the request table)
Toyota ──────┴─> Bridge gateway (protected) ──>│     └ adapters/deploy_process.py      (uv run adw_deploy.py ...)
                                               └──> adws/adw_deploy.py                  (one ADW id per run)
                                                     └ deployment/kit.py  -> DeploymentRunner -> OPERATION_TABLE
```

## Operations

| operation | lane | what it does |
|---|---|---|
| `projects` | inspect | the table: each project's source, units, checks and health checks |
| `status` | inspect | what is live, what is waiting (changed files / commits behind), which units are down |
| `health` | inspect | runs the project's health checks now |
| `logs` | inspect | the tail of one unit's journal or one configured log file, credentials masked |
| `history` | inspect | recent runs from the ledger and recent releases, with their ids |
| `deploy` | implement | checks → record a release → make live → restart if needed → health; on failed health, put the previous release back by itself |
| `restart` | implement | restart the project's units (or one), then health |
| `rollback` | implement | save what is live (`<id>-pre`), restore a release (default: the one before), restart, health; the answer says how to undo it |

`deploy` and `rollback` need a reason unless `--dry-run` is given. A dry run changes nothing.

## What a request can and cannot name

A request names an **operation and a project**, plus optionally a release id, one unit, a line count and a
reason. Paths, commands, units and health checks live only in `projects.json`. The worker refuses unknown
fields and bad values; the ADW refuses a unit or log not in that project's lists. So nothing on the Bridge can
make this run an arbitrary command -- adding a project or a check is an edit to `projects.json`, reviewed and
committed like any other code.

## Projects (`projects.json`)

- `dashboard` -- `in_place` over `~/letta-code` (`dashboard/` only). The dashboard serves its tree directly,
  so "deploy" means: run the bun suite and a `py_compile`, record the files' hashes as a release, rewrite the
  changed CSS/JS `?v=` tags in `dashboard.html` (`?v=adw<adw_id>`), restart `dashboard-server` only when a
  `.py` changed, then check the page answers.
- `agent_blocks` -- `git_pull` of `origin/main`: fast-forward only, never a merge, never a discarded local
  edit. Restarts the receptionists and Cashy; the two Bridge workers are `self_units`, restarted ~8 s later by
  a detached `systemd-run` so the task that asked for it can answer first.
- `bridge_sandbox` -- `in_place`, the coding agents' practice tree.
- `gateway` -- `source: none`; health only (the host is deployed by hand over ssh).

## Releases and rollback

`in_place` releases are content-addressed: every in-scope file's bytes are kept by sha256 under
`$ADW_DEPLOY_HOME` (default `~/.local/share/adw_deploy`), so a rollback restores exact bytes, including files
the deploy added (removed) or deleted (restored). `git_pull` releases record the commit, and a rollback is
`git reset --keep` to it, which refuses rather than overwrite a local edit. The **first** `in_place` deploy of a
project has nothing to compare with, so it records the current tree as the baseline and busts no cache.

## Traceability

Every run gets an 8-hex ADW id, printed first on stdout. It writes:

- `runs/<adw_id>.json` -- the request, the answer and every step with what it found;
- one line in `ledger.jsonl` -- who (Bridge task id and sender), what, which release, ok/verified;
- releases under `releases/<project>/`, each naming its ADW id, task id, reason and the release before it.

`verified` means the ADW checked the result itself (health passed), never that something was queued.

## Interfaces

`interfaces.py` has one Protocol per responsibility (`IProjectCatalog`, `ISourceTree`, `ISnapshotter`,
`IReleaseStore`, `IServiceManager`, `IHealthProbe`, `ILogReader`, `ICheckRunner`, `ILedger`, `ITrace`,
`IProjectLock`, `IClock`). Operations see only those, through `operations/common.Deps`; `kit.py` is the only
module that names a concrete class. One operation is one class in `operations/`, registered in
`operations/catalog.OPERATION_TABLE` -- adding one is a class and a line there, plus the worker's READS/WRITES
and a Plan in Toyota's `bridge_client/deploy_plans.py`.

## Enabling it on a machine

The worker registers the agent only when `~/.config/startrek_deploy/enabled` exists. The gateway lists
`deploy` in `protected_agents`, so only client identities bound to a sender in `client_scopes`
(`toyota-bridge`, `chatgpt-bridge`) can submit to it.

## Tests

```bash
cd adws && uv run --quiet --with pydantic --with pytest pytest -q -p no:cacheprovider deployment/tests
cd lancedb_memory && .venv/bin/python -B -m pytest -q agent_bridge_worker/tests/test_deploy_agent.py
cd lancedb_memory && .venv/bin/python -m receptionist_team.tests.test_bridge_deploy_route
```

The ADW's tests use real git, real files and the real release store, with fake services, health, checks and
clock -- so deploy, auto-rollback, rollback-and-undo and the git fast-forward/reset paths run for real.
