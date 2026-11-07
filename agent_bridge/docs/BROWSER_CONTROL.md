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
Chrome and Edge are reached from WSL over loopback (`networkingMode=mirrored`). A link opened with Windows'
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

Agent `browser`, workspace `browser`. The prompt is JSON; ChatGPT turns the sentence into it:

| Say | operation | prompt |
| --- | --- | --- |
| "Open a browser." / "Open Edge." | `implement` | `{"browser":"open"}` / `{"browser":"open","target":"edge"}` |
| "Go to my dashboard." | `implement` | `{"browser":"navigate","url":"https://desktop-2obsqmc.tailb8fc54.ts.net/"}` |
| "Refresh the page." | `implement` | `{"browser":"refresh"}` |
| "Read what's on the screen." | `inspect` | `{"browser":"read"}` |
| "Click the Notes button." | `implement` | `{"browser":"click","text":"Notes"}` |
| tabs / select / screenshot / status / close | `inspect` for tabs, screenshot, status; `implement` for select_tab, close | `{"browser":"tabs"}`, `{"browser":"select_tab","tab":"2"}`, `{"browser":"close","tab":"2"}` |

Optional fields: `target` (chrome|edge), `url`, `tab` (number or id), `text`, `label`, `css`, `confirm`.
A task succeeds only if the ADW verified the operation (`details.verified`); the answer carries `details.adw_id`.

## Deploy steps (not done)

1. **Gateway** (`bridge.php` on the host, then `php bin/migrate.php` to re-seed the registry; back up first as in
   DEPLOY.md). Add to `agents`:
   `['name' => 'browser', 'display_name' => 'Browser Control', 'description' => 'Opens and drives Chrome or Edge on EG\'s Windows desktop; every action is an ADW run.', 'operations' => ['inspect', 'implement'], 'workspaces' => ['browser'], 'write_workspaces' => ['browser']]`
   `implement` must be allowed for the client token, as for Notes.
2. **Worker**: `touch ~/.config/startrek_browser/enabled`, restart `agent-bridge-worker`. (Off until then.)
3. Toyota: no code needed for reading status; a Toyota plan like `notes_plans.py` is a follow-up.

## Tests

`cd adws && ../lancedb_memory/.venv/bin/python -m pytest -q browser_control/tests` (18), and
`cd lancedb_memory && .venv/bin/python -m agent_bridge_worker.tests.test_browser_agent` (12).
