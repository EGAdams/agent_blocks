# bridge_deploy

Toyota's Groq specialist for the Bridge agent `deploy`. The worker side is
`agent_bridge_worker/deploy_agent.py`; each task becomes one run of the Deployment ADW
(`adws/adw_deploy.py`, design in `adws/deployment/DESIGN.md`), with its own ADW id, run record and ledger line.

This is the **same** Bridge agent, worker adapter and ADW that ChatGPT drives over MCP. Toyota gains no
deployment path of her own -- only a second caller.

## The operations

Reads (`inspect`, no yes needed): `projects`, `status`, `health`, `logs`, `history`.
Changes (`implement`, read back, sent only after a yes): `deploy`, `restart`, `rollback`.

## What code decides, never the model

- The operation's lane: a read is `inspect`, a change is `implement`.
- A deploy or rollback carries a reason, unless it is a dry run.
- Projects, their checks, services and health checks live in the ADW's fixed table
  (`adws/deployment/projects.json`). A request names a project; it never names a path, a command or a
  service outside that table, and the worker and the ADW both re-check it.
- A deploy that fails its health check puts the previous release back by itself; a rollback saves what was
  live first, so it can be undone too.
- The gateway lists `deploy` as a protected agent: only client identities bound to a sender may submit to it.
