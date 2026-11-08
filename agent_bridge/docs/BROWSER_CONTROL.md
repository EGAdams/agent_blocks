# Browser Control (ADW) -- ChatGPT and Toyota drive a browser on the Windows desktop

Status (2026-11-07, checked by the runs quoted below): **registered on the production gateway, enabled on the
live worker, deployed, and verified end to end through ChatGPT's own MCP connector.** Earlier revisions of this
file said "not yet registered / not enabled" -- that is out of date: `bridge.php` on the host carries the
`browser` agent, `~/.config/startrek_browser/enabled` exists on this box, and the form operations went out on
2026-11-07 (deploy step 4). **Toyota has not been verified against the form operations** -- see Verification state.

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

## Deploy steps (1, 2 and 4 done; 3 is a follow-up)

1. **Gateway** (`bridge.php` on the host, then `php bin/migrate.php` to re-seed the registry; back up first as in
   DEPLOY.md). Add to `agents`:
   `['name' => 'browser', 'display_name' => 'Browser Control', 'description' => 'Opens and drives Chrome on EG\'s Windows desktop; every action is an ADW run.', 'operations' => ['inspect', 'implement'], 'workspaces' => ['browser'], 'write_workspaces' => ['browser']]`
   `implement` must be allowed for the client token, as for Notes.
   **Done** (commit `0c54c81` shift): `grep -n "'browser'" ~/agent_bridge_data/bridge.php` on the host answers at
   line 219 with `inspect`/`implement` and workspace `browser`.
2. **Worker**: `touch ~/.config/startrek_browser/enabled`, restart `agent-bridge-worker`. **Done** -- that
   file exists (created 2026-11-07 17:01) and `systemctl --user is-active agent-bridge-worker` says `active`.
3. Toyota: **still a follow-up, and the one real gap.** No code is needed for her to read status, but no
   Toyota plan routes a form request to these operations and nobody has driven a form through her. A Toyota plan
   shaped like `lancedb_memory/receptionist_team/.../notes_plans.py` is the next piece of work.
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

All three re-run green on 2026-11-07 (20:48 EST, DESKTOP-2OBSQMC):

```bash
cd adws && uv run --quiet --with pydantic --with pytest --with aiohttp \
    pytest -q browser_control/tests cdp_browser/tests/test_cdp_unit.py      # 92 passed (85 + 7)
cd lancedb_memory && .venv/bin/python -m agent_bridge_worker.tests.test_browser_agent   # 21 passed
cd agent_bridge/gateway && php tests/run_tests.php                          # 118 passed, 0 failed
```

`lancedb_memory/.venv` has **no pytest**, so the older spelling
(`../lancedb_memory/.venv/bin/python -m pytest`) fails with `No module named pytest`. Use the `uv run` line above
for the `adws` suites; the worker suite is a plain script and does run under that venv.

These numbers say the rules are right. They say nothing about whether a form on a real site works -- that is the
Verification state section.

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

## Environment (checked 2026-11-07, 20:47 EST)

Facts below were read off the machines, not remembered. Re-check the dated ones before trusting them.

| Piece | Where it actually is |
|---|---|
| Development checkout (source of truth) | `DESKTOP-2OBSQMC`, WSL2 `Ubuntu-26.04`, user `adamsl`, `/home/adamsl/agent_blocks`, branch `main`, remote `git@github.com:EGAdams/agent_blocks.git` |
| Gateway (deployed mirror) | shared host `tinman72@americansjewelry.com` **port 2222**, `~/agent_bridge/gateway/` (outside any docroot). Public base `https://bridge.americansjewelry.com/api/agent-bridge/v1`, MCP at `/mcp` |
| Gateway config + registry | `/home1/tinman72/agent_bridge_data/bridge.php` -- **not in this repo**; `gateway/config/bridge.example.php` is only a template. Re-seed with `php bin/migrate.php` after editing |
| Gateway backups | `/home1/tinman72/agent_bridge_backups/<label-or-timestamp>/`; the form-operations one is `browser_tools_20261108T001059Z` |
| Worker | this box, systemd **user** unit `agent-bridge-worker` running `lancedb_memory/agent_bridge_worker/adw_bridge_worker.py --watch`. Enable flag `~/.config/startrek_browser/enabled` |
| Browser | **Windows** Chrome (not WSL), debug port **9222** on Windows' `127.0.0.1`, profile `%LOCALAPPDATA%\adw-chrome-debug` of the signed-in user |
| `localhost` | ambiguous here, and it has cost hours. WSL loopback != Windows loopback. Port 9222 is Windows'; it is reachable from WSL only with `networkingMode=mirrored` in `.wslconfig` |
| Dashboard (the-factory) | this box, `:8765` (speech endpoints `POST /api/voice`, `POST /api/tts`) |
| Other local ports seen listening | 8766, 8767-8772 (receptionist/Cashy sidecars; Cashy `:8770`), 8787, 8789, 8790 Object Debugger, 8791 `mcp-proxy`, 8792 Factory Ops. None were touched by this work |
| Credentials | gateway client token and DB creds live in `bridge.php` on the host and in this box's environment/`.env` files. **Never copied into docs.** SSH to the host is key-based on port 2222 |

Commands that matter:

```bash
systemctl --user restart agent-bridge-worker        # after any worker-side change
systemctl --user is-active agent-bridge-worker      # "active" is necessary, not sufficient -- see below
journalctl --user -u agent-bridge-worker -n 50      # why a task sat in the queue
ssh -p 2222 tinman72@americansjewelry.com 'cd ~/agent_bridge/gateway && php tests/run_tests.php'
# rollback of the form-operations deploy: copy src/BrowserTools.php and src/McpEndpoint.php
# back out of ~/agent_bridge_backups/browser_tools_20261108T001059Z/ -- nothing else on the host changed.
```

