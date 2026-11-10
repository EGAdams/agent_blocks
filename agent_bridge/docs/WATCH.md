# Gateway Watch

The gateway watches its own queue. It runs on the web host, not on EG's
machine, so it keeps raising alerts while EG's machine, its WSL VM, the Bridge
workers and the dashboard's ADW Supervisor are all down. That was the failure
on 2026-11-09/10: the WSL VM shut down at 23:17, everything that could have
noticed went down with it, and the "Deed Notary Plan" print
(`t_50d047d72171a1ceb5e69db9`) waited 93 minutes and then failed ("no default
printer is set") without anyone being told.

Code: `gateway/src/AlertWatch.php`. Tables: `migrations/008_alerts.sql`
(additive). Tests: `gateway/tests/alert_tests.php`, which holds the six
simulations (stuck print, ADW timeout, crashed worker, Bridge disconnect,
watch restart, recovery of a stalled job). No test prints, and each one checks
that the print count did not change.

## What raises an alert

`QueueWatchPolicy` is pure; thresholds (seconds) are overridable under
`watch` in `bridge.php`:

| kind | when | sticky |
|---|---|---|
| `worker_silent` | a worker lane has not asked for work for `worker_silent` (1200) | no |
| `task_queued_too_long` | queued past `queued_device` (300, printer/scanner) or `queued` (600); says when every worker is silent | no |
| `task_running_too_long` | claimed longer than `running` (3600), measured from the claim | no |
| `task_worker_lost` | running, lease lapsed `lease_lapsed` (120) and no worker has polled to recover it | no |
| `task_retried` | requeued after a lost lease | no |
| `task_crashed` | failed with "lease expired" | yes |
| `device_task_failed` | a printer or scanner task failed | yes |
| `print_unconfirmed` | accepted/queued/submitted to the spooler with no settled `printer_status` for `print_unconfirmed` (900) | no |
| `print_ambiguous` | the device could not say whether paper came out | yes |

Each alert carries the job id, the problem, how long, and a recommended
action. A recommended action never resends a print: that stays EG's call.

A condition alert clears itself when the condition does (kept under
`recently_resolved`). A sticky alert stays until acknowledged and its key is
never raised again. One open alert per condition (`dedupe_key`). Unacknowledged
alerts escalate notice → escalated (15 min) → urgent (60 min), once per level,
and each raise and escalation is audited as `gateway-watch`.

The watch is refreshed on any gateway request (rate-limited to
`refresh_seconds`, 30) and never throws into the request it rides on.
`bin/watch.php` runs one refresh from the command line. Since 2026-11-10 the
gateway host's crontab runs it every 5 minutes, so alerts are raised and
escalated even when no client or worker calls (EG's machine down overnight is
exactly that case):

```
MAILTO=""
*/5 * * * * /usr/bin/flock -n ~/agent_bridge_data/watch.lock /usr/local/bin/php ~/agent_bridge/gateway/bin/watch.php ~/agent_bridge_data/bridge.php > ~/agent_bridge_data/watch.last 2>&1
```

(absolute `/home1/tinman72/...` paths in the real entry). It never mails;
`watch.last` holds the last run's one line of counts; `flock` stops runs
overlapping. It passes the config path because the web server, not the
gateway tree, names `BRIDGE_CONFIG`. A run takes under 0.1 s and writes
nothing unless an alert is raised, escalated or resolved. Remove it with
`crontab -e`.

## Request supervision

Tasks were not enough. On 2026-11-10 EG asked ChatGPT to print the final
deed and affidavit. ChatGPT made three `printer_list` checks (16:01:32,
16:16:51 and 16:17:05 local; tasks `t_ae1582…`, `t_878707…`, `t_7df2ab…`), all
succeeded, and no print was ever submitted. No task was stuck, so no watch
fired. The user's request lived only in ChatGPT's chat.

Code: `gateway/src/RequestWatch.php`. Tables: `migrations/009_requests.sql`
(additive: `requests`, `request_tasks`, `request_events`, `client_calls`).
Tests: `gateway/tests/request_tests.php`, which starts with a regression of that
incident. No test prints.

A **request** is a user's ask held as its own record above its tasks. It has an
id, a client, a title, its agents, its deliverables (for example "print the
deed"), a phase (queued, running, waiting, blocked, completed, failed,
cancelled), a declared blocker and its owner, a last-progress time, linked child
tasks, an event history (escalations, recoveries) and completion evidence. It
stays supervised until it is closed:

- **completed**: only with evidence. A print deliverable is met at
  `completed_per_spooler`, recorded as "…; paper output not verified".
  Submitting a task to another agent is never completion.
- **failed** or **cancelled**: only with a reason.

Tasks are linked at submit time (`LinkRequestPhase`, after `QueueTask`) to the
client's newest open request naming that agent. **Progress** is a non-check
task created or reporting an event. Polls, status checks (`print_status`,
`print_discover`, `search_memory`), MCP list/status tools and worker heartbeats
never move it.

`RequestPolicy` thresholds (seconds) are overridable under `requests` in
`bridge.php`:

| setting | default | what happens |
|---|---|---|
| `accept_seconds` | 30 | no task assigned yet: event `not_assigned` |
| `progress_seconds` | 120 | no progress: event `no_progress` |
| `warn_seconds` | 300 | visible alert `request_stalled` (notice) naming the missing prerequisite |
| `escalate_seconds` | 600 | events `timed_out` and `escalated`; the alert is raised to level 2 |
| `loop_window` / `loop_checks` | 1200 / 3 | `status_loop` alert: a client made only status checks, with no action and no open request |

New alert kinds:

| kind | when | sticky |
|---|---|---|
| `request_stalled` | an open request has made no progress for `warn_seconds` | no |
| `request_blocked` | an open request has waited on a declared blocker for `warn_seconds`; names the owner | no |
| `status_loop` | the loop of 2026-11-10 | no |
| `request_watch_error` | request evaluation threw; the task watch still runs | no |

When a print deliverable names a document that the latest `print_discover`
did not list, the alert says so and says what the outbox does hold.
`GuardRequestDuplicatesPhase` refuses a second print of the same document (or
the same title) inside one open request with `409 duplicate_print`. A replay
with the same idempotency key still passes. A retry after a lost lease is
recorded as a `recovery` event. The gateway restarts nothing; the box's worker
supervisor and factory-ops do that.

ChatGPT reaches requests through the MCP tools `request_open`,
`request_update`, `request_status`, `request_list` and `request_close`. Toyota
and the dashboard reach them over REST: `GET|POST /requests`,
`GET /requests/{id}`, and `POST /requests/{id}/update|close`. Both are offered
only to alert viewers. `GET /alerts` carries `requests` (open, recently closed,
thresholds) and `watch` (refresh interval, seconds since the last check, how
many requests are supervised).

## Who sees them

`AlertViewers`: a client named in `alert_viewers`, any client bound in
`client_scopes` (EG's ChatGPT and Toyota), or `'*'`.

- **ChatGPT**: every MCP tool result to a viewer ends with a `BRIDGE ALERTS`
  block listing up to five unacknowledged alerts and asking it to tell EG and
  then call `bridge_alert_acknowledge`. `bridge_alerts` lists them on demand.
  Verified live on 2026-11-10 with `al_5cb5e93e62de31b971c555ff`.
- **EG's dashboard**: `GET /alerts` (worker token or viewer client) feeds the
  dashboard's failure cards (`letta-code/dashboard/gateway_alerts.py`, the
  `bridge` source in `receptionist_alerts.py`), which show and speak them on
  every tab; "Got it" calls `POST /alerts/{id}/ack` with Toyota's client
  credential. The ADW Supervisor copies the same list into its state as
  `gateway_watch`.

## Why ChatGPT is not pushed to

MCP over this gateway is request/response (no SSE stream, no server-initiated
messages), and ChatGPT does not hold a connection open between turns, so the
gateway cannot start a conversation. ChatGPT learns of an alert on its next
Bridge tool call. Reaching EG while ChatGPT is idle needs a channel outside
MCP — email, SMS, or a phone push service — sending from the gateway host on
`alert.raised`/`alert.escalated`. None is configured: it needs EG to choose the
channel and approve the address and any credentials, and until a delivery is
confirmed by that channel no alert may be reported as delivered.
