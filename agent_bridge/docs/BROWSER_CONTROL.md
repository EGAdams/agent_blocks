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
browser.read  browser.screenshot  browser.status  browser.close  browser.form_controls  browser.select_option
browser.fill`. Package: `adws/browser_control/` (`operations.py`, `catalog.py`, `plane.py`, `interfaces.py`,
`models.py`, `forms.py`). Each operation is an `Operation` over an injected `ControlPlane`; a new one is a class
plus a line in `catalog.ORDER`.

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
(and the agent's description still said "Chrome or Edge"). The gateway now lists fourteen `browser_*` tools
(`gateway/src/BrowserTools.php`), each the same `browser` task written by the gateway and submitted with the
caller's own credentials through `ClientTasks->submit`, so policy and client scopes apply unchanged:

| Tool | ADW operation | Task |
| --- | --- | --- |
| `browser_open_dashboard_notes` | `browser.dashboard_notes` (request -> launch -> visibility -> dashboard_tab -> click -> notes_verify) | implement, no fields |
| `browser_open` / `browser_navigate` / `browser_click` / `browser_select_tab` / `browser_refresh` / `browser_close_tab` | open / navigate / click / select_tab / refresh / close | implement |
| `browser_select_option` / `browser_fill` | select_option / fill | implement, a form control named by `css` or `label` |
| `browser_read` / `browser_tabs` / `browser_status` / `browser_form_controls` | read / tabs / status / form_controls | inspect |
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

| "What's on this form?" | `inspect` | `{"browser":"form_controls","limit":120}` |
| "Set Record Type to PLAT Records." | `implement` | `{"browser":"select_option","css":"#ddlGSPermitType","option_text":"PLAT Records"}` |
| "Type Fralick's Addition in Project Name." | `implement` | `{"browser":"fill","css":"#txtGSProjectName","text":"Fralick's Addition"}` |

Optional fields: `target` (chrome only), `url`, `tab` (number or id), `text`, `label`, `css`, `option_text`,
`value`, `limit`, `confirm`. (On the ADW command line a two-word flag is spelled with a dash: `--option-text`.)
A task succeeds only if the ADW verified the operation (`details.verified`); the answer carries `details.adw_id`.

## Deploy steps (not done)

1. **Gateway** (`bridge.php` on the host, then `php bin/migrate.php` to re-seed the registry; back up first as in
   DEPLOY.md). Add to `agents`:
   `['name' => 'browser', 'display_name' => 'Browser Control', 'description' => 'Opens and drives Chrome on EG\'s Windows desktop; every action is an ADW run.', 'operations' => ['inspect', 'implement'], 'workspaces' => ['browser'], 'write_workspaces' => ['browser']]`
   `implement` must be allowed for the client token, as for Notes.
2. **Worker**: `touch ~/.config/startrek_browser/enabled`, restart `agent-bridge-worker`. (Off until then.)
3. Toyota: no code needed for reading status; a Toyota plan like `notes_plans.py` is a follow-up.
4. **The form operations: deployed 2026-11-07 with EG's approval.** They needed no registry or config change --
   the tools are code. What was done: the live `src/BrowserTools.php` and `src/McpEndpoint.php` were copied to
   `~/agent_bridge_backups/<timestamp>/` first, then those two files and `tests/browser_tests.php` were scp'd to
   `~/agent_bridge/gateway/` (md5s match this repo), `php -l` passed on both under ea-php83, and the server's
   own suite went 114 -> **118 passed, 0 failed**. `systemctl --user restart agent-bridge-worker` picked up the
   worker's changed `browser_agent.py`. Live over MCP with the client token: `tools/list` returns 35 tools
   including `browser_form_controls`, `browser_select_option` and `browser_fill`; `browser_form_controls`
   (task `t_e584e40c5345f2257fa294ea`, adw `bf9a03f4`) and `browser_select_option` on a site-drawn combobox
   (task `t_a48c153161dfea200262b2af`, adw `3c3a7b8a`, "Chose 'Apple' ... it now shows 'Apple'") both came back
   `verified: true`. ChatGPT only has to refresh the connector to see the three tools.
   Rollback: copy the two PHP files back out of that backup directory; nothing else changed on the host.

## Tests

`cd adws && ../lancedb_memory/.venv/bin/python -m pytest -q browser_control/tests cdp_browser/tests/test_cdp_unit.py`
(85 + 7), and `cd lancedb_memory && .venv/bin/python -m agent_bridge_worker.tests.test_browser_agent` (21), and
`cd agent_bridge/gateway && php tests/run_tests.php` (118).

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

## The form operations: form_controls, select_option, fill (2026-11-07)

ChatGPT could open Chrome, read a page and press a button, but could not work Michigan's plat-search form.
Three causes, all in Browser Control rather than in that site:

1. **An `<option>` cannot be clicked, ever.** Chrome draws an open dropdown with Windows, not with the page, so
   every `<option>` has a 0x0 box. `RankedElementResolver._judge` filters zero-size elements, so
   `browser.click --text "PLAT Records"` could only ever answer "I could not find one clickable element". A list
   is not clicked; it is told its new value.
2. **Nothing listed a page's form controls**, so a caller had to guess CSS selectors (`cdp_browser`'s
   `interactive_elements` existed for diagnostics but was never exposed).
3. **`LabelStrategy` compares a label exactly**, so the page's "Record Type:" never matched "Record Type".

| Operation | Takes | Verifies by |
|---|---|---|
| `browser.form_controls` | `tab`, `limit` (1-200, default 60) | reading the DOM; `verified` is always true (it only looks) |
| `browser.select_option` | `css` or `label`, and `option_text` or `value`, `tab` | a `<select>`: the value it reports after its `change` event, then a second resolve-and-read of the list. A site-drawn list: the words the control shows afterwards (or the item its `aria-activedescendant` points at) |
| `browser.fill` | `css` or `label`, `text`, `tab` | the value the field reports after `Input.insertText` and its `change` event, judged by `TypedValueCheck` |

Where the parts live:

- `adws/cdp_browser/forms.py` -- `FORM_CONTROLS_JS`, the DOM read. Stable names first (`label[for]`, then
  `aria-label`, `aria-labelledby`, a wrapping `<label>`, `placeholder`, `title`), dropping any candidate that is
  just the element's own id or name (ASP.NET forms set `aria-label` to the id) or that is a `javascript:` href.
  `cssFor` prefers `#id`, then a unique `[name]`/`[data-testid]`/`[aria-label]` attribute selector, and only then
  an `nth-of-type` path. Hidden inputs are not listed at all; no field's typed text is ever read -- a field
  reports only *whether* it holds something.
