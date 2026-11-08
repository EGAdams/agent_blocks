# Handoff — current checkpoint

**Shift:** Claude (Opus 5), ending 2026-11-08
**Box:** `DESKTOP-2OBSQMC`, WSL2 Ubuntu-26.04, user `adamsl`, `/home/adamsl/agent_blocks`, branch `main`
**Next reader:** Codex / ChatGPT / Claude / Toyota — read `AGENTS.md` first, then this, then the report it points at.

## ChatGPT relay — rebuilt here, and reachable by all three callers

The old relay was `~/letta-code/browser_tools/browser_server.py` driven by hand with `curl` (or by
`~/server_tools/relay_message_to_chatgpt.py`). It is now `adws/adw_chatgpt_relay.py` over
`adws/chatgpt_relay/`, and three callers reach the **same** ADW -- me, ChatGPT over the Star Trek Bridge,
and Toyota. `browser_server.py` and the Chrome in front of it are unchanged; what was rebuilt is everything
on this side of its HTTP.

Read `adws/chatgpt_relay/DESIGN.md` first -- it names every GoF pattern and where it lives, the five rules
that are enforced at every layer, and the two numbers that were measured rather than chosen.

| layer | file | state |
|---|---|---|
| the operations | `adws/chatgpt_relay/` | **verified live.** `relay.ask` 7/7 phases, ADW id `5ea1a596`, `bati says: RELAY OK`, `settled: true` |
| the ADW | `adws/adw_chatgpt_relay.py` | same run |
| the worker agent | `lancedb_memory/agent_bridge_worker/chatgpt_relay_agent.py` + `adapters/chatgpt_relay_process.py` | 14 offline tests pass; registered in `adw_bridge_worker.py` behind `~/.config/startrek_relay/enabled`, which **does not exist yet** |
| ChatGPT's door | `agent_bridge/gateway/src/RelayTools.php` (nine `chatgpt_*` tools) + `Bootstrap.php`, `McpEndpoint.php` | 13 gateway tests pass (131 total, 0 failed); **not deployed to the host** |
| Toyota's door | `receptionist_team/bridge_client/relay_plans.py`, `agents/bridge_chatgpt/`, `tests/test_bridge_chatgpt_route.py` | 101 offline checks pass; **no live run through her** |
| my door | `~/.claude/skills/chatgpt-browser-relay/SKILL.md` | rewritten to run the ADW; the raw HTTP table is kept only for debugging the relay itself |

So: the relay works, and two of the three front doors are built and tested but have never carried a real
message. Don't report either as verified.

**Three things to finish, in this order.**

1. `touch ~/.config/startrek_relay/enabled` (and `mkdir -p` its directory). Until that file exists the
   worker does not register the agent at all, so every `chatgpt_*` tool answers "no such agent".
2. Deploy the gateway files -- `RelayTools.php`, and the modified `Bootstrap.php` and `McpEndpoint.php` --
   plus an `agents` entry for `chatgpt-relay` with workspace `chatgpt-relay` (one name, used as both; see
   DESIGN.md's last section for why). Established procedure: scp with a backup under
   `~/agent_bridge_backups/`, `php -l` under ea-php83, restart the worker. **This is outward-facing and
   needs EG's yes first** -- I did not do it.
3. One live message each way: ChatGPT asking a session something, and Toyota's `bridge_chatgpt` route.

**Known, not fixed:** `receptionist_team/tests/test_latest_user.py`'s prompt-budget check fails --
Toyota's system prompt plus `LIVE_CAPABILITIES` is 13341 characters against a 12500 ceiling. It was already
over at 13002 before this work; `bridge_chatgpt`'s roster blurb added 339. The ceiling is real (Groq's 8000
tokens *per minute*), so the fix is trimming her prompt, which is its own job.

## Michigan plat diagnostic — the "More Details" ambiguity, and the empty attachment list

The work order: ChatGPT could read the Accela OLSR record `PLATS-38123` ("FRALICK'S ADD.") through the Bridge but
could not press **More Details** ("ambiguous", three tasks spent on it), and a fresh read of **Attachments**
returned upload rules and no filenames. Commit **`ea4d1ca`**.

**Confirmed cause of the ambiguity.** The real control is **`a#lnkMoreDetail`** — an icon anchor with **empty
text**, `title="Expand More Details"`, `href="javascript:void(0);"`, calling
`ControlDisplay($get("TRMoreDetail"), $get("imgMoreDetail"), …)`. The visible words "More Details" belong to a
*separate* `span#ctl00_PlaceHolderMain_PermitDetailList1_lblMoreDetail`. Twelve elements on the page contain that
text and four match it exactly, so click-by-text can never resolve to one — and the one element that has to be
pressed carries none of the text at all. Taking the first match would press a heading and report success.

