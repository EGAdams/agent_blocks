# Browser Control (ADW) -- ChatGPT and Toyota drive a browser on the Windows desktop

Status: implemented and verified locally 2026-11-07. **Not yet registered on the production gateway** and
**not enabled on the live worker** -- both are deploy steps (below).

## What exists, what was reused

| Need | Reused (already in the repo) | Added |
| --- | --- | --- |
| Start Chrome/Edge on Windows with a DevTools port | `browser_session` (`DebugChromeLauncher`, `ChromeCommandBuilder`, profile dir, detached spawner) | table of browsers (`browser_control/models.py`) |
| Drive a tab (navigate, text, click, tabs) | `cdp_browser.CdpWorkflowBrowser` (DevTools, no driver) | `reload`, `capture_png`, `is_foreground`, `close_page` (additive) |
| Find one clickable element | `browser_workflows` `RankedElementResolver` + locator strategies | -- |
| "Is this click consequential?" | `browser_workflows` `KeywordConsequenceClassifier` | -- |
| ADW run, lifecycle log, object trace | `browser_workflow_adw.run_browser_operation` + SSSF + `object_trace` | `adws/adw_browser_control.py` |
| Bridge transport | gateway + worker + `ScopedPolicy` (unchanged) | `BrowserAgent` (like `notes`/`printer`) |

Not used: Playwright/Selenium (the CDP path needs no driver), MCP browser servers (none present). Both Windows
Chrome is reached from WSL over loopback, which needs `networkingMode=mirrored` (see Host requirements). A link opened with Windows'
own default handler can never be steered afterwards, so `default` means Chrome with a debug profile
(`%LOCALAPPDATA%\adw-chrome-debug`; Edge: `adw-edge-debug`, port 9223).

## The ADW

`adws/adw_browser_control.py <operation> [flags]` -- one operation per run, `--json-out` for the outcome.
Operations: `browser.open  browser.navigate  browser.refresh  browser.tabs  browser.select_tab  browser.click
browser.read  browser.screenshot  browser.status  browser.close`. Package: `adws/browser_control/`
(`operations.py`, `catalog.py`, `plane.py`, `interfaces.py`, `models.py`). Each operation is an `Operation` over an
injected `ControlPlane`; a new one is a class plus a line in `catalog.ORDER`.

Tracing: the ADW id is the execution id. Each run is an `adw.run:<id>` object with an `adw.phase:<id>:browser_<op>`
child in the object trace (Object Debugger `recent`/`task`), with timestamps, status, and on refusal the reason.
The SSSF lifecycle log (`adws/adw_data/sssf.db`, `just phases <id>`) holds arguments and result.

Safety, enforced in code: only http(s) URLs with a host; URLs are reported without query/fragment; `read` returns
visible text only (no form values, cookies, storage); password fields are never clicked; clicks whose words look
consequential (pay, send, delete, confirm, ...) are refused unless the request carries `"confirm": true`;
`close` needs an explicit tab and refuses to close the last tab (that would quit the browser); the browser is
released, never quit.

## How ChatGPT invokes it

### Named MCP tools (2026-11-07)