- `adws/cdp_browser/element.py` -- `options()`, `choose(value)` (sets the value, fires `input` then `change`, and
  returns the list's value afterwards: the in-call verification), `value()` (what a control shows now, which is how
  a site-drawn choice reads back), `fill(text)` (empty, `Input.insertText`, `change`, read the value; a field that
  took nothing is then told its value through `HTMLInputElement.prototype`'s own setter, which is what makes a
  framework's value tracker notice -- that second attempt wins only if it got exactly what was asked for, or if
  typing got nothing at all, so a field that reformats keeps its own version).
- `adws/browser_control/forms.py` -- the rules, no browser: `FormLabelStrategy` (forgives a form's `:`/`*` and
  case, matches a label that starts with the words, and counts one node once so a control never reads as
  ambiguous), `form_strategy_registry()` (re-registers `label` for these operations only, leaving
  `browser_workflows`' default registry as the recorder and playback expect it), `form_locators_from`,
  `SecretFieldGuard`, `OptionMatcher`, `WidgetDropdown` (a list the site draws itself: open it, read the items it
  put in the page, click the one asked for -- items under the control's own `aria-controls`/`aria-owns` list first,
  every clause of the scope scoped to it), `looks_site_drawn`, and `TypedValueCheck` (which mismatch this is:
  reformatted and therefore taken, truncated by a `maxlength`, the wrong format for a date/time/colour input, or
  refused outright).
- `adws/browser_control/operations.py` -- `FormControls`, `FormOperation` (resolve one control, then the guard),
  `SelectOption`, `Fill`. `Click`'s failure now checks whether the wanted text is an `<option>` and, if so, names
  `browser.select_option` and the dropdown's selector.

Safety: `SecretFieldGuard` refuses a `password` or `hidden` input, an `autocomplete` of
`current-password`/`new-password`/`one-time-code`/`cc-csc`, and any control whose id, name, aria-label,
placeholder or title contains password, passwd, pwd, passcode, pin, otp, onetime, mfa, 2fa, totp, secret, token,
apikey, cvv, cvc, ssn, "security code" or "security answer" -- with the word that did it, so a false positive is
obvious. `fill` also refuses a file chooser, a read-only field, and a checkbox/radio/submit (those are
`browser.click`). An option or a control that matches twice is reported with both, never guessed between.
The consequential-click confirmation is unchanged: filling a form submits nothing, and the submit button is
still a `browser.click`.

Failure `cause`s (also in the `browser-form-control` skill): `control_not_found`, `ambiguous_control`,
`not_visible`, `disabled`, `no_such_option`, `not_a_dropdown`, `not_editable`, `readonly`, `credential_field`,
`value_differs`, `did_not_stick`, `not_accepted`, `no_options_shown` (a site's own list opened empty: it filters
as you type, so `fill` it first), `truncated`, `wrong_format`, `refused_text`.

The two kinds of dropdown are one operation to the caller and opposite acts underneath. A native `<option>` has no
box and is chosen by value; an item in a list the site draws itself is a node the page lays out, so it has a box,
is clicked, and is not in the page at all until the control opens -- which is why `browser.click` on the item
alone could never work for either. `form_controls` marks the second kind `site_drawn`, shows no options for it,
and says what it holds now.

Live, through the ADW, on `aca-prod.accela.com/LARA/Cap/CapHome.aspx?module=OLSR`: `form_controls` listed 25
controls with correct labels; `select_option` set Record Type to PLAT Records (`OLSR/PLAT/NA/NA`) and a separate
`form_controls` run confirmed it; `fill` typed a project name; `click` on `#ctl00_PlaceHolderMain_btnNewSearch`
submitted; `read` returned the eight matching records. The site-drawn path, live on the W3C APG select-only
combobox example: `form_controls` (adw `94e47652`) named it `#combo1` and marked it site-drawn with no options;
`select_option --css=#combo1 --option-text=Blueberry` (adw `709c1901`) answered "it now shows 'Blueberry'"; a
separate `form_controls` run confirmed "now 'Blueberry'". A field with its own format or its own mask is covered
by tests at the CDP boundary (`cdp_browser/tests/test_cdp_unit.py`) rather than by a live run.

Skill: `.claude/skills/browser-form-control/SKILL.md` -- the inspect -> select -> fill -> submit -> read loop, the
cause table, and the never-list. It calls these ADWs; it does not reach past them.