**Confirmed cause of the second failure.** `a:has-text('More Details')` is a **Playwright** selector, not CSS.
`document.querySelectorAll` raises `SyntaxError`, and `CdpWorkflowBrowser.find()` catches `exceptionDetails` and
returns `[]` — so an invalid selector is indistinguishable from "nothing matched". That is why it read as "none".

**Confirmed cause of the invisibility.** `div#tab-record_detail.record-tab-content` was `display: none` — it is
not the active tab panel. Every ancestor was 0×0, `body_text()` uses `innerText`, so the panel's 9,749 characters
were unreadable, and the resolver rejects zero-size elements, so even a correct selector refuses until the panel
is shown. The Record Details tab link itself lives in a `ul.dropdown-menu` with `display:none` under the visible
`a[title="Record Info menu, press tab to expand"]` — a two-step: open the menu, then the tab.

**What changed.** Two files, both in the existing ADW, no new mechanism:

- `adws/browser_control/operations.py` — `Click.perform`'s refusal now appends a new `_targeting_hint`, which
  reads the page's own `form_controls` and **names the candidates with their selectors**. `ambiguous`, `hidden`
  and `zero_size` are the same mistake (the words name several things, or the thing is in the page but not on
  it), so they get the same answer: run `browser.form_controls` and aim with the selector it reports. The loop
  ChatGPT got stuck in was that the refusal said "ambiguous" and never said what to do next.
- `adws/cdp_browser/forms.py` — `FORM_CONTROLS_JS`'s selector gained `a[onclick]`. Accela's "Record Details" tab
  anchor has no id, so `form_controls` could not see it, which made the control that reveals a whole panel
  impossible to find by any supported route. The pre-existing `acts` test still drops anchors that are only
  navigation, and `cssFor` already emits `a[title="Record Details"]` for it.

**Focused tests:** `cd adws && uv run --quiet --with pydantic --with pytest --with aiohttp --with selenium pytest
-q browser_control/tests cdp_browser/tests` → **95 passed, 2 skipped** (was 92/2). Three new tests cover the
ambiguous path, the hidden path, and that a plain "found nothing" gets no targeting lecture.

**Acceptance 1 — met, with ADW evidence.** Record Info menu (`aa19790c`) → Record Details (`7c690ef0`) →
`#lnkMoreDetail` (`ed5fed1c`, `verified: true`); the toggler flipped to "Collapse More Details" and
`TRMoreDetail` from `display:none` to `table-row`. Then `#lnkASI` (`b67a0748`), `#lnkParcelList` (`a659ef4f`),
and `browser.read` (`bf686264`, 1080 chars) returned: **County KENT**, Subdivision **FRALICK'S ADD**, Section 31,
Town 07N, Range 11W, Index Code 3372, **Number of Sheets 0**, "No parcel information".

**Acceptance 2 — verified against the rendered page; this is NOT a Bridge limitation.** With `tab-attachments`
active (`display:block`, class `show`, rect 1003×286, `ready: complete`, `pendingAjax: false`, no spinner), the
panel holds only the section header, the upload rules and the upload widget. `attach.tables` was empty, `fileRows`
was 0 page-wide, and the two links Accela renders when documents exist — `#lnkViewRecordDocument` and
`#lnkViewPeopleDocument` — are both server-rendered with class `ACA_Hide` and `display:none`. Neither of the
record's two Reports is a plat image.

**Hypothesis, kept separate.** The session is **anonymous** (`#ctl00_HeaderNavigation_btnLogin` present, no
logout, `$.global.isAdmin === false`). A survey of **10 distinct OLSR plat records** found the identical hidden
state on every one. So the DOM **cannot distinguish** (a) the state attached no scans to these records from
(b) ACA hides attachments from signed-out visitors. The decisive test is to sign in — **EG's step**: no agent
here types a credential into a page.

