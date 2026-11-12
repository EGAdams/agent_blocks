# Cashy on R46 over the Bridge

ChatGPT (or Toyota) drives **Cashy on R46** — Mom's Windows computer, hostname `Rosemary46`, WSL user
`adamsl` — through the Star Trek Bridge. Cashy runs on R46 and controls R46's own visible Chrome over
loopback DevTools (`127.0.0.1:9222`). Nothing on R46 listens to the network. The worker reaches the gateway
outbound, the same way the home worker does, and SQMC never connects to R46.

Verified live on 2026-11-11: Bridge → R46 worker → Cashy → Chrome. The Fifth Third login tab
(`secure.53.com/olb/login`) is visible on R46's desktop, and later tasks reuse the same session and tab.

## The path

```
ChatGPT ──MCP──> gateway (HostGator) ── task for agent/workspace cashy-r46
                     │  worker_pins: cashy-r46 is offered only to R46's worker token
                     ▼
R46 WSL: agent-bridge-worker-machine.service  (adw_bridge_worker.py --lane machine, worker wsl-rosemary46-machine)
                     │  CashyMachineAgent (lancedb_memory/agent_bridge_worker/cashy_agent.py)
                     ▼
uv run adws/adw_cashy_<op>.py --session=… --actor=<Bridge sender> --port=9222
                     ▼
R46 Windows Chrome (debug Chrome, its own profile, Console session, visible)
```

There are two checks on the target machine:

1. **Gateway.** `worker_pins` in `~/agent_bridge_data/bridge.php` maps R46's worker token id to
   `cashy-r46`. Other workers are never offered that workspace, and if one tries to claim it the gateway
   returns 409 `pinned_elsewhere`.
2. **Worker.** `machine` is required in every task. A task that names another machine is refused, and the
   refusal names both machines and says "nothing was opened here". `machines.json` maps a hostname to its
   alias and agent.

If R46 is off, the task stays `queued`. No other machine will take it. Cancel it, or wait.

## Calls (MCP `submit_agent_task`)

`agent` and `workspace` are both `cashy-r46`. `prompt` is JSON. The fields `machine` and `session` are
required in every call.

| operation | Bridge `operation` | prompt |
|---|---|---|
| status | `inspect` | `{"cashy":"status","machine":"R46","session":"r46-bridge"}` |
| list_options | `inspect` | `{"cashy":"list_options","machine":"R46","session":"r46-bridge"}` |
| read_page | `inspect` | `{"cashy":"read_page","machine":"R46","session":"r46-bridge"}` |
| open_bank | `implement` | `{"cashy":"open_bank","machine":"R46","session":"r46-bridge","bank":"fifth_third"}` |
| logged_in | `implement` | `{"cashy":"logged_in","machine":"R46","session":"r46-bridge"}`; only after EG says he is signed in |
| pause / resume / stop | `implement` | `{"cashy":"pause","machine":"R46","session":"r46-bridge"}` |

The full call:

```json
{"name":"submit_agent_task","arguments":{"agent":"cashy-r46","workspace":"cashy-r46","operation":"inspect",
 "prompt":"{\"cashy\":\"status\",\"machine\":\"R46\",\"session\":\"r46-bridge\"}"}}
```

**Polling.** Poll `get_agent_task_status {task_id}` until `state` is final, then call
`get_agent_task_result {task_id}`. Waiting 10–15 s between polls is enough. When idle, the R46 worker
polls the gateway every 60 s; after a task it polls every 10 s for a while. So the first task after a quiet
spell can take up to a minute to be claimed.

**Result.** `summary` starts with `[R46 rosemary46 session r46-bridge: <state>]`. `details` contains:

- `machine`: `{hostname, alias, agent}`
- `session`
- `actor`: the real Bridge sender
- `state`, `bank`, `tab` (the DevTools target id)
- `url`: sanitized, with no query string
- `title`: with digit runs masked to their last 4
- `task`, `question`, `events`

## Rules (enforced in code)

- **The actor is the real sender** (`chatgpt-bridge`, `toyota-bridge`), never `user`. Cashy lets only
  `user` approve a consequential step. So a Bridge caller can open and read a bank page, but it can never
  approve a transfer or a payment. The prompt cannot carry an `actor` field: any field outside
  `cashy, machine, session, bank` is refused.
- **No credentials.** Nothing types, reads or asks for a password or code. EG signs in by hand.
- **The bank's own domain.** `open_bank` reports "the login page is ready" only once the tab is on one of the
  profile's hosts or a subdomain of one. `evil53.com` does not count. If the tab doesn't get there within
  20 s, the call fails. Fifth Third goes `www.53.com` → `secure.53.com/olb/login-redirect.html` →
  `secure.53.com/olb/login`.
- **One session, one tab.** Repeating `open_bank` on the same bank navigates the session's existing tab;
  it doesn't open another. Cashy never closes the browser.
- **Nothing is copied off R46.** That covers browser profiles, cookies and bank sessions. The debug Chrome
  uses its own profile on R46.

## After EG says "I'm logged in"

1. `logged_in` (`implement`). Cashy checks the page rather than taking the claim on trust. The result
   should be `state: ready`.
2. `list_options` (`inspect`): what the signed-in page offers.
3. Then wait for EG's next instruction. Bridge callers still can't approve a transfer or payment.

## Setup on R46 (already done)

- Unit `~/.config/systemd/user/agent-bridge-worker-machine.service` (source:
  `agent_bridge/systemd/`), enabled. Token: `~/.config/agent_bridge/worker_token`. Under systemd, the
  worker re-registers WSL interop and adds `/mnt/c/Windows/System32` to `PATH` so it can start Windows
  Chrome.
- `lancedb_memory/.venv` (pydantic) runs the worker. `uv` (`~/.local/bin`) runs the Cashy ADWs.
- Health: `systemctl --user status agent-bridge-worker-machine` and
  `journalctl --user -u agent-bridge-worker-machine -f`.
- Gateway: `WorkerPins.php`, plus `worker_pins` in `bridge.php`, plus the `cashy-r46` agent seeded by
  `php bin/migrate.php ~/agent_bridge_data/bridge.php`. Rollback copies are in
  `~/agent_bridge_backups/pre_pins_20261111_191544` and `bridge.php.pre_cashy_r46_*`.

## Tests

- `cd lancedb_memory && .venv/bin/python -m pytest -q agent_bridge_worker/tests`. `test_cashy_agent.py`
  covers routing, wrong or unknown machine, required fields, session continuity, actor never `user`, no
  field injection, Cashy unavailable, title masking, argv, and the pinned lane.
- `cd adws && uv run --quiet --with pydantic --with pytest --with aiohttp --with lancedb --with pyarrow
  pytest -q cdp_browser/tests/test_cdp_unit.py cashy/tests`. These cover the domain check and the
  "no execution context yet" retry.
- Gateway: `php tests/run_tests.php` and `php tests/pin_tests.php` on a host with PHP.
