# Sheila on BATI -- operational report (mission "GET SHEILA FULLY OPERATIONAL", 2026-11-26)

Sheila is the **Pi Coding Agent CLI** (not a Raspberry Pi) on **BATI** (`DESKTOP-SHDBATI`). This file is the
mission's final report. The machine-readable record of all twenty acceptance tests is `SHEILA_ACCEPTANCE.json`
beside it, and `SHEILA_MISSION_CHECKLIST.md` is the persistent checklist. Both are read by Sheila's own `status`
operation, so a Bridge client can find them with `{"sheila":"status"}` on agent `sheila`, workspace `sheila`.

Times are UTC. Mission request: `rq_b0bf4a3307995bb6ac4e1b92` (client `eg-chatgpt`). Every acceptance task
names it (link `declared`).

## Operational: **YES** -- 19 of 20 PASS, 1 PARTIAL, 0 FAIL (2026-11-26T16:58Z to 17:20Z)

Deployed version **9ee9ff607d70** on BATI (release adw `2092efb5`, through `adw_deploy.py`; health check passed). The
one PARTIAL is the dashboard (test 19): it shows her Bridge tasks but not her readiness. Nothing was mocked: every
test ran against the live gateway, BATI's real worker, real Pi and the real supervisor, and every file a test
claims was read back independently over SSH. Tests 1-12 ran on 3e794a44d017 (16:58-17:07Z), and tests 13-20 on
9ee9ff607d70 after its deploy. 9ee9ff6 changes only what happens when an operation *fails*. None of tests 1-12
takes that path, and the fail path was re-proved live on 9ee9ff6 (`t_cc8601a4…`, one execution).

| # | Test | Result | Task / ADW | Actual (short) |
|---|---|---|---|---|
| 1 | Starts on BATI | PASS | `t_0e4fc746d92b5026de513a1c` | unit active on DESKTOP-SHDBATI; status `ready` |
| 2 | Accepts a Bridge task | PASS | `t_0e4fc746…` | claimed by `wsl-desktop-shdbati-machine`; link `declared` |
| 3 | Reads a controlled file | PASS | `t_d03ec0de1eced3c37be31c50` | exact nonce content reported |
| 4 | Creates and edits a file | PASS | `t_3b524385a1ef8b6508f738fc` | Pi `implement` in the jailed sandbox |
| 5 | File verified independently | PASS | `t_3b524385…` | SSH read and sha256 match |
| 6 | Runs an approved test | PASS | `t_4c791397a45c13e0a96863fb` | suite passed; file unchanged |
| 7 | Inspects the Bridge | PASS | `t_77142679613cbe35c78dbcae` | 21 agents, equal to gateway `/agents` |
| 8 | Sends a post office message | PASS | `t_3d330731293e62e7fbbf32c5` | to `python:hello_world`, task_type `hello` |
| 9 | Other agent receives and answers | PASS | `t_3d330731…` | "Hello World" |
| 10 | Sheila receives the reply | PASS | `t_3d330731…` | correlated by envelope id |
| 11 | Executes an ADW | PASS | `t_db401d94d94e57a10025824a` / adw `329b140a` | `machine_watch_dry_run`, exit 0 |
| 12 | Returns a verified ADW result | PASS | same | "checks passing"; ledger line with the ADW id |
| 13 | Supervisor detects success | PASS | adw `ae84a08a` | cron watch 6/6 at 17:11:27Z |
| 14 | Supervisor detects a real failure | PASS | adw `f2b149fb` | worker stopped (controlled) 17:11:43Z; 1/3, 2/3, incident `20261126T171606Z-bati-ce0ddc` at 3/3 |
| 15 | Repairs through the approved mechanism, mails | PASS | adw `f2b149fb` | `systemctl --user restart agent-bridge-worker-machine`, recheck ready, incident fixed 17:17:48Z after 1 attempt; one mail "sheila failed and was fixed" sent via BATI Gmail 17:18:02Z |
| 16 | Recovers after repair | PASS | adw `68df520a` | active since 17:17:16Z; counters cleared |
| 17 | Task queued in the outage completes once | PASS | `t_3e443b370db3f2bf791ae7b3` | queued 17:12:54Z, 0 attempts; claimed 17:17:35Z; succeeded, 1 attempt |
| 18 | Bridge shows distinct, true states | PASS | `rq_b0bf4a33…` | queued / running / succeeded / failed-with-error; phase waiting during the outage, `no_progress` escalations at the real stalls |
| 19 | Dashboard summary | **PARTIAL** | -- | Requests panel shows the mission and Sheila's tasks; no readiness card and no BATI lane |
| 20 | Available without an interactive session | PASS | `t_8feda6556595dd8302fcdf4a` | fresh status `ready`; Linger=yes, unit enabled, supervisor from cron |

Section 8: **Claude Sonnet** `t_f78a7015e45052628a46b221` and **Claude Opus** `t_14b5e32148a8e4f3b338d58d` each ran an
`implement` through the Bridge, named this request, wrote their nonce file (`acceptance_20261126/sonnet.txt`,
`opus.txt`), and were read back on disk. Both succeeded in one attempt.

