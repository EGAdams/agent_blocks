# Object Debugger bridge agent

Implemented and deployed on 2026-11-06 with user approval. Star Trek Bridge
`list_available_agents` lists `object-debugger`, and all three request types
completed successfully through the actual connector. Paste-ready instructions
are in [OBJECT_DEBUGGER_CHATGPT.md](OBJECT_DEBUGGER_CHATGPT.md).

## Behavior and rationale

The telemetry and HTTP debugger already existed, but the bridge had no agent
that exposed their read model. The gateway accepts `inspect`, so `status`,
`recent [limit]`, and `task <id>` are classified from the request prompt under
that operation. No gateway protocol change or inbound tunnel is needed.

`ObjectDebuggerExecutor` reuses `object_trace.inspector.Inspector`, the
existing SQLite adapter in `read_only=True` mode, the existing SSSF reader,
`SystemClock`, and `source_build()`. A missing database reports an unavailable
source and is never created. The caller cannot choose a database, directory,
command, or SQL statement. Only `chatgpt-bridge` may request `inspect` work.

Status includes counts, schema/build, latest sequence/time, and freshness.
An empty source is distinguished from a stale source; stale means the last
recorded event is older than 24 hours. This describes recorded activity,
not proof that every object remains alive. Task answers retain the
inspector's evidence and missing-information list.

Recent requests default to 50 events and cap the requested count at 200.
The cursor starts near the latest sequence, so responses show recent events.
Responses contain at most 24,000 UTF-8 bytes of text, including the JSON
details carried in the bridge reply body. Older events are omitted with
`truncated: true` when necessary. Oversized non-event responses are withheld.

`SecretScanner` scans text and details before output trimming. JSON leaf
values are also scanned as assignments so quoted credential field names do
not hide assigned secrets. A match withholds the whole response. Exceptions
return a fixed unavailable-source message rather than exposing exception text.

One extra integration fix was necessary beyond the original handoff's four
composition files: `RepeatAnswers` normally reuses identical read requests
for 15 minutes. It now bypasses that cache for `object-debugger`, including
existing cached entries, so successive requests inspect current telemetry.

## Changed files

- `lancedb_memory/agent_comms/executors/object_debugger.py`: executor.
- `lancedb_memory/agent_comms/factory.py`: runtime builder.
- `lancedb_memory/agent_comms/agents.json`: agent card and sender restriction.
- `lancedb_memory/agent_bridge_worker/workspaces.json`: read-only `object_trace` workspace.
- `lancedb_memory/agent_bridge_worker/repeat_answers.py`: live telemetry cache bypass.
- `lancedb_memory/agent_comms/tests/test_object_debugger.py`: 12 focused tests.
- `lancedb_memory/agent_bridge_worker/tests/test_repeat_answers.py`: cache bypass regression.
- This report and the progress note in `HANDOFF.md`.

## Verification

All 114 tests passed using `lancedb_memory/.venv/bin/python` from
`lancedb_memory/`:

| Module | Passed |
| --- | ---: |
| `agent_comms.tests.test_object_debugger` | 12 |
| `agent_comms.tests.test_post_office` | 24 |
| `agent_comms.tests.test_operations` | 6 |
| `agent_comms.tests.test_alerts` | 8 |
| `object_trace.tests.test_object_trace` | 20 |
| `agent_bridge_worker.tests.test_bridge_worker` | 17 |
| `agent_bridge_worker.tests.test_traced` | 5 |
| `agent_bridge_worker.tests.test_repeat_answers` | 5 |
| `agent_bridge_worker.tests.test_write_access` | 17 |

The focused tests cover unavailable/stale/empty sources, malformed task IDs,
unauthorized senders, unsupported operations, real read-only SQLite access,
secret withholding in health/task/event results, JSON credential fields,
output limits, and registry/policy/workspace/post-office integration.
`git diff --check` also passed.

Live verification:

- The handoff task `t_19133b18bee29034b4be59c7` remains resolvable.
- A harmless `hello-world` inspect task submitted through the actual Star
  Trek Bridge completed: `t_d496bd4b1819fb4bea696e97`.
- Real detached post-office workers executed the new agent's `status`,
  `recent 3`, and `task t_d496bd4b1819fb4bea696e97` requests against the live
  trace database, using isolated temporary mailboxes/logs. The task answer
  included 17 evidence records and all 11 worker phases.
- The `agent-bridge-worker` user service was restarted and is active.
  Factory Ops :8792, mcp-proxy :8791, and dashboard :8765 retained their
  original processes. No files in `the-factory` or the documentation SPA
  were changed. Unrelated worktree changes and live trace files were preserved.

Sample sanitized task response (first line):

```text
Bridge task t_d496bd4b1819fb4bea696e97 attempt 1 ran every phase through finalize_task. The task itself is succeeded (finished 2m ago), gateway state succeeded: Hello World.
```

The response also explicitly reports a missing Toyota submission record;
this task came through ChatGPT, so the inspector does not invent one.

## Remote registration and rollback

Host: `tinman72@americansjewelry.com`, **SSH port 2222**. Port 22 closes the
connection. Read-only inspection confirmed seven existing registered agents.
The live config is `/home1/tinman72/agent_bridge_data/bridge.php`.

The approved entry was added to its `agents` array; all existing configuration
was compared against the backup and confirmed preserved:

```php
[
    'name' => 'object-debugger',
    'display_name' => 'Object Debugger',
    'description' => 'Read-only live ADW object lifecycle: inspect with status, recent [1-200], or task t_<24 lowercase hex digits>.',
    'operations' => ['inspect'],
    'workspaces' => ['object_trace'],
    'write_workspaces' => [],
],
```

Config and consistent SQLite backups are outside the web root at
`/home1/tinman72/agent_bridge_backups/object_debugger_20261106_165517/`.
The candidate passed PHP lint and a full configuration comparison before
atomic replacement; config permissions remain `0600`. The deployed gateway's
`bin/migrate.php` seeded eight agents with no new schema migrations.
The deployed config SHA-256 is
`456d4ccedba6b5fc23423d800a4b3f4f42b7a30fef8c5f04e717e1a8aee0d787`.

Actual connector checks passed:

- Discovery: `operations: ["inspect"]`, `workspaces: ["object_trace"]`, no write workspaces.
- `status`: `t_70bf62459a452eebf62b147c`, succeeded; fresh source, 1,839 objects, 5,951 events at that observation.
- `recent 5`: `t_b3cc52dfba8e79382d131ab1`, succeeded; five real lifecycle events through sequence 6,037.
- `task t_d496bd4b1819fb4bea696e97`: `t_5099415f5ecdb3f976bce7af`, succeeded; 17 evidence records, all 11 phases, gateway state succeeded.
- `implement`: refused by the gateway with `Object Debugger does not accept implement`; no task queued.
- Repeated `status`: `t_019610952c064e344807a526`, succeeded with a newer observation time and event sequence than the first identical prompt; the cached answer was not reused.

These are snapshots taken during verification; subsequent requests reread
the source and may report different counts or task ages.

Rollback registration by restoring the backed-up config and reseeding the
registry. The local read-only card/workspace can remain inert, or be removed
along with the builder if removing the feature entirely.

Remote registration and the new agent's direct ChatGPT round trip are
verified. No commit or push has been performed for this work.
