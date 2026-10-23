# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Scope

**Two receptionists, one engine.** **Toyota** is the conversational front door
to `finance_team`'s headless roles; **Mazda** is a document-verification
receptionist whose specialists are Letta agents this package can't reach yet.
They are not two packages — they are two `ReceptionistProfile` values in
`profiles.py`, both assembled by `main.py`'s `build_receptionist()` and both
selected at the command line with `--agent {toyota,mazda}`. (`mazda_team/` used
to be a separate package; it was retired, and re-creating it would undo the
point.)

Source files, by job:

| file | job |
|---|---|
| `profiles.py` | `ReceptionistProfile` — everything that differs between the two |
| `rosters.py` | `Roster` — which agent names a receptionist may route to |
| `delegation.py` | `IDelegationRunner`, `IRouteOverride`, `UnwiredRunner`, `RunnerMap` — *how* a route runs, and the one route the model doesn't get to choose |
| `roles.py` / `extra_roles.py` | Toyota's roster wording, blurbs, spoken names, extra `AgentRole`s |
| `mazda_roles.py` | Mazda's six specialists (five stubs + Mickey) and her rendered prompt |
| `main.py` | the composition root: `build_receptionist()`, `build_toyota()`, `repl()` |
| `service.py` / `service_models.py` / `turn_runner.py` | the loopback HTTP sidecar the dashboard talks to |
| `adapters/` | `routing_model`, `status_route`, `figure_route`, `latest_user`, `handoff_model`, `tool_agent_runner`, `mickey_runner`, `adw_runner`, `live_finance_runner`, `voice_io`, `metrics` |
| `progress/` | Mickey: `tracker` reads report files, `narrate` prompts, `mickey` composes the two |
| `operations/` | Toyota-only `WorkflowModel` stack (reports/tax intents, assignment policies) |
| `memory_desk.py` | the shared-memory recall wiring both receptionists use |
| `forget.py` | deletes turns the agent got wrong, so recall stops quoting them back |
| `adapters/figure_route.py` | Toyota's `IRouteOverride`: a question about a real figure always reaches the books |
| `adapters/live_finance_runner.py` | spawns the Live Finance ADW and repeats its line, with no model in the path |
| `adapters/latest_user.py` | the one definition of "the last thing the user said", shared by both overrides |

Everything else comes from `agent_memory/` (the engine) and `finance_team/`
(the roles Toyota delegates to).

Plus `agents/` — **one folder per agent, both receptionists included**, holding
the prompts, model config, tool declarations and documentation that used to be
Python literals. `agents/README.md` is authoritative for that subtree; it has
its own style rules and its own loader. Read it before editing anything under
`agents/`, and the `/new-receptionist-agent` skill before adding an agent.

There are **two front ends over one `build_receptionist()`**: the terminal REPL
(`main.py`) and a loopback HTTP sidecar (`service.py`) that the letta-code
dashboard talks to. The sidecar is Toyota-only today. See *The sidecar* below
before touching either.

Read in this order, preferring the more specific on conflict:
`../../CLAUDE.md` (workspace) → `../CLAUDE.md` (commands, invariants,
known doc drift) → `../README.md` (narrative) → this file.

## Running

All commands run from `lancedb_memory/` (one directory up), never from here.
That directory holds both halves of what this package needs: the `.venv`
(Python 3.14) with every dependency, and the import root — `receptionist_team`,
`finance_team`, and `agent_memory` are sibling packages *inside* it, so it is
the only cwd where `-m receptionist_team.main` resolves. Running it from this
directory fails with `No module named 'receptionist_team'`, and invoking the
interpreter as `../.venv/bin/python` additionally trips `sys.prefix` warnings.
Bare `python3` has none of the dependencies (PEP 668 externally-managed), so
always use `.venv/bin/python`.

```bash
cd ..
.venv/bin/python -m receptionist_team.main                     # Toyota, typed chat
.venv/bin/python -m receptionist_team.main --voice             # mic in / spoken out
.venv/bin/python -m receptionist_team.main --agent mazda        # Mazda, same engine
.venv/bin/python -m receptionist_team.main --agent mazda --voice
.venv/bin/python -m receptionist_team.main --session toyota --embedding-profile sentence
.venv/bin/python -m receptionist_team.main --role-model claude-haiku-4-5-20251001
.venv/bin/python -m receptionist_team.progress.cli             # Mickey's snapshot, no LLM
.venv/bin/python -m receptionist_team.service --port 8767   # the dashboard sidecar
```

Normally you don't run `service.py` by hand — it is the user unit
`toyota-receptionist.service` (`systemctl --user status toyota-receptionist`,
log `/tmp/toyota_receptionist.log`), restartable from the dashboard's Server
Management tab. A hand-started copy will collide on the port with the unit.

