# BATI desktop Chrome

Drive the debug Chrome on BATI's interactive Windows desktop (`DESKTOP-SHDBATI`), from BATI only.
Entry point: `adws/adw_bati_chrome.py {status,open,show_view,read,screenshot,plan_for_today}`.

## Why this exists (the missing piece)

Browser Control (`adws/browser_control/`, `adws/cdp_browser/`) reaches Chrome's DevTools port at
`127.0.0.1:9222`. On SQMC that works. On BATI it couldn't, for three reasons:

1. **BATI's WSL runs in NAT networking mode** (`wslinfo --networking-mode` → `nat`, no `.wslconfig`), so WSL
   cannot reach Windows' loopback, where Chrome binds its debug port. This is the integration that was missing.
2. **There was no Bridge worker on BATI**, so the Bridge `browser` agent (on SQMC's worker) couldn't target it. There is
   one now: see "Over the Bridge" below.
3. **EG's everyday Chrome has no debug port**, and must stay that way.

The alternative fix, `networkingMode=mirrored` plus `wsl --shutdown`, would restart WSL. That would take down
BATI's Docker (the Letta server) and its in-WSL `tailscaled`, so it needs EG's approval. It was not done.

## How it works

`relay_win.py` runs under BATI's own Windows Python (`C:\Python313\python.exe`) as the signed-in user, for one
ADW run. It is passed base64-encoded inside `-c` so interop quoting can't alter it. It talks to
`127.0.0.1:9222` on WSL's behalf over its stdin/stdout, one JSON object per line. It opens no listener, makes
no firewall, WSL or registry change, needs no admin, and exits when stdin closes.

Chrome is launched (when no debug Chrome is up) by Browser Control's own `InteractiveTaskStarter`: the
`\ADW\ADW_OpenChrome` scheduled task, Interactive logon, RunLevel Limited, separate profile `adw-chrome-debug`.
If nobody is signed in, that is refused, and so is this ADW. Access controls are never worked around.

## Safety, enforced in code

| Layer | Refuses |
|---|---|
| machine guard (`operations.guard_machine`) | anything but WSL hostname **and** Windows `COMPUTERNAME` = `DESKTOP-SHDBATI` (`wrong_machine`). Never reroutes. |
| URL allowlist (`BatiPolicy.origins`) | any origin but `http://localhost:8765` / `http://127.0.0.1:8765`, or any URL with credentials (`url_not_allowed`) |
| view allowlist (`BatiPolicy.views`) | any dashboard tab but `plan-for-today` (`view_not_allowed`) |
| visibility (`WindowsSessionProbe`) | session 0, no signed-in desktop, unknown placement (`hidden_session`, `no_desktop`, `visibility_unknown`) |
| relay, WSL side (`relay.py`) | `/json/close`, the browser-level socket, non-loopback socket URLs |
| relay, Windows side (`relay_win.py`) | any HTTP path outside `/json/{list,version,new?,activate/}`, any method but GET/PUT, any socket but `/devtools/page/<id>`. With no browser socket there is no `Browser.close`, so it cannot quit Chrome. |
| operations | there is no type, fill, close or quit. `read` returns the URL without query/fragment, the title, which view is showing, its heading and status line, and the task count. No form values, cookies or storage. |

## Audit, timeouts, alerts

- Audit: `adws/adw_data/bati_desktop/audit.jsonl` (gitignored). Each run writes a `start` and `end` line with
  the ADW id; a failure adds an `alert` line.
- Screenshots: `adws/adw_data/bati_desktop/screens/<adw_id>.png`.
- Timeouts: one relay call is 15 s; the whole operation has a hard 120 s SIGALRM deadline; Chrome launch has 25 s;
  the view has 20 s.
- On any failure or timeout: a Windows toast on BATI's desktop (the Machine Supervisor's `PowerShellToast`) and
  a non-zero exit.

## Known behaviour

A freshly launched dashboard has its nav buttons before its module scripts bind their click listeners, so the
first press can do nothing. `show_view` presses again until the view is really active. That is safe: a nav
press switches the view and re-reads the note; with today's note present, `load()` never writes.

## Tests

`cd adws && uv run --quiet --with pydantic --with pytest pytest -q bati_desktop/tests`. These are 18 offline
tests with fake relay/starter/session parts. They also run `relay_win.py` itself to prove its own refusals.

## Over the Bridge

Since 2026-11-21 BATI runs its own worker on the **machine lane** (unit `agent-bridge-worker-machine`, worker id
`wsl-desktop-shdbati-machine`, token id `k_058e496e9df5beed04103e38` in `~/.config/agent_bridge/`). That is the R46
pattern, copied: a general worker here would compete with SQMC's for every general and coding task. The gateway's
`worker_pins` offers the protected agent `bati-desktop` to that token alone, and `machines.json` maps
`desktop-shdbati` to it. `lancedb_memory/agent_bridge_worker/bati_desktop_agent.py` checks the prompt (JSON, every
field allowlisted, `machine` required) and runs this ADW through `adapters/bati_desktop_process.py`.

    inspect    read_only        {"bati": "status"|"read"|"screenshot", "machine": "BATI"}
    implement  workspace_write  {"bati": "open"|"show_view"|"plan_for_today", "machine": "BATI"}

Verified live 2026-11-21: task `t_f2e6fba90a3f2186e5f7155b` (`status`, sent as `toyota-bridge` from SQMC) was claimed
and completed by BATI's worker, adw `a7f8cd9e`. Logs: `journalctl --user -u agent-bridge-worker-machine` on BATI.
Worker tests: `cd lancedb_memory && uv run --quiet --with pydantic --with pytest pytest -q agent_bridge_worker/tests`.

## Who reaches it

- **ChatGPT**: seven MCP tools, `agent_bridge/gateway/src/BatiTools.php` (an `AgentTaskTools` subclass). Reads are
  `bati_status`, `bati_read` and `bati_screenshot`; steps are `bati_open`, `bati_show_view` and `bati_plan_for_today`;
  `bati_get_result` collects a slow answer. The gateway writes `machine: "BATI"` into every prompt itself and ignores
  any machine the caller sends. It checks the URL and view allowlists before a task is queued; the ADW checks them
  again and decides. A screenshot stays on BATI (its path comes back in `data`); it isn't published to the download store.
  Deployed 2026-11-21, by adding `BatiTools.php` and two lines to the host's own `Bootstrap.php`. Note that the host
  copy is ahead of this repo (request-origin work, migration 010), so it must be patched, never overwritten. Backup:
  `~/agent_bridge_backups/pre_bati_tools_20261122T013258Z`. The host suite passed 195/0. Verified live through MCP
  from SQMC: `bati_status`, task `t_c302b822e44402a8fc77df23`, adw `93fc1f2b`, actor `chatgpt-bridge`.
- **Toyota and Sydney**: the `bridge_bati_desktop` route (`receptionist_team/bridge_client/bati_plans.py`,
  `adapters/bati_desktop_route.py`). It has offline tests only and no live run through either of them yet.

## Not yet

- The view allowlist is one view. Widen it deliberately, one entry at a time.
