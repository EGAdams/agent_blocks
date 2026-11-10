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
`bin/watch.php` runs one refresh from the command line; it is **not** on a
cron yet (the host has no crontab), so between requests nothing is evaluated.
Workers poll every few seconds, and their silence is itself what
`worker_silent` measures, so in practice ChatGPT's or Toyota's next call does
the evaluation.

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