**Acceptance 3 — no plat image is reachable by this route, with negative evidence.** The MI_Plats ArcGIS
ImageServer (`imagery.michigan.gov/server/rest/services/MI_Plats/ImageServer`) returned `features: []` for
`UPPER(Name) LIKE '%FRALICK%'`, and for `%KENT%` only `kent_Rockford_Plat_1996.ers`. Official routes:
**Kent County Register of Deeds**, 300 Monroe Ave NW, Grand Rapids, (616) 632-7610, online records back to
30 Jan 1874 — that is where Liber 4 of Plats, Page 31 lives; and **LARA OLSR**, 517-241-6321 /
bccolsr@michigan.gov. Note Michigan's own ACA Plat Data Search guide documents the download route as exactly
"Record Info → Attachments" — the path that is empty here.

**Acceptance 4 — the transport was never the defect.** "given:text=More Details: ambiguous" is the
`RankedElementResolver`'s own message, so ChatGPT's connector reached the ADW correctly. Both workers are live:
`agent-bridge-worker.service` and `agent-bridge-worker-express.service` active/running. **Toyota: still no live
run** — her `bridge_browser` route remains implemented + 110 offline tests, tracked separately as before.

**Acceptance 5 — partially resolved, and the deed stays untouched.** County **KENT** matches the deed;
"FRALICK'S ADD" matches "FRALICK'S ADDITION"; Section 31 corroborates tax parcel 41-14-**31**-404-015. But the
record states **no liber/page anywhere**, so **"Liber 4 of Plats, Page 31" is not confirmed** and PLATS-38123
remains a *candidate* — the search returned **8 distinct Fralick plats** (PLATS-55826, 66207, 66208, 1698, 38123,
38147, 37113, 47886). `~/notes/2026-11-03_draft-personal-representative-s-deed-of-distribution.md` was **read
only**; its `[VERIFY: … "ACCORDING TO THE RECORDED PLAT THEREOF"]` highlight is deliberately left in place.

**Remaining limitations.** (1) The sign-in question above. (2) `browser.click` on a record-number link reported
`ok` with an empty `moved_to` and did not navigate (`7a540262`); those anchors are plain hrefs with no onclick and
no target — worked around with `browser.navigate`, root cause not yet established (likely the element was out of
view when the synthetic click was dispatched). (3) **Side effect to disclose:** tab 1's UI state changed — More
Details, Application Information and Parcel Information are now expanded (the panel was returned to Attachments,
its original active tab) — and tab 4, originally `about:blank`, now holds an Accela record page.

## Previous checkpoint — Bridge browser form controls

## Objective and acceptance

"Fix Star Trek Bridge browser controls — dropdowns and form fields," so **ChatGPT can operate website forms
through traceable ADWs.** Accepted when ChatGPT, through its own connector, can list a page's form controls,
set a dropdown, type into a field, submit with the page's own button, and read the result back — with every
step an ADW run that has an id, a lifecycle log and an object trace, and with passwords and file choosers
refused rather than worked around.

## What changed, and what was actually demonstrated

Three new operations (`browser.form_controls`, `browser.select_option`, `browser.fill`) plus three closed gaps:
a dropdown the **site draws itself** (open it, click the item), a field that **takes no typed keys** (a date or
masked input, told its value through the prototype setter so a framework notices), and a field that
**reformats** what it is given (now a success with a note, not a mismatch). Two refusals stayed refusals on
purpose: credential fields and file choosers.

The full report — reused parts, the two opposite kinds of dropdown, every failure `cause`, the safety rules, the
environment table, and a row-by-row planned → implemented → tested → deployed → **verified end to end** ladder —
is **[`agent_bridge/docs/BROWSER_CONTROL.md`](agent_bridge/docs/BROWSER_CONTROL.md)**. Read that before changing
any of it. The agent-facing loop is `.claude/skills/browser-form-control/SKILL.md`.

Verified 2026-11-07, re-checked while writing this:

- Suites green: `adws` **92** (`uv run ... pytest -q browser_control/tests cdp_browser/tests/test_cdp_unit.py`;
  note `lancedb_memory/.venv` has no pytest), worker **21**, gateway **118 passed, 0 failed** — locally **and**
  on the host.
- Deployed to the gateway host with a backup first (`~/agent_bridge_backups/browser_tools_20261108T001059Z/`);
  `md5sum` of the two PHP files is identical on both sides today; `php -l` clean under ea-php83; worker restarted
  and `active`.
- **End to end through ChatGPT's real MCP connector:** `tools/list` → 35 tools including the three new ones;
  `browser_form_controls` (task `t_e584e40c5345f2257fa294ea`, adw `bf9a03f4`) and `browser_select_option` on a
  site-drawn combobox (task `t_a48c153161dfea200262b2af`, adw `3c3a7b8a`) both `verified: true`.