Root cause of "ChatGPT cannot open Chrome": the connector listed no browser tools. The browser agent was reachable
only through the generic `submit_agent_task` with a hand-written JSON prompt, so ChatGPT concluded it had no browser
(and the agent's description still said "Chrome or Edge"). The gateway now lists eleven `browser_*` tools
(`gateway/src/BrowserTools.php`), each the same `browser` task written by the gateway and submitted with the
caller's own credentials through `ClientTasks->submit`, so policy and client scopes apply unchanged:

| Tool | ADW operation | Task |
| --- | --- | --- |
| `browser_open_dashboard_notes` | `browser.dashboard_notes` (request -> launch -> visibility -> dashboard_tab -> click -> notes_verify) | implement, no fields |
| `browser_open` / `browser_navigate` / `browser_click` / `browser_select_tab` / `browser_refresh` / `browser_close_tab` | open / navigate / click / select_tab / refresh / close | implement |
| `browser_read` / `browser_tabs` / `browser_status` | read / tabs / status | inspect |
| `browser_get_result` | -- | reads a browser task's result |

Each call waits up to `browser_wait_seconds` (default 20) for the worker's verified result; if not ready it returns
`ready=false` and the task_id for `browser_get_result`. A failure carries the ADW's line, `cause`, `adw_id` and the
task's recorded steps as `diagnostics`. Tools are listed only when the `browser` agent is configured, the gateway
grants the operation's mode, and the caller's client scope allows agent/workspace `browser`.
ChatGPT must refresh the connector to see them.

### Raw task form

Agent `browser`, workspace `browser`. The prompt is JSON:

| Say | operation | prompt |
| --- | --- | --- |
| "Open the browser." | `implement` | `{"browser":"open"}` |
| "Go to my dashboard." | `implement` | `{"browser":"navigate","url":"https://desktop-2obsqmc.tailb8fc54.ts.net/"}` |
| "Refresh the page." | `implement` | `{"browser":"refresh"}` |
| "Read what's on the screen." | `inspect` | `{"browser":"read"}` |
| "Click the Notes button." | `implement` | `{"browser":"click","text":"Notes"}` |
| "Open the browser and go to Notes." | `implement` | `{"browser":"dashboard_notes"}` (no other fields) |
| tabs / select / screenshot / status / close | `inspect` for tabs, screenshot, status; `implement` for select_tab, close | `{"browser":"tabs"}`, `{"browser":"select_tab","tab":"2"}`, `{"browser":"close","tab":"2"}` |

Optional fields: `target` (chrome only), `url`, `tab` (number or id), `text`, `label`, `css`, `confirm`.
A task succeeds only if the ADW verified the operation (`details.verified`); the answer carries `details.adw_id`.

## Deploy steps (not done)

1. **Gateway** (`bridge.php` on the host, then `php bin/migrate.php` to re-seed the registry; back up first as in
   DEPLOY.md). Add to `agents`:
   `['name' => 'browser', 'display_name' => 'Browser Control', 'description' => 'Opens and drives Chrome on EG\'s Windows desktop; every action is an ADW run.', 'operations' => ['inspect', 'implement'], 'workspaces' => ['browser'], 'write_workspaces' => ['browser']]`
   `implement` must be allowed for the client token, as for Notes.
2. **Worker**: `touch ~/.config/startrek_browser/enabled`, restart `agent-bridge-worker`. (Off until then.)
3. Toyota: no code needed for reading status; a Toyota plan like `notes_plans.py` is a follow-up.

## Tests

`cd adws && ../lancedb_memory/.venv/bin/python -m pytest -q browser_control/tests` (18), and
`cd lancedb_memory && .venv/bin/python -m agent_bridge_worker.tests.test_browser_agent` (12).

## Chrome only; host requirements (2026-11-07 fix)

Chrome is the only authorized browser: Edge was removed from the table, aliases and defaults; any other name is
refused ("Chrome is the only authorized browser ... Nothing was launched.") and the worker agent refuses it first.

The first live failures ("control port never answered") had two host causes, now diagnosed by `link.py`:
1. **WSL interop**: a systemd service has no `WSL_INTEROP`; its default socket can belong to a dead session
   (`accept4 failed 110`), so `chrome.exe` never starts. The ADW now finds a working socket, or says interop is down.
2. **WSL networking**: Chrome listens on Windows' 127.0.0.1:9222. WSL reaches it only in `networkingMode=mirrored`.
   The live box's `.wslconfig` has `localhostForwarding=true` and no mirrored mode (NAT), so the port is unreachable
   whatever the timeout, and the Windows firewall drops WSL-to-host connections. Fix: add `networkingMode=mirrored`
   under `[wsl2]` in the Windows user's `.wslconfig` and `wsl --shutdown` (restarts every WSL service).

## browser.open is three phases: launch, visibility, open (2026-11-07)

"The control port answers" proved Chrome was running, not that anyone could see it. Chrome started through
WSL interop opens in the Windows session of the `wslhost` carrying the interop socket; after WSL was restarted
remotely that was session 0, the hidden services desktop. Every check passed and nobody saw a window.

`browser.open` is now an `OperationSequence` (`browser_control/catalog.py`), each step its own traced phase:

| Phase | Does | Fails with `cause` |
|---|---|---|
| `browser_launch` | Starts Chrome through an Interactive-only scheduled task `\ADW\ADW_OpenChrome` run as the signed-in user (`windows.InteractiveTaskStarter`), so it opens on that person's desktop whatever session WSL is in. A Chrome already on the port but in a hidden session is closed (`Browser.close`) and replaced. | `windows_interop`, `launch`, `control_port`, `hidden_session` |
| `browser_visibility` | Asks Windows (`windows.WindowsSessionProbe`) which session owns the debug port's Chrome and which sessions have a desktop (explorer.exe). Success only if they match and it isn't 0. | `hidden_session`, `visibility_unknown` |
| `browser_open` | Adds the tab if Chrome was already running; verifies the tab list. | -- |

PowerShell gets each script as `-EncodedCommand`, and the URL travels inside it base64-encoded, so no URL can
change what the script does. The debug profile is `%LOCALAPPDATA%\adw-chrome-debug` of the signed-in user.
