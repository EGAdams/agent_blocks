# Toyota and Sydney → Star Trek Bridge

Sydney (2026-11-13) has the same routes on her own desk: her own
`sydney-bridge` identity (token id `k_4a8a0e0d1602922560b44526`, bound in the
gateway's `client_scopes` with only a `sender`, exactly like Toyota's; scanner
scopes copied from Toyota's; no printer scopes, like Toyota), her own pending
"yes", note revisions and completion notices (`desk.DESKS["sydney"]`,
`desk_for()`), and trace kind `sydney.bridge_submit`. She never falls back to
Toyota's token. The workers accept the three senders from one set,
`agent_bridge_worker/senders.py`.

Current bootstrap behavior (2026-11-06): Toyota has a separate authenticated
`toyota-bridge` identity with the same gateway scope as ChatGPT (EG, 2026-11-06). Machine Ops and Object
Debugger accept her explicitly. The read-only runner returns after submission;
`NOTICES` refreshes asynchronously. Status contains bounded `recent_events`,
and `events t_...` reads that evidence without an events endpoint. Cancellation
uses the existing cancel API. See
[the current integration guide](../../../agent_bridge/docs/BOOTSTRAP_INTEGRATION.md)
for exact commands, identity boundaries and development/deployment scope.
`bridge_notes` manages every note on the Notes page, the same operations ChatGPT has
(`notes_plans.NOTES_PLANS`): list/search, read, highlights, refresh (did it change?), trash list;
and, each read back and sent only after a "yes", update (by exact text, line or section), highlight
(add / recolor / clear), create, rename or move to a category, trash, restore and purge. A change
carries the revision Toyota last read in this process, so she asks for a read first after a restart.
Purge is permanent, works only on a trashed note, and her read-back says so: the "yes" is EG's authorization. The older 15-second polling and missing-cancel descriptions below are historical.

Toyota's narrow door to the public Star Trek Bridge client API. She can list
the Bridge's agents, send a **read-only** request to any Bridge agent that
offers a read-only operation (`codex` on `agent_blocks` by default), and read a
task's status or result back. That is all.

The agent list is never written down here: it is the Bridge's own live
`GET /agents` answer, so an agent added to the Bridge is usable without a code
change, as long as it offers `inspect`, `explain` or `search_memory` in a
workspace it doesn't write to.

## Config files

| file | what it decides |
|---|---|
| `receptionist_team/toyota_roster.json` | which roles Toyota may route to (`enabled`), and which are parked (`disabled`, each with a `purpose`). Her finance routes, Agent Creator, Mechanic and `star_trek_bridge` are all enabled. |
| `receptionist_team/bridge_client/toyota_bridge_route.json` | the Bridge policy: default agent `codex`, default workspace `agent_blocks`, allowed operations `inspect`/`explain`/`search_memory`, `read_only` |

## How a request is addressed

```
ROUTE: star_trek_bridge | what does roster_membership.py do?        -> codex, agent_blocks, inspect
ROUTE: star_trek_bridge | hello-world: say hello                    -> hello-world, its read-only workspace
ROUTE: star_trek_bridge | codex@letta_code: where is the dashboard?  -> codex, letta_code
ROUTE: star_trek_bridge | codex/explain: what does roles.py do?     -> codex, explain
ROUTE: star_trek_bridge | which agents are on the bridge            -> listing, nothing sent
```

`bridge_client/policy.py` checks each one against the live list. A named
agent, workspace or operation that isn't allowed is **refused, never swapped**
for another: an agent that only implements, prints or scans, a write
workspace, or an operation outside the policy all get "…so I didn't send
anything". Text with a colon that doesn't name a listed agent goes to the
default agent unchanged.

`toyota_bridge_route.json` is validated by `models.BridgePolicy`: only the
operations `inspect`, `explain` and `search_memory` are accepted, and
`approval_mode` can only be `read_only`. `implement` and `workspace_write`
fail validation, so they can't be switched on by editing this file. Neither
voice nor model output can choose a write operation or approval; they can
only name a Bridge agent, workspace or read-only operation, which the policy
then checks. The user's words become the task prompt and nothing more.

## Auth variable names

These go in `receptionist_team/.env`, or the service environment. Only the
names are listed here; the values never belong in code, docs, logs or tests.

- `TOYOTA_BRIDGE_CLIENT_TOKEN_FILE`: the path to a file that holds the
  **client-role** token. This is the preferred form.
- `TOYOTA_BRIDGE_CLIENT_TOKEN`: the token itself, as an alternative.
- `SYDNEY_BRIDGE_CLIENT_TOKEN_FILE` / `SYDNEY_BRIDGE_CLIENT_TOKEN` /
  `SYDNEY_BRIDGE_URL`: the same three for Sydney. Her credential is read only
  from her own names; with none set her routes say the Bridge isn't available.
- `TOYOTA_BRIDGE_URL`: optional. Defaults to
  `https://bridge.americansjewelry.com/api/agent-bridge/v1`. Only `https`
  URLs are allowed, and no `/worker` path.

The worker's variables are never read here, and a worker token is not a
client credential. If no client credential is configured, Toyota says the
Bridge "isn't available… Nothing was sent", and every local route keeps
working.

## Endpoints used

These four are the only paths the client sends:

- `GET /agents`
- `POST /tasks`
- `GET /tasks/{id}`
- `GET /tasks/{id}/result`

There is no cancel, because the public API this client uses doesn't
expose one.

## How a turn goes (ADW phases)

`adapters/bridge_runner.BridgeDelegationRunner` runs `bridge_client/workflow.py`:
**Discover → Validate → Route → Submit → Monitor → Retrieve → Respond**. Each
phase writes one `[star_trek_bridge] phase=… status=…` line to stderr (the
service log) with its inputs, outputs, error and next phase, never the prompt
text or a credential. `runner.last_trace` holds the same records.

1. The model emits `ROUTE: star_trek_bridge | <question>`. Toyota speaks her
   acknowledgment first.
2. Discover reads `GET /agents`; Validate applies the policy. If it refuses,
   nothing is sent and Respond says why.
3. It submits once, with a fresh idempotency key. On network trouble it
   retries up to 3 times with the **same** key. If it still can't tell
   whether the task arrived, it says so.
4. It polls for about 15 seconds:
   - Finished: it reads the result back. Only `ready=true`, `ok=true` and
     `state=succeeded` count as success.
   - Still `queued` or `running`: it gives the task id and never claims
     the work is done. The worker only runs during its active hours, and there is one worker,
     so a task waits behind any task already running.
   - `failed` or `cancelled`: it says so, with a sanitized reason.
5. "status t_…", "result t_…" or a plain "status" (for her last task) reads
   a task back instead of sending a new one.

Upstream error text is sanitized before anyone hears it. Bearer tokens,
the configured token, and long token-like runs are stripped, and the text is
clipped. Redirects are refused, response bodies are bounded, and requests
are capped at 64 KB.

## Extending Toyota's roster

1. Define the role: an `agents/<name>/` folder registered through
   `extra_roles.py`, or a `finance_team` role. It also needs a blurb, a
   spoken name and a start task in `roles.py`, which fails loud at import
   if any of them is missing.
2. Add the name to `enabled` in `toyota_roster.json`.
3. If it needs a runner other than `ToolAgentRunner`, map it in
   `profiles._toyota_runner()`.

An unknown name in the config raises `RosterMembershipError` at import. A
name that is both enabled and disabled is invalid. A disabled entry with no
`purpose` is invalid. Nothing is created or enabled automatically.

## Startup

The service unit is `toyota-receptionist.service` (port 8767, on Win11). The
new route is live only after that unit restarts. Restarting it ends Toyota's
warm session, so check that nobody is mid-conversation with her first.

```bash
systemctl --user restart toyota-receptionist
```

## Checks run

From `lancedb_memory/`:

```bash
.venv/bin/python -m receptionist_team.tests.test_bridge_client    # contract, transport, sanitizing
.venv/bin/python -m receptionist_team.tests.test_bridge_runner    # outcomes, dynamic agents, refusals, phases
.venv/bin/python -m receptionist_team.tests.test_toyota_roster    # roster, finance routes kept, specialist, ack, unavailable
```

## Rollback

- To stop Bridge calls only, remove `star_trek_bridge` from `enabled` in
  `toyota_roster.json`, or unset `TOYOTA_BRIDGE_CLIENT_TOKEN_FILE`. Either
  way, restart the unit.
- To undo the dynamic-agent change only (back to the fixed codex route and
  the parked finance routes), on DESKTOP-2OBSQMC:
  `cd ~/agent_blocks && tar xzf ~/rollback/agent_blocks_receptionist_20261103-pre-bridge-dynamic.tgz`
  then restart the unit.
- To roll back everything, return the files listed in the integration
  report to `ffe1d68` and delete the new ones (`bridge_client/`,
  `adapters/bridge_runner.py`, `roster_membership.py`, `toyota_roster.json`,
  `agents/star_trek_bridge/`, and the three new tests). Then restart the
  unit.

## Bridge specialists (beyond read-only)

`star_trek_bridge` stays read-only. Four more Toyota routes each drive one Bridge agent:

| Route | Bridge agent | Operations |
|---|---|---|
| `bridge_writer` | codex-writer | implement (bridge_sandbox by default) |
| `bridge_coder` | claude-sonnet | implement, inspect, explain |
| `bridge_printer` | printer | print, print_status, print_cancel, print_discover |
| `bridge_scanner` | scanner | scan (window or freezer; Scala files the page) |

Flow: `adapters/bridge_specialist_runner.py` asks Groq (`openai/gpt-oss-20b`,
JSON mode) to fill a small form; `specialists.py` checks it against the live
`GET /agents` and builds the exact request (printer and scanner prompts are
built by code). implement, print, print_cancel and scan are read back and held
in `desk.PENDING`; the next short "yes"/"no" is sent to that specialist by
`adapters/bridge_confirmation_route.py`. Everything else is sent at once.

A task still running after ~15 s is handed to `desk.NOTICES`; Toyota's alert
inbox (`CombinedAlerts` with the post office) reports it when it finishes, at
the start of her next turn and on the dashboard. "cancel t_..." asks the Bridge
to cancel a task (`POST /tasks/{id}/cancel`).