Not demonstrated, and not to be claimed:

- **Toyota's form route is written and tested offline, but has never reached a real page.**
  `receptionist_team/bridge_client/browser_plans.py` (one Plan per operation, same shape as `notes_plans.py`),
  the `bridge_browser` specialist, her `agents/bridge_browser/` folder, and 110 offline checks in
  `tests/test_bridge_browser_route.py`. Same Bridge agent, worker and ADW ChatGPT drives -- a second caller, not
  a second mechanism. Not to be called verified until one live run goes through her. ChatGPT and Toyota are
  still tracked separately for this reason.
- **No live run of a date / masked / `maxlength` field.** Covered only by CDP-boundary unit tests; no public
  https page with a native `<input type=date>` outside an iframe was found, and `checked_url` forbids
  `file:`/`data:` so one could not be authored.
- **ChatGPT must refresh its connector** to see the three tools. The server already serves them. Nothing else is
  pending on our side.
- **No escalation threshold** is configured for a stuck or failed browser task — each call just returns
  `ready=false` and a task id after `browser_wait_seconds` (default 20). Gap recorded in the report with the
  next action.

## Git synchronization (re-checked 2026-11-07, 23:30–23:40 EST)

Repo: `agent_blocks`, intended branch `main`, intended remote commit **the tip of `origin/main`**. Hashes in the
table below were true at the times given and go stale the moment they are written -- re-run
`git fetch origin && git rev-list --left-right --count origin/main...HEAD` on each machine rather than trusting
one of them.