Source vs deployed: the repo copy is the source of truth; the host copy is the mirror. They are **the same bytes
today** -- `md5sum` of `src/BrowserTools.php` (`a69abac0982329d9879e5f887108629d`) and `src/McpEndpoint.php`
(`266b2d7c61de81d5b0ca4f4ba1a2e861`) match on both sides, checked 2026-11-07. Compare them again before assuming
a fix is live. The worker runs straight out of the checkout, so for it "deployed" means committed **and** restarted.

## Verification state -- what was demonstrated, and what only passes tests

| Capability | Planned | Implemented | Tested locally | Deployed | Verified end to end |
|---|---|---|---|---|---|
| `form_controls` | yes | yes | 92 adws / 21 worker / 118 gateway | 2026-11-07 | **yes, via ChatGPT's MCP connector** -- task `t_e584e40c5345f2257fa294ea`, adw `bf9a03f4`, `verified: true` |
| `select_option`, native `<select>` | yes | yes | yes | 2026-11-07 | yes, via the ADW on `aca-prod.accela.com/LARA/Cap/CapHome.aspx?module=OLSR` (adw `7d9c36b1`); Record Type = PLAT Records, confirmed by a second `form_controls` run |
| `select_option`, site-drawn combobox | yes | yes | yes | 2026-11-07 | **yes, via the MCP connector** -- task `t_a48c153161dfea200262b2af`, adw `3c3a7b8a`, "Chose 'Apple' ... it now shows 'Apple'". Also via the ADW on the W3C APG example (adw `709c1901`) |
| `fill`, ordinary text field | yes | yes | yes | 2026-11-07 | yes, via the ADW (adw `0fb57906`), and the full Accela loop ended in `read` returning eight records |
| `fill`, field with its own format or mask (date, `maxlength`, reformatting) | yes | yes | yes, at the CDP boundary (`cdp_browser/tests/test_cdp_unit.py`) | 2026-11-07 | **no.** No live run exists. No public https page with a native `<input type=date>` outside an iframe was found, and `checked_url` forbids `file:`/`data:`, so no page could be authored for it |
| Toyota driving a form | yes | -- | -- | -- | **no.** Deploy step 3. She has no plan that routes to these operations |
| A password step | n/a | refused on purpose | yes | -- | refusal is the feature; do not route around it |
| A file chooser | n/a | refused on purpose | yes | -- | same |

What the green suites do **not** prove: that a form on a real site works. A passing test, a queued task, and an
`active` worker are three different things from a visible, correct result in the page. Only the rows above marked
"verified end to end" were actually driven through the real connector or the real ADW into the real Chrome.

ChatGPT and Toyota are tracked separately on purpose: ChatGPT is verified, Toyota is not.
ChatGPT must refresh its connector to see `browser_form_controls`, `browser_select_option` and `browser_fill` --
the gateway already serves them (`tools/list` returned **35** tools on 2026-11-07). If ChatGPT says it has no
browser tools, that refresh is the first thing to check, not the gateway.

## Telling failures apart (the distinctions that have wasted time)

| Symptom | What it is **not** | How to tell |
|---|---|---|
| A task never answers | not necessarily worker failure | it may be **queued behind a busy worker**. `journalctl --user -u agent-bridge-worker` shows the one it is on. `browser_get_result` with the task id is the right follow-up, not a second submit |
| The local queue looks empty | not proof the gateway queue is | they are two different stores: the gateway's DB on the host, and this box's post office. Check both before saying a task vanished |
| `is-active` says `active` | not proof the agent is reachable | the enable flag, the gateway registry entry, and the client token's scope are three more gates. A tool simply does not appear in `tools/list` if any of them is shut |
| "control port never answered" | not a timeout to raise | two host causes, both diagnosed by `browser_control/link.py`: WSL interop has no working socket for a systemd service (`accept4 failed 110`, so `chrome.exe` never started), or WSL is in NAT mode and cannot reach Windows' 127.0.0.1:9222 at all. More waiting never fixes either |
| Every check passed and nobody saw a window | not success | Chrome opened in Windows **session 0**, the hidden services desktop. That is why `browser.open` has a separate `browser_visibility` phase and the `hidden_session` cause |
| `form_controls` shows a stale page | not a selector problem | the tab may have navigated or posted back. Run `form_controls` again; take selectors only from the latest run. A **guessed selector** is the commonest cause of a failed form step |
| `no_options_shown` | not an empty list | a combobox that filters as you type: `fill` it first, then `select_option` |
| A health or quota figure you could not read | not zero and not healthy | report it **unknown** |

Trace for any run: the ADW id is the execution id. `adw.run:<id>` and `adw.phase:<id>:browser_<op>` are in the
object trace (Object Debugger `recent` / `task`, `:8790`), and arguments plus result are in the SSSF lifecycle log
`adws/adw_data/sssf.db` (`just phases <id>`). A refusal records its `cause` there, so a failed form step is
reconstructable afterwards without re-running it.

Monitoring coverage, stated honestly: each bridge call waits up to `browser_wait_seconds` (default 20) and then
hands back `ready=false` plus the task id -- that is the whole timeout story; there are no retries and no progress
events during a run. **No escalation threshold is configured for a failed or stuck browser task**, and none has
been verified. Next action if that matters: raise it through `lancedb_memory/agent_comms`' alert path, the same way
a failed post-office task already alerts its caller.