Tests cover the sidecar's turn loop, the agent folders, both profiles, the
delegation acknowledgment, the operations stack and Mickey's tracker — sixteen
standalone scripts in `tests/`, each run as a module from `..`:

```bash
.venv/bin/python -m receptionist_team.tests.test_service_turns
.venv/bin/python -m receptionist_team.tests.test_mazda_profile
.venv/bin/python -m receptionist_team.tests.test_delegation_acknowledgment
.venv/bin/python -m receptionist_team.tests.test_mickey_routing
.venv/bin/python -m receptionist_team.tests.test_progress_tracker
.venv/bin/python -m receptionist_team.tests.test_status_route
.venv/bin/python -m receptionist_team.tests.test_adw_browser_runner
.venv/bin/python -m receptionist_team.tests.test_search_open_adw
.venv/bin/python -m receptionist_team.tests.test_figure_route
.venv/bin/python -m receptionist_team.tests.test_live_finance_adw      # one test reaches the real DB
.venv/bin/python -m receptionist_team.tests.test_live_finance_runner
.venv/bin/python -m receptionist_team.agents.tests.test_agent_catalog
.venv/bin/python -m receptionist_team.agents.cli validate
```

They inject fakes, so no LanceDB, no Groq, no Claude SDK, no network —
`test_mazda_profile.py` deliberately builds Mazda's whole routing stack
*without* importing `claude_agent_sdk`, which only holds because
`profiles.py`'s runner factories import their adapters inside the function.
Everything *below* the sidecar is still verified by running the REPL:
`handle_turn` goes to a real LanceDB table, and a Toyota delegation runs the
Claude Agent SDK with tools against the live MySQL DB. The async harness is
`../tests/runner.py` and the shared fakes are `../tests/fakes.py`.