### Blockers (none stops her from operating)

| Item | Owner | Detail |
|---|---|---|
| Dashboard readiness card (test 19) | EG / the dashboard's session | SQMC `~/letta-code/dashboard` has uncommitted changes from another session; it was not edited. The data is ready: `{"sheila":"status"}` returns `runtime_state`, blockers and version |
| Close `rq_2efbd…` and `rq_e7f95…` | ChatGPT (OAuth client) or EG | per-client request scoping, see below |
| RSI pilot (section 15) | open | `adws/agent_improvement/profiles.py` has no Sheila `TargetProfile` and no judge for her. A cycle was not run, rather than faked. Activating any improvement stays EG's approval |
| Mail delivery, mailbox side | -- | the supervisor counts a mail as sent only when the Gmail connector's send result returns without error. Reading the inbox from this session needs a Gmail re-login |

### Failure supervision (section 14)

- **Bounded retries.** A Pi run (transport or model failure) is retried up to 3 times with the same idempotency key.
  A failed operation is never retried (9ee9ff6). The supervisor wakes at most 8 times a day, with a 360-minute
  cooldown per check.
- **Escalation.** Rung 1 is the local supervisor, then peers over SSH (SQMC, R46), then a HIGH PRIORITY mail, all
  through the mail guard, to one recipient from settings. Proved to rung 1 in test 15. The three rungs were drilled
  on 2026-11-15.
- **Network outages versus implementation failures.**
  - The supervisor's report separates them: test 15's diagnosis was "a clean stop (SIGTERM), not a crash".
  - SQMC's `wsl-interop` check flags a dropped interop channel.
  - Failure Triage clusters failed envelopes by normalised error and routes code or config clusters to an owner.
  - A usage limit or no internet leaves a supervisor unable to act. The ladder then moves on, and the mail is held
    in `mail_outbox/` and flushed later. That happened today at 16:47Z and was delivered later by adw `4c975ad4`.
- **No mail storms.** Test 15 sent one mail for one incident; the outbox was empty afterwards.

## Correction, recorded rather than hidden

Test 15's supervisor wrote that it "didn't find who or what stopped" the worker at 17:11:43Z. That stop was this
mission's controlled failure for tests 14-17. Its other remark, a Sheila task "without a task_type", refers to
`t_cc8601a4…`. That task was a deliberate re-proof that a refused message runs once (9ee9ff6), not a defect.

## How a task reaches her

```
Bridge client (chatgpt-bridge / toyota-bridge / sydney-bridge)
  -> gateway agent "sheila" (protected; pinned to BATI's machine-lane worker token)
  -> BATI unit agent-bridge-worker-machine.service (worker id wsl-desktop-shdbati-machine)
  -> post office envelope to "sheila" (BATI ~/agent_blocks/adws/adw_data/post_office/)
  -> SheilaOperations (agent_comms/executors/sheila_ops.py), a Decorator in front of Pi:
       {"sheila": "status" | "bridge" | "message" | "run_adw", ...}  -> a deterministic operation (no model)
       anything else                                                  -> Pi in its bubblewrap jail
  -> ledger line (BATI ~/.local/share/sheila/ledger.jsonl) and the Bridge result
```