| Machine | Role | Path / context | Branch | HEAD | vs `origin/main` | Dirty | Checked |
|---|---|---|---|---|---|---|---|
| `DESKTOP-2OBSQMC` (EG) | development + bridge worker + debug Chrome | `/home/adamsl/agent_blocks`, WSL2 Ubuntu-26.04 | `main` | `ea4d1ca` | **level, pushed** | 3 **pre-existing, deliberately untouched** files: `adws/adw_data/mechanic_translator/mechanic_instruction.txt`, `adws/adw_interface_browser_open.py`, `catherine_agent_sdk` | 23:38 EST |
| `DESKTOP-SHDBATI` (BATI Win10, Letta agents server) | Letta server + ChatGPT relay + maps tool | `/home/adamsl/agent_blocks`, WSL on that box | `main` | `d5c8f9f` | **2 behind** (`a0afda6`, `ea4d1ca`) — **pending checkout, not synchronized** | clean | 23:35 EST |
| `Rosemary46` (Mom's) | — | `~/agent_blocks`, WSL at tailscale `100.72.34.38`, **sshd on port 2222** | `main` | `d5c8f9f` | **2 behind** (same two) — **pending checkout, not synchronized** | clean | 23:37 EST |

**Verified vs pending, stated plainly.** EG's box is verified level with `origin/main` and pushed. Both other
machines were **reached and read this shift** — their branch, HEAD and dirty count are measured, not assumed —
and both are **two commits behind**; nothing was pulled on them, so they are pending checkouts, not synchronized
ones. `git -C ~/agent_blocks pull --ff-only` on each closes it. Nothing is running out of either checkout that
this shift's commits affect, so being behind is not currently breaking anything.

Two things about reaching them:
**Rosemary46's sshd listens on 2222**, which `~/.ssh/config` records for `100.72.34.38` — port 22 there is
genuinely refused, so that is not a surprise default. And the first ssh after an idle period often times out
while tailscale sets up a path — this shift `tailscale ping rosemary46-24` **timed out entirely** ("no reply")
yet the second ssh attempt connected, so a failed tailscale ping is **not** evidence of a dead host. Use a retry
loop with a sentinel marker. `desktop-shdbati` answered its ping over DERP (~60ms, no direct connection) and
connected first try.

`ssh desktop-shdbati` by **name** now works. The earlier `Host key verification failed` was *not* a stale or
conflicting key: `ssh-keygen -F desktop-shdbati` (needed because `known_hosts` is hashed, so grep finds nothing)
showed **no entry at all** for the short name or the FQDN, and a non-interactive ssh cannot prompt to accept an
unknown host, so it fails instead of asking. Fixed by a `Host desktop-shdbati desktop-shdbati.tailb8fc54.ts.net`
block in `~/.ssh/config` giving it `HostName 100.80.49.10` and `StrictHostKeyChecking accept-new`
(backup: `~/.ssh/config.bak.20261108T015953Z`). No `known_hosts` entry was deleted — none needed to be. Short
name, FQDN and the existing `desktop-shdbati-wsl` alias all return `DESKTOP-SHDBATI`.

Gateway host is **not** a git checkout: it is a file mirror, synchronized by `scp` with a backup first. Git alone
never deploys it. Databases, agent memory and credentials are likewise outside git — `bridge.php` on the host,
the LanceDB stores, `adws/adw_data/*.db`, and the gitignored spreadsheet and mechanic run directories. Pushing
commits preserves none of those.

## Where this sits among the other projects

Owners, so a change lands in the right repo:

- **Star Trek Bridge** — gateway PHP in `agent_bridge/gateway/` (this repo, mirrored to the host); worker and
  agent cards in `lancedb_memory/agent_bridge_worker/` + `lancedb_memory/agent_comms/`. Data flow:
  ChatGPT → MCP `/mcp` → gateway → task row → worker (`--watch`) → `BrowserAgent` → `UvBrowserCommands` →
  `adws/adw_browser_control.py` → CDP → Windows Chrome → verified result back up the same path.
- **ADWs** — `adws/`. `adw_modules/` is shared and **not edited from here**. Browser Control is
  `adws/browser_control/` over `adws/cdp_browser/`; the click recorder/playback is `adws/browser_workflows/`;
  Cashy (bank navigation) is `adws/cashy/`. Each reads its own `DESIGN.md` first.
- **the-factory dashboard / Notes / Worker Activity** — `/home/adamsl/the-factory` and
  `/home/adamsl/letta-code/dashboard`, served on `:8765` on this box. Browser Control only *drives* it
  (`browser.dashboard_notes`); it does not own it. **Notes reading, editing, saving, refreshing and persistence
  were not exercised this shift** — `browser.dashboard_notes` proves only that the tab opens and NOTES is showing.
- **Object Debugger** — `lancedb_memory/object_trace/`, HTTP on `:8790`. Every ADW run above is traceable there.
- **Machine Ops / Factory Ops** — `:8792`, independent of the Bridge. Untouched.
- **Supervisors / watchdogs** — runbook `/home/adamsl/letta-code/dashboard/docs/adw-supervisor.md`.
- **Toyota / Receptionist, Mazda** — `lancedb_memory/receptionist_team/`. Toyota is the consumer that still has
  no route to the form operations.
- **Agent creation and maintenance** — `adws/adw_toyota_mechanic.py` + guard; runs land in
  `adws/adw_data/mechanic/` (gitignored).
- **Nonprofit finances, receipts, bank reconciliation, tax, probate** — `/home/adamsl/rol_finances` and the
  `nonprofit_finance` MySQL books, read-only through `ReadOnlyBooks`. Generated sheets in
  `adws/adw_data/spreadsheets/` (gitignored: regenerable, and real medical and payroll rows). Nothing in this
  shift touched them, and **no deed or business document was modified.**
- **Scanners / printers** — reached through the post office's cards, owned elsewhere.
- **Raspberry Pi / HUB75 scoreboards** — separate product checkouts; see the
  `scoreboard-checkout-migration` skill. Unrelated to this work.

Treat that list as a map, not a claim that every link is finished.

## Engineering decisions worth keeping

- **Every user-facing automated action is an ADW**, including the simple ones, so it has an id, a lifecycle log
  and an object trace. The skill invokes the ADWs; it never reaches past them into Selenium, Playwright, or
  screenshot-and-guess.
- **One class per rule, one per operation.** A new operation is a class plus a line in `catalog.ORDER`; a new way
  of finding an element is a `register()` call, not an edit. Written against `I`-prefixed `Protocol`s
  (say "interface", never "port"); Pydantic models for data crossing a boundary; the composition root is the only
  place that names concretes.
- **Chrome only**, on the Windows desktop, over CDP — no WebDriver. A window opened any other way has no debug
  port and cannot be steered.
- **Reuse before building.** `browser_session`, `browser_workflows`' resolver and consequence classifier,
  `browser_workflow_adw`, and the existing gateway/worker/policy path were all reused unchanged;
  `browser_workflows`' default strategy registry was deliberately left alone so the recorder keeps working.
- **Verified means read back.** A step answers `verified: true` only when the ADW re-read the thing it claims,
  and the worker treats `ok` with `verified: false` as a failure. A submitted form is never a result.
- **Agents get no approvals and no secrets.** A consequential click needs `confirm` and EG's say-so in the
  conversation; a password step stops. Both ChatGPT and Toyota must be able to use the Bridge — that constraint
  stays.
- **EG's working preferences:** commit and push straight to `main`, no branches; short, direct answers; avoid the
  words "idempotent", "fail-closed", "canonical"; keep hands-free workflows working; don't re-ask for permission
  already given.

## Next actions, in order

1. **ChatGPT:** refresh the connector. (EG's, not an agent's.)
2. **Toyota:** drive one form through her for real — ask her for the page's controls, set a dropdown, type a
   field, press the page's own search button, read the results — and only then mark her row verified. Her code
   and tests are done; what is missing is the live run.
3. Optional: a live date-field run, if a suitable https page turns up; and an escalation threshold for a stuck
   browser task.
4. Open question for EG, not changed on his behalf: every `implement` step of hers is still read back and waits
   for a spoken yes, so a form is one yes per field. That protection was kept rather than relaxed for the form
   loop; say the word if you want the loop hands-free.

No decision or permission is outstanding. Commit and deploy were authorized in the session that did them
(`24b5286` the work, `938b1e5` the deploy record, plus this documentation commit).

---

# Earlier handoff — Permanent Object Debugger Bridge Agent

**From:** Claude (Sonnet 5) shift ending 2026-11-06
**To:** next shift (Codex, reads `AGENTS.md`)
**Current status (Codex, 2026-11-06):** executor implemented, 114 tests passed,
live telemetry verified through real detached workers, and the local bridge
worker restarted. **Remote gateway registration approved and deployed.**
Star Trek Bridge discovery lists `object-debugger`; its `status`, `recent 5`,
and `task` requests all completed successfully through the actual connector.
See [the implementation and deployment report](agent_bridge/docs/OBJECT_DEBUGGER.md)
for changed files, verification, a sample response, and the prepared gateway
entry. SSH uses port **2222**. Remote config and registry updated with a backup;
no commit or push performed. Paste-ready ChatGPT instructions are in
`agent_bridge/docs/OBJECT_DEBUGGER_CHATGPT.md`.

The original design handoff follows; its "not yet coded" statements describe
the starting point, not the current implementation.

## Task

Full brief (verbatim) is in the conversation that produced this handoff; short version:

Create a read-only `object-debugger` agent, reachable from ChatGPT through Star Trek Bridge, that lets ChatGPT inspect live ADW object/task lifecycle events (the same data `http://127.0.0.1:8790/object-debugger.html` already shows). Three structured requests: `status`, `recent`, `task <id>`. Must reuse existing telemetry/adapters, validate input, cap output, redact secrets, never write, never expose shell. Workspaces: `/home/adamsl/agent_blocks` and `/home/adamsl/the-factory`.

Acceptance: ChatGPT can run `list_available_agents`, see `object-debugger`, and pull real events from this machine through the bridge — no Cloudflare tunnel.

## What's already decided (don't re-derive — just build it)

1. **No new MCP gateway operation.** The remote gateway's `submit_agent_task` tool only accepts `operation` from a fixed, narrow enum (`inspect`/`explain`/`search_memory`, +`implement` for writers) — see `agent_bridge/gateway/src/McpEndpoint.php` and `Policy.php`. There is no protocol room for literal `status`/`recent`/`task` operations without editing live PHP on the shared host. **Decision: implement all three as free-text-classified sub-requests inside one `inspect` capability**, parsed from `Envelope.text`. This matches existing patterns in this repo (Mickey's `narrate.py`, `AdwLiveFinanceRunner`'s question registry).

2. **Reuse `object_trace.inspector.Inspector` directly, in-process** (same class the HTTP debugger on port 8790 already uses via `object_trace/service.py`). Do not write a new telemetry reader. Mapping:
   - `status` → `Inspector.health(build, store) -> Health`
   - `recent` → `Inspector.changes(after_seq, limit) -> dict`
   - `task <id>` → `Inspector.ask("where_stopped", subject=task_id) -> Answer` (already resolves a bridge task id to ADW phases/gateway state/evidence)

3. **Store access:** open `object_trace.adapters.sqlite_store.SqliteTraceStore(path, read_only=True)`. Default path from `object_trace.factory.trace_db_path()`. A missing DB raises `sqlite3.OperationalError` on open — that *is* the "unavailable source" test case; don't auto-create the DB (the debugger's own `service.py` does that for serving convenience, but the new executor must not).