`main.py` parses `receptionist_team/.env` itself via `os.environ.setdefault`
(no `python-dotenv` in the venv); copy `.env.example` beside it. `GROQ_API_KEY`
is required (the receptionist's own replies *and* STT);
`PIPECAT_PILOT_AGENT_ID` is optional and only speeds up STT;
`<NAME>_EDGE_TTS_VOICE` and `MICKEY_REPO_ROOT` are optional overrides.
Don't add a `source .env` step to docs — `setdefault` already means an
exported value wins.

There is no `requirements.txt` or lockfile anywhere in the workspace;
`../.venv/bin/python -m pip list` is the only manifest. Worth knowing before
you go looking for a missing dependency: **there is no `groq` package, and
there never needs to be.** `agent_memory/adapters/groq_model.py` uses the
installed `openai` SDK with `base_url="https://api.groq.com/openai/v1"`,
since Groq's API is OpenAI-compatible. Likewise `adapters/voice_io.py` uses
only stdlib (`urllib.request`, `wave`, `array`, `subprocess`) — its real
dependencies are the external binaries `parecord` and one of
`ffplay`/`mpg123`/`paplay`, not Python packages.

## Architecture

### Both receptionists are Groq; only delegated roles are Claude

`main.py` builds `GroqLanguageModel` (`--model`, default `openai/gpt-oss-120b`
— both receptionists)
for conversation and routing — one HTTP call per turn, no `claude_agent_sdk`
subprocess spawn. Claude is reserved for delegated finance work
(`--role-model`, default `claude-sonnet-5`), which needs real Bash/Read/Write
tool use. The root `CLAUDE.md` and `README.md` may still describe this split as
*Mazda's* divergence; it is both receptionists' arrangement.

**Two Groq limits bite, and they behave differently.** 8000 tokens per
minute, *shared* across both `gpt-oss` sizes (`x-ratelimit-limit-tokens` on any
response), and 200000 tokens per day, counted *separately per model* — read it
off a daily 429, which names the model and reports `(TPD): Limit 200000, Used
…`. A refused request consumes nothing, so an oversized probe is a free way to
read either counter. Two consequences worth keeping straight: the per-minute
ceiling is about the size of one turn, while the daily one is about how many
turns a day the receptionists get — roughly 55–65 each at 3000–3700 tokens a
turn — and because the daily budgets are per model, a receptionist stuck on an
exhausted model can be moved to the other one and keep working. That is not a
fix, only a second bucket; both were spent in one day on 2026-10-23.

**Neither limit ends the turn any more.** `main.py`'s `build_chat_model()`
builds `FallbackModel( RetryingModel( groq ), [ RetryingModel( openai )])`
(`agent_memory/adapters/decorators.py`,
`agent_memory/adapters/openai_model.py`), so a spent daily budget falls
through to OpenAI's `gpt-5-mini` instead of reaching the user as a 429. The
backup is built **only when a key is present** -- without one
`build_chat_model` returns the bare retry-wrapped Groq model and the machine
behaves exactly as it did before, because no fallback has to mean a quieter
day rather than a broken startup.

The key goes in `receptionist_team/.env` as **`RECEPTIONIST_OPENAI_API_KEY`**,
not `OPENAI_API_KEY`, and `openai_model.resolve_api_key()` checks that name
first. The scoped name is not decoration: `adapters/openai_embedder.py`
constructs a bare `OpenAI()` and reads only the ambient `OPENAI_API_KEY`, so a
key parked under the scoped name is invisible to it and a stray
`--embedding-profile openai` cannot bill a paid embedding run against credits
bought for fallback replies. The resolved key is handed to the client as
`OpenAI( api_key=... )` rather than left to the SDK's ambient lookup, which is
what makes that guarantee hold instead of being a naming convention.

Worth stating once because it has caused real confusion: an OpenAI key has no
bearing on Claude's auth, and neither has any bearing on a ChatGPT
subscription. `ANTHROPIC_API_KEY` is the variable that silently flips Claude
Code off subscription OAuth onto metered billing (see this repo's standing
rule and the two commented-out exports in `~/.bashrc`); OpenAI API credits and
a ChatGPT subscription are separate systems that cannot spend each other's
money, which is also why the subscription cannot drive this code. Both entrypoints get this for free:
`service.py` calls the same `_load_dotenv()`, and `fallback_model=None`
threads down to `$OPENAI_FALLBACK_MODEL` and then the adapter default.

Three details in that adapter are load-bearing. `reasoning_effort` is pinned
to the floor because the GPT-5 family bills hidden reasoning tokens at the
output rate and pays for them in latency -- a receptionist writing three
sentences has nothing to reason about, and leaving it off is the difference
between a few dollars a month and three times that. **The floor is spelled
differently per generation**: `gpt-5` wants `"minimal"` and 400s on `"none"`,
`gpt-5.4` wants `"none"` and 400s on `"minimal"`. Rather than a model-name
table that the next release invalidates,
`_retry_without_refused_effort()` reads the supported list out of the 400 and
retries once with the cheapest one, then remembers it on the instance -- a
warm sidecar must not pay a wasted 400 per turn to rediscover the same
answer. Measured on gpt-5.4-mini, whose floor is spelled differently from the
default's: it self-heals to `none` on turn one (2.1s) and
turn two costs 0.53s with 5 output tokens, i.e. no reasoning billed. The default is a *mini*, never a nano: the
one time this repo ran a model of that weight (Groq's `gpt-oss-20b`) it
answered status questions from recalled chat instead of emitting a `ROUTE:`
line, which is why `adapters/status_route.py` exists -- not worth re-fighting
to save a few dollars a year. And `_refusal()` separates "out of credit" from
"rate limited", because a receptionist reporting a rate limit when the real
answer is the billing page sends the next hour down the wrong path; that
mistake has already been made once here, with Groq's daily cap.

A 400 is classified as `ModelUnavailable` rather than left to retry, because
`RetryingModel` sits on anything else: five attempts at one un-retryable 400
turned `"does not support 'minimal' with this model"` into `"Model failed
after 5 attempts"`, which is how a one-word config mistake hid for an
afternoon. And every operator-visible `print()` here passes `flush=True` --
not tidiness: a sidecar redirects stdout to a file, so an unflushed warning
sits in an 8KB buffer for hours, which is precisely the silent paid usage the
warning exists to prevent. That one was found reading an empty log.

`FallbackModel` falls through on `ModelUnavailable` and on `RuntimeError`
(which covers `RetryingModel` giving up and both provider error classes), and
deliberately **not** on `TypeError` and friends -- a bug in our own
prompt-building should surface as a bug, not quietly buy a second paid API
call on every turn. It prints each hop, every time, so a receptionist silently
costing money because the free backend has been dead for a week is visible.
When every hop fails it raises the *last* error, not the first.

Against the per-minute ceiling the model makes no difference and the size of
one turn is everything. Toyota's system prompt is ~12000 characters of routing
rules against Mazda's ~7700, which is why `ReceptionistProfile.recall_max_chars`
exists and why **Toyota's** is 6000 rather than the shared 12000 default —
Mazda's is still unset, since her smaller prompt left her turn under the
ceiling. Shrinking a system prompt is the only lever left after that, and it is
the one lever that helps against both limits at once.

`ANTHROPIC_API_KEY`/`ANTHROPIC_BASE_URL` must stay unset (local Claude Code
OAuth, not metered API) — same repo-wide rule; `finance_team/adapters/tool_agent.py`
holds the per-subprocess `env=` backstop.

### The composition root and the one cross-team import

`main.py` is the only module here allowed to name a concrete implementation.
The deliberate cross-team edge is `adapters/tool_agent_runner.py` importing
`finance_team.adapters.tool_agent.run_role` and `finance_team.roles`
(`ROLES`, `REPO_ROOT`) — Toyota's whole purpose. It lives in the *runner* and
not in `routing_model.py` on purpose: that is what lets Mazda reuse the same
`RoutingModel` without pulling the Claude SDK and the `rol_finances` repo root
in behind her. `roles.py` imports `finance_team.roles.ROLES` for names only,
nothing SDK- or DB-shaped.

### A profile is the unit of "a receptionist"

`ReceptionistProfile` (frozen dataclass, pure data — role config, not a wire
contract, same reasoning as `finance_team`'s `AgentRole`) carries the
`agents/<name>/` folder name, display name, default session, nexus subdir,
system prompt, roster, a `build_runner` factory, the peer session/label, the
TTS voice, and two flags — `handoff` and `operations` — for the dashboard-only
decorators Toyota has and Mazda doesn't.

`build_receptionist(profile, settings, role_model, ...)` is the whole
assembly; `build_toyota()` survives only as a thin wrapper because
`turn_runner.py` and four tests name it. **Adding a third receptionist is one
`PROFILES` entry plus an `agents/<name>/` folder** — not another `build_*()`
and emphatically not another package.

The two runner factories in `profiles.py` import their adapters *inside the
function*. Keep it that way: module-scope imports there would drag
`finance_team` (hence `claude_agent_sdk`) and `GroqLanguageModel` onto the
path of anything that merely reads the profiles, including Mazda's offline
tests.

### Delegation is a Strategy, so an unwired agent can say so

`delegation.py` holds `IDelegationRunner` —
`run(role, agent_name, model, extra="") -> AgentReply`. `RoutingModel` matches
the control line, speaks the acknowledgment, and hands the role to the
injected runner; it never learns whether anything real is on the other side.

| runner | used by | what it does |
|---|---|---|
| `ToolAgentRunner` (`adapters/`) | Toyota | `run_role` through the Claude Agent SDK against `rol_finances` |
| `MickeyRunner` (`adapters/`) | Mazda's `mickey` | collects the progress snapshot, then one constrained model call |
| `AdwBrowserRunner` (`adapters/`) | Mazda's `browser_opener` | subprocesses `adws/adw_interface_browser_open.py` and reports the line it wrote |
| `AdwLiveFinanceRunner` (`adapters/`) | Toyota's `live_books` | subprocesses `adws/adw_interface_live_finance.py` and reports the figure it wrote, verbatim |
| `ToolAgentRunner` | Toyota's `search_link_opener` | the agent folder below; no dedicated runner, because picking a result link is a judgement |
| `UnwiredRunner` | Mazda's other five | says plainly the connection isn't built and nothing ran |
| `RunnerMap` | Mazda | dispatches by agent name over a default |

`UnwiredRunner` is load-bearing, not a placeholder to be "finished":
**Mazda's standing rule is never to claim work was done unless a tool
confirmed it**, so a stub that fabricated a plausible result would be the one
thing she must not do. Her five stub `AgentRole`s also carry
`allowed_tools=[]` so nothing can run against real financial data by accident,
and `agents/mazda/tools/*/tool.json` are declared `"kind": "stub"` — the
`db_query` one is read-only by design.

`browser_opener` is the first thing Mazda can actually *do*, and it is a
workflow rather than an agent: `adapters/adw_runner.py` runs the ADW as a
subprocess with `--message-out`, reads that one line, and returns it verbatim.
No Claude turn, no `bypassPermissions`, no model anywhere in the path — the
same "deterministic first" shape as Mickey, and for the same reason. Toyota
reaches the same ADW through her `browser_opener` *agent folder* (a Claude SDK
turn with Bash that decides `--url` vs `--phrase` itself); Mazda makes that
decision in `target_of()`, a plain tested function, because "open this" doesn't
need an agent. Its two halves live where the mistakes are: `target_of` treats
anything it can't recognise as a search phrase (wrong toward "url" sends a
sentence to a URL validator; wrong toward "search" is harmless), and a
workflow that writes no line is reported as *unknown*, never as opened.
`mazda_roles.BROWSER_AGENT_NAME` is the one spelling of the routing name.

### `search_link_opener` is the only agent that drives a page

Toyota's `search_link_opener` folder is the step past `browser_opener`, and the
two split on one question: does the user already have the address? If yes it is
`browser_opener`, which hands a link to the launcher ladder and stops. If the
site has to be *found* first -- "pull up Fifth Third Bank so I can log in" --
it is `search_link_opener`, which runs
`adws/adw_interface_search_open.py`: search headlessly, read the organic result
links off the results page, pick the one that IS the site, trim it to the front
door, and only then spawn the real window. `"Fifth Third Bank"` lands on
`https://www.53.com/`, logged out, in a window the user controls.

The headless half is **selenium-headless's `WebBrowserManager`**
(`/home/adamsl/selenium-headless/browser_manager/`), imported by path inside
that ADW's `SeleniumResultPageReader` and nowhere else -- that repository is a
separate history with no packaging, and its other pieces (`link_analyzer`,
`data_extractor`, `duplicate_manager`) belong to its sunbiz.org crawler and are
not used here. `selenium` is therefore now a dependency of `../.venv`, the one
interpreter that has both it and the ADW's `pyyaml`/`rich`/`dotenv`. The
everything-after is `adw_modules/map_open.py` untouched -- same `validate()`,
`select_launcher()`, `launch()` as the other ADW.

Three things about it are load-bearing:

- **Bing leads the backend ladder, and that order is measured, not preferred.**
  Google answers headless Chrome with `/sorry/index` and DuckDuckGo's html
  endpoint with "Unfortunately, bots use DuckDuckGo too"; Bing answers with ten
  organic rows. Both blocked engines stay on the ladder because a challenge
  page is zero candidates and one page load, not an error. A Google
  `search?q=...` URL handed in as `--search-url` is read for its `q=` and run
  against the ladder instead of being fetched, and the operator's line says so.
- **Bing's result anchors are not destinations.** They point at
  `bing.com/ck/a?...&u=a1<base64url>`; the href at face value opens bing.com. A
  redirect that will not decode is dropped rather than opened.
- **No pick means the search page opens, and the line says so.** Nothing
  invents a destination -- same rule as `UnwiredRunner`. The agent's own prompt
  repeats it, because "it opened the bank" when it opened a results page is the
  one outright lie this workflow could tell.

`tests/test_search_open_adw.py` pins all three with a faked reader, plus one
`--dry-run` spawn of the real workflow that *does* reach the network, since a
backend that quietly starts challenging us is exactly what no fake can catch
(it skips rather than fails when offline).

Her prompt also has a vocabulary rule, pinned by a test: she must not use the
words "idempotent", "fail-closed" or "canonical" — say "safe to repeat",
"stops safely rather than guessing", "standard vendor key".

### Mickey is deterministic first, explained second

`progress/tracker.py` reads
`<repo_root>/finance_reports/<year>/<month>/<account>.json` with no model
involved and persists one snapshot; `narrate.py` turns that snapshot into a
prompt; only then does a model speak. That order is the design — the
explanation cannot add a fact the tracker didn't collect, and
`tests/test_mickey_routing.py` pins it. Repo root defaults to
`receptionist_team/workspace/` and `MICKEY_REPO_ROOT`,
`MICKEY_EXPECTED_ACCOUNTS`, `MICKEY_MONTHS_BACK`, `MICKEY_STALE_DAYS` override the
defaults (they were `MAZDA_TEAM_*` before the move; nothing in the repo sets
either spelling).

`tracker.REPORT_WRITER_AGENT` is `"mickey"`: writing the finance reports is her
own job (it read `"sheeta"` until that `mazda_team` role was retired). She
carries no tools, so a next step assigned to her is work still waiting — never
work she did, and `narrate.py`'s system prompt says so explicitly, because a
tracker claiming she wrote a report is exactly the fabrication Mazda's rules
forbid. `TAX_FORM_AGENT` still reads `"tarita"`, a label naming an agent
outside this package rather than a routable target.

### Routing is a `LanguageModel` Decorator, and it is live

`RoutingModel` wraps the Groq model and satisfies the same `LanguageModel`
Protocol, so `ConversationAgent` sees one `complete()` and one `AgentReply`
and never learns a delegation happened (same shape as
`agent_memory/adapters/decorators.py`'s `RetryingModel`). Both the roster and
the runner are **injected** — `RoutingModel` has no hard-coded knowledge of
either receptionist's team.

```
ROUTE: <agent_name>
ROUTE: <agent_name> | <extra instructions>
```

matched with `re.fullmatch`, **not** `search` — ordinary conversation that
merely mentions routing must not fire a delegation. Keep it that way: for
Toyota a matched ROUTE line reaches `run_role(..., cwd=REPO_ROOT)` with
`permission_mode="bypassPermissions"` against `/home/adamsl/rol_finances`
and the real `rol_finances` MySQL DB. Extra instructions are appended to a
`dataclasses.replace`'d copy of the role, never mutated in place. An unknown
agent name returns a friendly message listing known agents rather than
raising.

### One route doesn't wait for the model to agree

`delegation.IRouteOverride` is consulted by `RoutingModel` **before** the
inner model is called at all; returning `None` means "no opinion, let the
model decide". **Both receptionists now name one**, for the same failure in
two places: a question the model answered out of recalled conversation when it
should have asked a tool.

Mazda's is `adapters/status_route.StatusQuestionRoute("mickey")`. Her prompt has
always said every status question goes to Mickey, and the 20b often ignored it —
answering from recalled conversation instead and re-asserting a year of
finished finance reports that do not exist, which recall then handed back to
her as history. The forced route skips the inner call entirely: a model that is
asked gets the chance to answer the question itself from memory.

Two details are load-bearing. It reads the **last** `USER:` in the composed
prompt (`agent_memory/prompts.py`'s `DEFAULT_TEMPLATE` labels recalled turns
the same way, so matching the whole prompt would make every later turn a status
question for as long as that turn stayed in recall). And with no `USER:`/
`AGENT:` marker at all — `RecallOnlyComposer` — it returns `""` and forces
nothing, rather than treating the whole prompt as the user's words. An override
naming an agent off the roster is ignored, so a typo falls back to the model
deciding instead of breaking every turn. `tests/test_status_route.py` pins all
of it.

Toyota's is `adapters/figure_route.FigureQuestionRoute("live_books")`, and it
is the other half of the same lesson. Her only data-shaped assignment was
synthetic -- one practice expense, #900001 -- and `operations/workflow_model`
injected "synthetic TRAINING environment only" into every single turn. So when
she was asked what the organization had actually spent, there was no path to a
real number and she produced a believable one; that reply was stored, recalled,
and re-asserted until it read like established fact. 66 turns of training talk
were in her session when this was fixed, and `forget.py --apply` deleted them.

**The memory was the symptom; the cause was that no number was reachable.** A
prompt rule cannot supply a figure. What fixed it is
`adws/adw_interface_live_finance.py` -- see below -- plus the route to it not
depending on the model agreeing to take it.

The override is deliberately generous, which is only safe because of where it
ends: the ADW has its own matcher over its installed questions, and an
unmatched one says plainly that it read no figure and names what it CAN be
asked. A false positive therefore costs one honest "I can't ask the books that
yet" rather than an invented amount. Its `_BELONGS_ELSEWHERE` veto runs
*first*, for the opposite risk -- a spreadsheet request belongs to
`category_spreadsheet`, a specific tax record to a WORK command, report
freshness to `progress_check`, and routing any of those here would answer a
narrower question than the one asked.

### The live books are read-only, enforced in code

`adws/adw_interface_live_finance.py` is five phases, no model anywhere in it,
against the live `nonprofit_finance` MySQL schema. Four things in it are
load-bearing:

- **`ReadOnlyBooks` is a Decorator over `ILiveBooks`, not a grant.** The MySQL
  account on this box holds ALL PRIVILEGES, so the database is not what stops
  a write -- this is. It refuses anything that is not a single `SELECT`/`WITH`,
  including a read with a write stapled on behind a semicolon.
  `assert_read_only` is a public classmethod so a test needs no connection.
- **The session is opened `READ ONLY` and never committed**, as a second line
  behind the Decorator. Both are deliberate; don't remove either as redundant.
- **The organization is resolved by `organizations.type = 'NONPROFIT'` each
  run, never by a pinned `org_id`** -- the same schema also holds two sets of
  *personal* books, and reporting one of those as the organization's spending
  would be a real error with real consequences. The column is `type` (a
  reserved word, backquoted), **not** `org_type`.
- **An unmatched question answers nothing.** There is no default query: the
  line names the installed questions and says it read no figure. Same rule as
  `UnwiredRunner` and as `adw_interface_search_open.py`'s unpickable
  destination.

The installed questions are a Strategy registry, `install_questions()`, most
specific first -- `total_spend` deliberately last among the amount questions,
since "how much did we spend by category" contains "how much did we spend".
Adding one is a single entry there: no phase changes, no new flag, nothing to
edit in the receptionist. `--list-questions` prints them and touches no
database.

Credentials come from `NON_PROFIT_USER`/`NON_PROFIT_PASSWORD`/
`NON_PROFIT_DB_NAME`/`DB_HOST`/`DB_PORT` in the environment, or from
`--env-file` (default `/home/adamsl/.config/mazda/finance-db.env`), never from
a literal, and the environment wins. Note that `finance_team/roles.py` briefs
`/home/adamsl/planner/.env` for its own DB work and **that file does not
exist**; the config path above is what works.

`AdwLiveFinanceRunner` reaches it the same way `AdwBrowserRunner` reaches the
browser ADW: subprocess, `--message-out`, and the written line returned word
for word. The reply reads a little more like a ledger than like Toyota, and
that is the right trade -- a figure she has rephrased is a figure nobody can
check. The `--message-out` file is the contract and outranks the exit code: an
unmatched question exits 0 with a useful line, and a matched question that
found no rows exits non-zero with a line worth repeating.

`agent_memory/adapters/lancedb_store.py` gained `forget(ids)` for the same
episode's aftermath: her own fabrications were stored turns, so recall kept
handing them back as fact. `receptionist_team/forget.py` is the CLI (dry-run by
default, `--match` regex, `--keep` to spare ids, `--apply`), and the warm
sidecar must be restarted afterwards — it holds the old table handle.

### `roles.py` fails loud at import on an unregistered role

`ROLE_BLURBS`, `ROLE_SPOKEN_NAMES`, and `ROLE_START_TASKS` are each checked
against `finance_team.roles.ROLES` at module import; a missing key raises
`RuntimeError` immediately. So **adding a role to `finance_team/roles.py`
breaks Toyota's import until all three dicts here gain an entry** — that's
the intended failure, since a role the receptionist can't name or describe
is a role it shouldn't be routing to. `TOYOTA_SYSTEM_PROMPT` builds its
roster and its allowed-agent-names list from `ROLES` too, so no third list
needs editing.

### The delegation acknowledgment

`TOYOTA_SYSTEM_PROMPT` tells the model to emit a bare ROUTE line because
"the application supplies the spoken acknowledgment separately so that it can
begin playback immediately while the delegated agent starts working." That is
now true on both front ends.

`RoutingModel.__init__` takes an optional `on_delegation: Callable[[str], None]`
and calls it with `roles.delegation_acknowledgment(role_name)` *before*
awaiting `run_role`. The sidecar passes one (see below), so a dashboard user
hears "let me check with accounting" within a second and the reply lands
minutes later on the same stream. `main.py`'s `repl()` wires
`AcknowledgmentSpeaker.say` into the same hook when `--voice` is set; typed
mode has no voice_io and correctly leaves `on_delegation=None`.
`tests/test_repl_voice_delegation.py` pins both branches and
`tests/test_delegation_acknowledgment.py` pins the ordering — the
acknowledgment reaches the sink *before* the slow call.

The callback is synchronous and fire-and-forget on purpose: it runs inside
`complete()`, so anything slow or raising there would delay or break the very
turn it is announcing. That is why `AcknowledgmentSpeaker`
(`adapters/voice_io.py`) plays on a daemon thread, swallows playback failures,
and returns immediately; `repl()` calls its `wait()` before speaking the real
reply so the two players never overlap.

### The sidecar

`service.py` is a `ThreadingHTTPServer` holding **one warm
`ConversationAgent`**, because loading the sentence-transformers model and the
LanceDB table costs ~30 seconds — per process, not per turn. Three endpoints:
`GET /health` (the dashboard's tile probe), `POST /turn`, which streams
NDJSON `service_models.TurnRecord`s: zero or more `acknowledgment`s, then a
`reply`, then a terminal `result` — or a single `error`; and `POST /compact`,
a single plain-JSON `{"ok": true, "session": ...}` reply for the dashboard's
"compact conversation" button. `/compact` rotates the warm agent onto a new
session id (`ConversationAgent.clear_context()`,
`agent_memory/agent.py`) rather than deleting anything — recall is
session-scoped, so the next turn's recall simply finds nothing from before.
It goes through `_TurnRunner`'s same lock as a turn, so it can't land
mid-turn, and `self._settings.session_id` is updated with it so `/health`
and the next `TurnRecord.session` agree.

Four things here are load-bearing:

- **It binds `127.0.0.1` and must keep doing so.** A delegated turn runs
  Claude with `bypassPermissions` and real Bash against the live
  `rol_finances` MySQL DB. The dashboard that calls it is `0.0.0.0`-bound and
  Tailscale-served to a phone; loopback-only binding *is* the authorization
  model. Do not make it configurable.
- **Turns are serialized** by an `asyncio.Lock`. `handle_turn` recalls before
  it persists, which only yields a sane transcript if turns don't interleave —
  and HTTP will happily deliver two at once.
- **The turn runs on its own loop thread; records cross back on a
  `queue.Queue`** drained by the HTTP thread. That is what lets the
  acknowledgment reach the browser while `handle_turn` is still blocked
  inside `run_role`.
- **A dropped client does not abort the turn.** `BrokenPipeError` closes the
  generator and lets the delegation finish — a half-run finance role that
  already wrote files is worse than a wasted reply.

`_TurnRunner` takes its agent in `__init__` and builds the real one in
`build()`, so `tests/test_service_turns.py` drives the loop with a fake and
never imports torch. Port 8767 is deliberate: 8765 is the dashboard, 8766 is
`planner/a2a_communicating_agents/thought_bridge.py`.

The consumer lives in the other repo —
`letta-code/dashboard/toyota_service.py` translates these records into the
same envelope `/api/letta-code-stream` emits and serves them at
`/api/receptionist-turn`. **The wire format is a contract across two
repositories**: changing `TurnRecord.kind` means changing `_translate` there
too, and the dashboard's `tests/test_toyota_service.py` is where the
translation is pinned.

### Voice is borrowed, not implemented

`adapters/voice_io.py` only speaks HTTP to the dashboard on `localhost:8765`
(`POST /api/voice` for STT, `POST /api/tts` for edge-tts) and imports nothing
from `dashboard/`, so a dashboard refactor can't break this package's imports
— but `dashboard-server.service` must be running or `--voice` falls back to
typed input for that turn. Specifics that cost real debugging time and are
documented in the file's own comments:

- Recording is **silence-terminated**, via `parecord --raw` to a temp file
  polled frame by frame (WSLg exposes the mic as a PulseAudio `RDPSource`,
  not an ALSA card; `parecord ... -` to a pipe buffers in bursts and is
  useless for level reading). The mic idles around 1700–2400 RMS here, hence
  the warm-up discard, the adaptive floor, and `SPEECH_MARGIN = 1200`.
- A dead `parecord` raises `VoiceUnavailableError` rather than looking like
  "nobody spoke" for 8 seconds every turn.
- TTS pipelines sentence N+1's synthesis behind sentence N's playback;
  `tts_wait_stall > 0` in the metrics names the sentence that fell behind.
- Everything in `speak()` fails closed — the reply is always printed first,
  so a missing player or a bad sentence skips audio, never the conversation.
- `ffplay` gets `stdin=DEVNULL` plus a timeout; inheriting the terminal's
  stdin can wedge it on SIGTTIN despite `-autoexit`.

### Every agent is a folder, Toyota included

`agents/<name>/` holds `system_prompt_template/`, `user_prompt_template/`,
`model_confiurations/` (spelled exactly that way on disk), `tools/` and
`documentation/`. Three modules consume it:

- `roles.py` renders `agents/toyota/system_prompt_template/system_prompt.md`,
  filling its `{{roster}}` and `{{allowed_agents}}` placeholders — the prompt
  text is no longer in `roles.py`, only the roster wording is;
- `main.py` builds `TemplatePromptComposer` from
  `agents/toyota/user_prompt_template/user_prompt.md` (byte-identical to
  `agent_memory.prompts.DEFAULT_TEMPLATE`, pinned by a test, so this is the
  prompt Toyota always had — now editable without touching Python);
- `extra_roles.py` turns every name in `EXTRA_ROLE_NAMES` into a
  `finance_team` `AgentRole` via `agents.AgentRoleFactory`, which is where
  `spreadsheet_reshaper`'s prompts went.

`agents/` imports `IApplication`, `IArgumentParser`, `ILifecycleLog` and
`LifecycleLogged` from `../../sssf_interfaces` — the SSSF factory's own
`adws/adw_interface_prompt.py`, loaded by file path — rather than restating
them. `agents/README.md` has the module-by-module responsibility table, the
placeholder rules, and what is deliberately left as a stub
(`documentation/`, the `tools/*` declarations, `toyota_mechanic/`).

### Memory

Standard `agent_memory` wiring, with two choices made in `main.py`:
`--embedding-profile` defaults to `sentence` (384-dim, local semantic model,
table `conversation_memory_sentence384`) because real human chat needs
meaning-based recall, and `Settings` is `model_copy`'d per profile so the
write-only archive lands in `nexus/<profile.nexus_subdir>/`.

Both receptionists share **one** LanceDB table, and each profile names the
other as its `peer_session`, so `PeerSessionRecall` lets each hear what the
user told the other (`tests/test_shared_memory_wiring.py`). The recall chain is
`SimilarityRecall → RecencyFloorRecall → PeerSessionRecall → TokenBudgetedRecall`.
Recall still reads LanceDB only — don't reintroduce a filesystem read into
that path.

## Style and housekeeping

- **This package does not follow the workspace bracket-spacing convention**
  (`str )`, `[ 0.0 ]`) — **except everything under `agents/`, which does.**
  That subtree is written to the SSSF template's rules on purpose; `main.py`,
  `profiles.py`, `rosters.py`, `delegation.py`, `roles.py`, `mazda_roles.py`,
  `extra_roles.py`, `service.py`, `adapters/`, `operations/`, `progress/` and
  `tests/` keep their unspaced style. Match the file you're editing; never normalize across
  the boundary.
- The convention **cannot** reach inside an f-string format specifier:
  `f"{ name:<24 }"` raises `ValueError: Unknown format code`. Write
  `f"{ name:<24}"`. `adw_interface_prompt.py`'s own `PrintLifecycleLog` and
  `ConsoleLifecycleLog` have exactly this bug (`#{ id( obj ):x }`) and raise
  on first call, which is why `agents/lifecycle.py` implements `ILifecycleLog`
  locally instead of importing them.
- `voice_metrics.log` is **tracked in git** and appended to by every voice
  run, so `--voice` always dirties the worktree. Don't commit it as part of
  an unrelated change.
- `adapters/voice_io.py` imports `log_stage` but only uses `Stopwatch` —
  harmless, but don't take the import as evidence of a second logging path.
- `workspace/progress/mickey_state.json` is gitignored; `workspace/` itself is
  tracked for its `README.md` only.
