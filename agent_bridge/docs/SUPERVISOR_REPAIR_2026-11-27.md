# Machine Supervisor and agent registry: investigation and repair, 2026-11-27

All times are UTC. Every claim below has live evidence: a task id, a request id, or a log line on the machine named.

## Status

| What | State |
|---|---|
| Supervisor assignment path (Bridge → worker → supervisor todo → completion) | **Working, verified live** on SQMC and BATI |
| Automatic recovery of an interrupted todo | **Working, verified live** (drill 1) |
| Request closes only on real completion, not on "queued" | **Working, verified live** (drill 2) |
| Duplicate prevention (one Bridge task files one todo) | **Working, verified live** on BATI |
| Dad's enrollment | **Blocked on EG**: an SSH key on Dad's machine (see Blockers) |

## §1 The three items

| Item | What it was | Did it run? | Changed / tested | State | Owner |
|---|---|---|---|---|---|
| `t_376042e69e3ba95cfdb8720a` | Supervisor todo for Dad's enrollment | Refused by the worker in 9 s | nothing | failed (malformed) | OAuth ChatGPT client |
| `t_a517848668c108258da81c03` | Codex Writer repair, `agent_blocks_dev` | Ran 14:51:51–14:53:22 | `write outcome no_changes, 0 path(s) changed`, no tests | succeeded with no work, superseded | — |
| `t_ee450a282bbc40f602fdf6fb` | Earlier Opus repair (from the plan) | not running; never deployed | partial edits in `agent_blocks_dev` only | abandoned, superseded | — |
| `rq_9687210f337e8999aa7de22d` | Dad's supervised enrollment | open / blocked since 14:53:24 | — | blocked, escalated 15:03:32 | OAuth ChatGPT client (per-client scoping: no other client can update it) |

## §2 Root causes

1. **The rejection was a malformed request, not a defect.** ChatGPT sent
   `{"supervisor":"todo","machine":"SQMC","todo":"..."}`; the schema is `text`, not `todo`. The registry, routing and
   worker were correct, and the refusal was correct.
2. **Why the watchdog "detected but did not recover":** the refusal said only `todo does not take: todo` and the
   gateway's alert said "Tell EG exactly what is needed". Nothing could correct the request without a person reading
   the source. Fixed: the refusal names the keys and an example prompt (7c8764a); the gateway alert says how to
   correct it and to resend once (966de63).
3. **An orphaned todo was failed, not resumed.** If the supervisor process died mid-todo, the message was failed.
   Fixed: it is resumed once (`MAX_RESUMES=1`), then failed with diagnostics (fadc6e2).
4. **Found by drill 1: a queued todo closed the request.** The Bridge task "succeeded" when the todo was *queued*,
   and RequestWatch counted that as completion. Fixed in 31e77b1: the todo's result carries the read that proves it
   done (`details.handoff`), and the gateway marks the deliverable met only when a read finds the message in
   `completed` (failed if `failed`; otherwise submitted, with the read to make in `why`).
5. **Also in 31e77b1:** a second post of the same Bridge task files nothing and names the first todo.

## §3–§4 Supervision and recovery: what the live drills show

**Drill 1, `rq_0fc36ccf500810416e5ac792`, recovery:**

| Time | Event |
|---|---|
| 15:10:42 | malformed todo refused; the alert names the fix |
| 15:11:42 | corrected todo handed over |
| 15:12:07 | worker killed on purpose |
| 15:14:02 | todo resumed and done; no duplicate |

**Drill 2, `rq_61bad09926f78394cafa495e`, completion is not "queued":**

| Time | Event |
|---|---|
| 15:26:46 | todo task `t_12d8bff197df9a1f1771cb30` queued; request stays **open**, deliverable `submitted` |
| 15:28:07 | read `t_95002cf3c922012ac8a52696` finds it in `in_process`; request stays open |
| 15:32:01 | supervisor finishes the todo |
| 15:32:17 | read `t_6407dc08718d99949a80f80d` finds it in `completed`; request auto-closes `completed` with the result as evidence |

**Dedupe, BATI:** `adw_machine_todo.py --task-id t_dedupe_drill_20261127` was posted twice. The first post queued
`20261127T153237Z_1994d9ac_from_operator`; the second answered "already filed … not filed again". There was one
envelope, done at 15:33:09.

