# Scala storage — filing scans on BATI, R46 and SQMC

Scala copies a scanned document, by scan id, from the scanner API on SQMC into an approved folder on one
of three household machines, and finds and verifies it there afterwards. ChatGPT reaches it as Bridge agent
`scala-storage`; the ChatGPT browser relay is never used for files.

## The machines (verified by `hostname` over ssh, 2026-11-12)

| machine | hostname | root | reached from SQMC by |
|---|---|---|---|
| BATI | `DESKTOP-SHDBATI` | `/home/adamsl/file_cabinet` | `ssh adamsl@100.80.49.10` (port 22) |
| R46 (Mom's) | `Rosemary46` | `/mnt/c/Users/rbarn/file_cabinet` (a dedicated cabinet, created empty; nothing of Mom's touched) | `ssh -p 2222 adamsl@100.72.34.38` |
| SQMC | `DESKTOP-2OBSQMC` | `/mnt/c/Users/NewUser/scanned_documents` (the existing scan cabinet) | in place, no ssh |

Machines, aliases (`mom`, `rosemary46`, …), roots, ssh routes and each machine's folder allowlist are data in
`lancedb_memory/scala/storage/storage_targets.json` (`SCALA_STORAGE_TARGETS` overrides it). SQMC's allowlist
is `bills_511/*` and `documents_511/*` only: `rol_receipts` (Mazda's) and `inbox` (Scala's own) are not
writable through this path. No credentials anywhere — the existing ssh keys, with `BatchMode=yes`.

## Calling it

`submit_agent_task`, agent and workspace `scala-storage`, JSON prompt:

| operation | mode | fields |
|---|---|---|
| `targets` | inspect | — |
| `folders` | inspect | `machine` |
| `health` | inspect | `machine` (hostname, root writable, free space) |
| `verify` | inspect | `scan_id`, `machine`, `folder` |
| `retrieve` | inspect | `scan_id`, `machine` (copies it back to SQMC's `retrieved/`, hash-checked) |
| `status` | inspect | optional `scan_id`: every filing attempt, plus anything waiting for retry |
| `store` | implement | `scan_id` (or `last`), `machine`, `folder` |

`{"storage":"store","scan_id":"scan_01M787R1STEA7N30DM8PG13MSZ","machine":"BATI","folder":"insurance/home"}`.
A folder may be given as `insurance/home`, the full path under the root, or a unique leaf (`electric`). The same
operations run directly with `uv run adws/adw_scala_storage.py storage.<op> --machine=… --folder=… --scan-id=…`
on SQMC (the only machine holding the scanner token).

## What a store guarantees

1. The original's bytes come from the scanner API by scan id and must match the scanner's own size and SHA-256.
2. A helper (`scala/storage/remote_helper.py`, stdlib only, shipped base64 over the ssh command line) runs on
   the destination and refuses to do anything unless `socket.gethostname()` is the configured hostname.
3. Only an allowlisted folder, made of snake_case parts, with no symlink anywhere under the root; `..`, `~`,
   backslashes and absolute paths outside the root are refused before any machine is contacted.
4. Written to a hidden `.part`, fsynced, re-hashed, then hard-linked into place — never replacing a file. The
   same bytes already there is a verified `duplicate`; different bytes under the name is `name_conflict`.
5. Re-verified (size, SHA-256, hostname) after the copy, and the scanner's record re-read to prove the original
   is still there. A store is always a copy: `move` and `delete` do not exist.
6. If the destination cannot be reached, nothing is filed, the result is FAILED + retryable, and a staged copy is
   kept under `adws/adw_data/scala_storage/pending/` (used only if the scanner itself is down at retry time).

Names follow Scala's cabinet convention: `<capture date>_<folder leaf>_<sha256[:12]>.<ext>`.

## Records (SQMC, gitignored `adws/adw_data/scala_storage/`)

- `audit.jsonl` — one line per attempt: scan id, source, machine, hostname, folder, destination, SHA-256,
  size, result (`stored`, `duplicate`, `unreachable`, `refused:<code>`), ADW id, Bridge task id, client.
- `runs/<adw_id>.json` — the run's phases (`resolved → fetched → transferring → verifying → done`).

## Turning it on / off, rollback

- Worker side: `~/.config/scala_storage/enabled` on SQMC (remove it and restart the workers to turn it off).
- Gateway side: agent entry + `protected_agents` in `~/agent_bridge_data/bridge.php` on the host, then
  `php ~/agent_bridge/gateway/bin/migrate.php ~/agent_bridge_data/bridge.php`. Backups from the rollout:
  `bridge.php.bak-scala-20261112`, `bridge.sqlite.bak-scala-20261112`.
- Code: deployed by the Deployment ADW (`agent_blocks`); `deploy.rollback` puts the previous release back.

## Verified live (2026-11-12)

- Through ChatGPT's MCP path: `targets` (task `t_6e5031c62e42fcdbb6110e4b`), and the example `store` of
  `scan_01M787R1STEA7N30DM8PG13MSZ` to BATI (task `t_48c371b06bbb042d812fca28`, adw `cc60ce22`):
  `/home/adamsl/file_cabinet/insurance/home/2026-11-12_home_9e0e779533b6.jpg`, 823,350 bytes, SHA-256
  `9e0e7795…68ab`, re-checked with `sha256sum` on BATI; the scanner's original unchanged.
- Directly on SQMC: `health` on all three; stores to R46 (remote) and SQMC (local), then verify and retrieve
  (those two test copies were removed afterwards); duplicate; traversal, absolute, unapproved folder, unknown
  machine and move refused; wrong machine (R46 answering at a "BATI" address) refused `identity_mismatch`;
  offline address → retryable with a staged copy, then retried to a verified result; a real symlink on BATI
  refused with nothing written.
- Offline tests: `lancedb_memory/.venv/bin/python -m scala.tests.test_storage` and
  `-m agent_bridge_worker.tests.test_scala_storage_agent`.

Not built: Toyota has no `bridge_scala_storage` route yet; she can reach it only through a generic Bridge task.