4. **Composition points — exactly 4 files, no others need touching:**
   - `lancedb_memory/agent_comms/executors/object_debugger.py` — **new file**, the actual `ITaskExecutor` implementation (see interface shape below). Not started.
   - `lancedb_memory/agent_comms/factory.py` — add one new entry to `default_builders()`'s returned dict, key `"object_trace"`, building the new executor. (Confirmed: `adw_bridge_worker.py` and `service.py` need zero changes — they pick up new agents purely from config.)
   - `lancedb_memory/agent_comms/agents.json` — add a new card, e.g.:
     ```json
     {
       "name": "object-debugger",
       "display_name": "Object Debugger",
       "runtime": "object_trace",
       "settings": {},
       "capabilities": [
         { "task_type": "inspect", "description": "status/recent/task lifecycle events from the ADW object trace", "writes": false, "default_wait_seconds": 30 }
       ],
       "accepts_from": ["chatgpt-bridge"]
     }
     ```
     (Confirmed field shapes against `agent_comms/models.py`'s `AgentCard`/`Capability` — see below.)
   - `lancedb_memory/agent_bridge_worker/workspaces.json` — add a read-only entry, e.g. `"object_trace": { "path": "<abs path to lancedb_memory/object_trace>", "writes": false }`. Confirmed in `agent_bridge_worker/workspaces.py`: a `writes:false` entry only needs to be an absolute, symlink-free, existing directory — does **not** need to be a git repo.

5. **Redaction backstop:** reuse `agent_bridge_worker.redaction.SecretScanner` — `SecretScanner().found(text) -> list[str]` (names the *kind* of secret found, never the value). Convention in this codebase: if anything is found, **withhold the whole response text**, don't try to cut just the secret out. Apply this to the final text+details before returning, in every branch.

6. **Task-id validation:** reuse the gateway's own regex, `^t_[0-9a-f]{24}$` (from `agent_bridge/gateway/src/GatewayApp.php`'s `ClientTasks::owned()`). Validate locally before ever touching the store — this is the "malformed task ID" test case.