**Thresholds (honest):** the gateway's not-started alert fires at 30 s, not the brief's 10 s; in the drills, refusals
surfaced in about 5–9 s. With no progress, the gateway records `no_progress` at 2 min, a warning at 5 min, and escalated plus timed_out at
10 min (rq_9687's events: 121 s, 308 s, 608 s). A heartbeat
or lease renewal does not count as progress; task events and handoff reads do.

## Files changed

| Commit | Files |
|---|---|
| 7c8764a | `lancedb_memory/agent_bridge_worker/machine_supervisor_agent.py`, its test |
| 966de63 | `agent_bridge/gateway/src/RequestWatch.php`, `tests/request_tests.php` |
| fadc6e2 | `adws/machine_supervisor/` (resume-once in the todo worker and watch), tests, `DESIGN.md` |
| 31e77b1 | `machine_supervisor_agent.py` + test, `RequestWatch.php` + test, `adws/machine_supervisor/operations/{receive,todo}.py`, `tests/test_handoffs.py`, `DESIGN.md` |

## Tests

| Suite | Result |
|---|---|
| `agent_bridge_worker` `test_machine_supervisor_agent` | 10 passed, 0 failed |
| every other `agent_bridge_worker` test module | all passed |
| `adws/machine_supervisor/tests` | 79 passed, 0 failed |
| bati_desktop + deploy + machine_supervisor under pytest | 28 passed |
| gateway `php tests/run_tests.php` (host temp copy) | 209 passed, 0 failed |
| skipped | none |

Live drills, not mocks, are the acceptance evidence above.

## Deployment

| Where | How | Evidence |
|---|---|---|
| Gateway | `RequestWatch.php` copied; `php -l` passed; sha256 checked | backup `~/agent_bridge_backups/RequestWatch.php.966de63` |
| SQMC | deploy ADW, adw `911bebbe` | release `20261127T102546-911bebbe`; health passed |
| BATI | deploy ADW | release `20261127T102622-77c4b809`; health passed |
| R46 | fast-forward to 31e77b1 | — |

No rollback was needed.

## Dad's enrollment

The work was handed to the SQMC supervisor under its own request, `rq_007918d59d9e8f64b838b2ee`, because rq_9687 can't
be linked from another client. Todo task `t_35a9600290332d8bb34671b9` ran; read `t_c9abec3f3049164699eac0f9` closed
the request at 15:36:43 with the supervisor's finding:

- dadscomputer, 100.100.229.107, is online on the tailnet (ping 6 ms) and TCP 22 answers;
- SSH as `adamsl` and as `dad` is refused (`publickey,password`), and so it is from BATI, R46 and the dev box;
- Tailscale SSH is not enabled.

## Blockers

| Blocker | Owner | Exact step |
|---|---|---|
| No key accepted on Dad's machine | EG | On dadscomputer, append SQMC's public key to the chosen user's `~/.ssh/authorized_keys`, and say which user: `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJu4jIb5347YGP4FwD9xaFEERPdjBoNBUYJdiCzh3a5G adamsl@DESKTOP-2OBSQMC`. (Tailscale SSH, `sudo tailscale set --ssh`, is the alternative, but the default tailnet policy's `check` mode re-prompts in a browser, which is not hands-free.) |
| rq_9687 is still open / blocked | OAuth ChatGPT client | `request_update` on rq_9687 pointing at rq_007918d59d9e8f64b838b2ee and blocked on EG's key; do not resubmit the todo. |
| SQMC `wsl-interop` failing; wake limit reached | EG | `wsl --shutdown` on SQMC (unrelated to this repair; HIGH PRIORITY mail sent 10:05 EST). |

## Next operation

After EG adds the key, send one supervisor todo to SQMC under a new request, for plan items 17 and 19:

```json
{"supervisor": "todo", "machine": "SQMC", "text": "Dad enrollment step 2: ..."}
```

The todo should verify the hostname and user, check that Tailscale and sshd are enabled at boot, and look at the
`~/agent_blocks` clone and branch, read-only. Close it with a read, never by resending the todo.
