# bridge_scala_storage

Toyota's Groq specialist for the Bridge agent `scala-storage`. The worker side is
`agent_bridge_worker/scala_storage_agent.py`; each task becomes one run of `adws/adw_scala_storage.py`
(design and machines in `agent_bridge/docs/SCALA_STORAGE.md`), with its own ADW id, run record and audit line.

This is the **same** Bridge agent, worker adapter and ADW that ChatGPT drives over MCP. Toyota gains no
filing path of her own -- only a second caller.

## The operations

Reads (`inspect`, no yes needed): `targets`, `folders`, `health`, `verify`, `retrieve`, `status`.
Change (`implement`, read back, sent only after a yes): `store`.

## What code decides, never the model

- The operation's lane: a read is `inspect`, a store is `implement` (`bridge_client/storage_plans.py`).
- A store is always a copy. `move` and `delete` are answered in words and never sent.
- A request names a scan id (or `last`), a machine and a folder. A folder with `..`, `~` or a backslash is
  refused here; the worker, the ADW and the helper on the destination re-check the approved list, the
  machine's hostname, symlinks and the SHA-256.
- The gateway lists `scala-storage` as a protected agent: only client identities bound to a sender may submit.