## Exact interface shapes to implement against

```python
# agent_comms/ports.py
class ITaskExecutor(Protocol):
    @property
    def performer(self) -> str: ...
    async def execute(self, envelope: Envelope) -> TaskResult: ...
    # NEVER raise for a failed piece of work -- a failure is TaskResult(ok=False).
    # Only raise if you couldn't even try.

# agent_comms/models.py
class Envelope(BaseModel):
    id: str; kind: MessageKind; sender: str; recipient: str
    task_type: str; text: str                      # <-- the free-text prompt to classify
    context: dict[str, Any] = {}
    priority: int = 50; correlation_id: str
    parent_id: str | None = None; trace: list[str] = []

class TaskResult(BaseModel):
    ok: bool; text: str; performer: str             # e.g. "object_trace:inspector"
    cost_usd: float | None = None
    details: dict[str, Any] = {}

# object_trace/inspector.py
class Inspector:
    def __init__(self, reader: ITraceReader, clock: ITraceClock, sssf: SssfReader | None = None, ...): ...
    def health(self, build: str, store: str) -> Health: ...
    def changes(self, after_seq: int, limit: int = 200) -> dict: ...
    def ask(self, question: str, subject: str | None = None) -> Answer: ...

# object_trace/models.py
class Answer(BaseModel):
    question: str; found: bool; answer: str  # max_length=1200
    evidence: list[Evidence] = []; missing: list[str] = []; subject: str | None = None

class Health(BaseModel):
    ok: bool; build: str; schema_version: int; objects: int; events: int
    max_seq: int; last_event_at: float | None; now: float; store: str; sssf: str
```

`object_trace.tracer.SystemClock().now() -> float` (epoch seconds) is the clock to inject into `Inspector`. `object_trace.service.source_build()` already computes the `build` string `health()` wants — reuse it, don't recompute.

## Classification plan (not yet coded)