| Runtime item | Value |
|---|---|
| Identity | post office agent `sheila`; Bridge agent `sheila`; read-only Bridge client `sheila-bridge` (`k_8438df5d9ce7e513251e5d15`, token 0600 at BATI `~/.config/sheila/bridge_read_token`; a submit with it is 403) |
| Pi | 0.74.2 under `~/.nvm/versions/node/v22.18.0`; Pi state `~/.pi-bridge/agent`; login copied without its refresh token, 0600, per run |
| Model | `openai-codex/gpt-6-sol`, pinned and confirmed per task (see `CLAUDE_AGENTS.md`) |
| Workdir | `~/bridge_workspaces/sheila_sandbox` (Bridge workspace `sheila`); read-only `inspect` / `explain` / `ask`, write `implement` |
| Performers | `pi:read-only`, `pi:workspace-write+shell`, `sheila:operations` |
| Completion | the worker publishes the result to the gateway; the Bridge task ends `succeeded` or `failed`, never "accepted" |
| Supervisor | BATI's cron watch every 2 min, check `sheila` (`adw_sheila.py status --check`, threshold 3); repair named in the check's `about`: `systemctl --user restart agent-bridge-worker-machine` |
| Post office | fixed recipients (`hello-world: hello`); correlated replies; failed operations are not retried |
| ADWs | fixed list on her card: `machine_watch_dry_run`, `sheila_selftest`; each run returns its ADW id, exit code, duration and output tails |
| Logging | her ledger; the worker's journal; post office `events.jsonl`; gateway `/tasks/{id}/events` |
| Deploy | `adw_deploy.py deploy.deploy --project agent_blocks_bati` (fast-forward of BATI's checkout, worker restart, health check, automatic rollback) |
| Independence | a systemd user unit plus cron: no interactive session is needed |

## Capabilities (each proven live; see the test numbers)

| Area | Capability | Test |
|---|---|---|
| Files | read a controlled file and report its exact content | 3 |
| Files | create and edit a file, verified independently over SSH | 4, 5 |
| Files | run an approved test suite and leave it unchanged | 6 |
| Bridge | inspect state and discover agents (her list equals the gateway's `/agents`) | 7 |
| Swarm Forge / post office | send, the other agent receives and answers, she receives the reply, correlated | 8, 9, 10 |
| ADWs | execute an allowlisted ADW and return a structured, ledgered result | 11, 12 |
| Email | only through the Machine Supervisor's mail guard: one recipient from settings, exact subject, body hash checked | 15 |

## What was wrong, and what changed (commits on `main`)

| Commit | Change | Why |
|---|---|---|
| 22eadbf | Pi executor, jailed, pinned model | earlier the same day |
| 16fdb7e | operations layer, BATI machine lane, supervisor check, deploy entry | Sheila had no Bridge route, no status, no supervisor check |
| 8ca64cf | readable Bridge summaries; a named `User-Agent` | the gateway answers Python-urllib with 406 -- her first live `bridge` op failed on it (BATI failed envelope, 16:49Z) |
| d8c1510 | gateway `RequestWatch`: only a request opened within 6 hours adopts an undeclared task | new tasks were being attached to week-old supervised requests (`rq_5762…` had adopted 191); a task can still name an old request explicitly. Deployed to the gateway host, 196/196 tests there; backup `~/agent_bridge_backups/20261126T165736Z_adopt_window` |
| 3e794a4 | her ledger keeps the headline, not the flattened JSON body | the status op showed unreadable summaries |
| 9ee9ff6 | a failed operation carries `retryable: False` | live: one refused `message` envelope ran three times (task `t_a2302bd175a614cb887efdb0`). A retry of an operation can only repeat a refusal or duplicate a side effect (a message sent, an ADW run). Re-proved live: task `t_cc8601a4a063e676af4820be`, one execution |

Every change has a regression test that was red before the fix.

## Decisions on old items (nothing deleted, purged, replayed or resent)

| Item | Disposition |
|---|---|
| `rq_2efbd0b987493fa1bf234914` (`sheila-finish-20261125`) | **superseded by `rq_b0bf4a3307995bb6ac4e1b92`**. Its 24 tasks were all *adopted*, none declared. Requests are scoped per client: it belongs to the OAuth ChatGPT client, so this mission's `eg-chatgpt` token gets `not_found` and cannot close it. Not worked around. **Blocked on ChatGPT (OAuth session) or EG** -- text to send is below. |
| `rq_e7f95a093cc0deb6a74d886d` | duplicate of `rq_2efbd…`; same owner, same block |
| envelope `20261125T201929Z_8d8d4aed_from_chatgpt-bridge` (todo `8d8d4aed`, task `t_a7ab6b7cc3a8fabb79e87dc3`) | stays failed, historical. It failed on the Claude usage limit before any Sheila run; replaying it would duplicate this mission |
| Supervisor todos 788e1793, d51492a0, e31994dc | stay failed, historical |
| SQMC `agent_blocks_dev` Sheila demo | superseded, not ported (it predates Pi and would be a second implementation) |

Text for ChatGPT to send (`request_close`, as the client that owns them):

```
request_close rq_2efbd0b987493fa1bf234914 outcome=cancelled reason="Superseded by rq_b0bf4a3307995bb6ac4e1b92
(Sheila on BATI, 20-test live acceptance, 2026-11-26). Its 24 tasks were adopted, not declared; evidence:
agent_bridge/docs/SHEILA.md and SHEILA_ACCEPTANCE.json."
request_close rq_e7f95a093cc0deb6a74d886d outcome=cancelled reason="Duplicate of rq_2efbd0b987493fa1bf234914,
itself superseded by rq_b0bf4a3307995bb6ac4e1b92."
```

## Post office, 2026-11-26T17:01:40Z (counted from the files, nothing moved)

| Machine | new | in process | completed | failed | waiting > 1 h | alert files |
|---|---|---|---|---|---|---|
| BATI | 3 | 0 | 17 | 2 (sheila 1: the 406 above, fixed; toyota 1: sidecar refusal 2026-10-30) | 2 (unread replies addressed to `post-office`, 2026-10-30) | 2 |
| SQMC | 124 | 0 | 2025 | 95 (claude-sonnet 34, machine-ops 29, codex 10, codex-writer 9, yolanda 8, opus 4, mechanic 1) | the 124 are unread *replies* (sydney / post-office, from 2026-10-28 on), no request waiting undelivered | 92 |
| R46 | -- | -- | -- | -- | no post office on R46 | -- |

"Undelivered", "retry" and "orphaned": no request envelope is waiting unclaimed on either machine and no running
marker is left; the waiting files are replies nobody reads. SQMC's failed envelopes are Failure Triage's ledger
(`adws/adw_data/failure_triage/`), handled there.

## Rollback

`adw_deploy.py rollback --project agent_blocks_bati` puts the previous release back. Removing `sheila` from BATI's
`post_office_agents` in `lancedb_memory/agent_bridge_worker/machines.json` and redeploying takes her off the Bridge.
The gateway change is reversed from its backup.