Most-specific-first, on `envelope.text`:
1. Regex search for `t_[0-9a-f]{24}` anywhere in the text → `task` request with that id.
2. Else if the word "task" appears but no valid id matched → `task` request with `arg=None` → respond with the malformed/missing-id message (this *is* the malformed-task-id test case; don't query the store).
3. Else if text matches `recent|events|changes|lifecycle` → `recent` request. Optionally parse a trailing number (`recent 10`) as the limit, hard-capped (suggest cap=200, default=50) — this is the brief's "bounded ... events" + "cap output sizes" requirement.
4. Else → default to `status` (safe, cheap, matches "source connectivity, heartbeat, freshness, errors").

Freshness/staleness for `status`: compare `Health.now - Health.last_event_at` against a threshold (not yet chosen — 24h/86400s is a reasonable default; make it a constructor param so tests can inject a small threshold).

## Still-open items for the next shift

- **Write the executor file itself** — nothing written yet. Follow this repo's closing-bracket spacing convention in `lancedb_memory` files touching `agent_comms`/`agent_bridge_worker` style areas — check sibling files (`executors/basic.py`) for the exact look before writing.
- **Tests.** This repo does *not* use pytest for these packages — bespoke `test_*.py` scripts run directly (see `agent_comms/tests/test_operations.py` for the pattern: `sys.path.insert(...)`, a `_kit()` helper building a temp `build_post_office(...)`, plain `assert`). Need coverage for: unavailable source (missing DB file), stale source, malformed task id, unauthorized sender (not in `accepts_from`), and redaction (inject a fake secret into a fake `Health`/`Answer` and confirm it's withheld, not leaked).
- **End-to-end verification once wired:**
  - Confirm `t_19133b18bee29034b4be59c7` is still resolvable via the new `task` request, if the object_trace DB retained it.
  - Run one new harmless test task through the real bridge worker and confirm the new agent reports real events it produced.
- **Remote gateway registration — DO NOT do this without asking the user first.** `list_available_agents` is served by the remote PHP gateway's own config (`agent_bridge/gateway/config/bridge.example.php` is only a template; the real file lives outside this repo at `/home1/tinman72/agent_bridge_data/bridge.php` on the shared host `tinman72@americansjewelry.com`). Registering the new agent there means an SSH edit to a live production system — hard to reverse, affects a shared service. Surface this step to the user explicitly before touching it, even though the brief's wording authorizes it ("Register the new agent with the actual bridge gateway"). The needed config shape (confirmed from `Policy.php`'s `PolicyAuthorizer::authorize()`):
  ```php
  ['name' => 'object-debugger', 'operations' => ['inspect'], 'workspaces' => ['object_trace']]
  ```
  (workspace name here must match whatever key you used in the local `workspaces.json`.)
- **Deployment (Phase 4):** after tests pass, only restart the `agent-bridge-worker` systemd **user** service (the one running `adw_bridge_worker.py --watch`). Do **not** touch Factory Ops (port 8792), the unrelated `mcp-proxy` (port 8791), or the dashboard (port 8765) — all three are explicitly required to stay untouched by the brief.
- **Final report required by the brief:** root cause/design rationale, changed files, test results, deployed agent registration, and a sample sanitized response. Nothing to report yet since no code exists.

## Repo conventions worth knowing before touching this

- Root `CLAUDE.md` in `agent_blocks` documents the whole architecture — read it, it's authoritative for file layout, DIP boundaries (`agent_comms` is lower-level; nothing in it should import from `agent_bridge_worker` except via the documented one-way dependency `agent_bridge_worker → agent_comms`. Importing `redaction.SecretScanner` from an `agent_comms` executor is a deliberate, acknowledged exception for reuse of a single source of truth on secret patterns — don't "fix" it by duplicating the regexes instead).
- `ReportFindings`/code-review tooling aside, this user does not want speculative abstractions — implement exactly the three structured requests, nothing more generic.
- User preference (from persistent memory, applies across all sessions in this account): commit and push directly to `main`, no feature branches; avoid jargon words "idempotent"/"fail-closed"/"canonical" in any user-facing text; interfaces are named `ISomething`, never `*Port`.

## 2026-11-06 — failed supervisor task repaired locally

User authorized fixing the defects diagnosed in `t_cb29dcb55654efa68f29fee9` and
requested a message for ChatGPT. Original task/history/patch preserved; do not
replay its write ID. Main runbook:
`/home/adamsl/letta-code/dashboard/docs/adw-supervisor.md`; ready-to-send message:
`/home/adamsl/letta-code/dashboard/docs/adw-supervisor-chatgpt-handoff.md`.
Supervisor now reads durable post-office rows independently, handles real timing,
marks stale/unknown/incomplete coverage honestly, and renders escalation state.
Runtime traces carry future Claude limits/outcome/PIDs; ADW run IDs and worker-app
closure are repaired. Settled failed writes run configured checks; `letta_code`
has focused supervisor validation. Service example prepared, not activated.
No service restart, credential change, remote deployment, commit, or push.
See runbook for exact verification and remaining gateway/dispatch/activation work.
