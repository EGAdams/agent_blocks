# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## spa_documentation

This nested Git repository currently runs as a plain HTML/CSS/JavaScript
documentation SPA for the parent `agent_blocks` workspace. It has no framework,
runtime build step, package manifest, or executable test suite.

Before doing any work here, load the `building-the-spa` skill from
`skills/building-the-spa/SKILL.md`. It defines the navigation drill-down,
directory layout, generated-page conventions, Construction Status task trees,
the Mermaid pan/zoom pattern, and the verification workflow this SPA depends
on. Load it every time, not only when the task appears visual.

## TypeScript refactor checkpoint

`app/` contains the strict TypeScript design for replacing the 1,301-line
`app.js`. The tree currently contains interface contracts, immutable API/DOM
shapes, and `create*` factory declarations. It passes strict type checking, but
it is a **design skeleton, not an implementation**:

- `index.html` still loads the legacy `app.js`;
- nothing under `app/` is executed by the browser;
- `app/tsconfig.json` is `noEmit` and there is no local TypeScript package yet;
- `window.__spa` is a typed planned test seam, not a live global yet;
- all internal TypeScript imports deliberately use emitted `.js` specifiers.

`app_refactor_plan.md` is the authoritative architecture and nine-step work
order (Step 0 through Step 8). Follow it one committed step at a time.
`app_refactor_plan.html` is its synchronized visual progress companion.

`app_tests/` is the structural test-design mirror: every one of the 62
`app/**/*.ts` modules has one `<name>.test.ts` counterpart under the same
relative directory. Each file names its sole test responsibility and planned
evidence but implements no assertion. The mirror is a review guide, not test
coverage. Keep cross-module lifecycle evidence at the owning coordinator,
composition root, or future browser-integration boundary.

The TypeScript design keeps concrete browser adapters at
`app/composition-root.ts`, raw `fetch()` isolated to the future
`app/core/fetch-http-client.ts` implementation, JSON boundary validation in
`app/core/api-decoders.ts`, passive views, single-owner Observer state, and one
mandatory enhancer chain wrapped around the content host. Read the architecture
laws and rejection checklist in the Markdown plan before implementing a stub.

Type-check the current design with the installed compiler:

```bash
/home/adamsl/letta-code/node_modules/.bin/tsc -p app/tsconfig.json
/home/adamsl/letta-code/node_modules/.bin/tsc -p app_tests/tsconfig.json
```

Before browser cutover, the project still needs a repository-local TypeScript
toolchain, generated ESM under `dist/app/`, and a build hook in `start.sh`.
`server.py` already sends `Cache-Control: no-cache`; verify that the dashboard's
`/agent-block/` reverse proxy preserves revalidation for imported submodules.
Versioning only the entry module is insufficient if the proxy caches imports.

## Commit before you stop, every time

This repo has no CI and no reviewer forcing commits, so uncommitted work has
silently piled up across sessions before -- once to 13 files / 346 lines
spanning two unrelated features tangled together in the same file, discovered
only when a change appeared to have been "reverted" that had actually just
never been committed. `git log` / `HEAD` is only the source of truth for
"what's actually here" if commits happen; an uncommitted working tree is
scratch space nobody is reading, and the next session (or the next hour of
this one) has no way to tell it apart from finished work.

- Run `git status` and `git diff --stat` **before** editing any file here --
  don't assume the working tree matches the last commit or matches what a
  prior message in this conversation described.
- Commit finished, coherent chunks of work as you go rather than batching
  everything to the end of a session. A commit is a checkpoint; small ones
  are cheap.
- If you find a file's uncommitted diff mixes two unrelated changes, split
  it before committing (reconstruct the intermediate state and commit each
  piece with its own message) rather than committing them tangled together
  or discarding either one.

## Commands

```bash
./start.sh                 # serve on http://localhost:8931/index.html
./start.sh 9000            # alternate port

# Design-only TypeScript check; emits nothing.
/home/adamsl/letta-code/node_modules/.bin/tsc -p app/tsconfig.json

# Test-design scaffold check; runs no assertions and emits nothing.
/home/adamsl/letta-code/node_modules/.bin/tsc -p app_tests/tsconfig.json

# Run browser characterization tests headlessly, or watch them in Chrome.
# Both scripts compile browser_tests/ to dist/browser_tests/ first (pretest).
npm test
npm run test:headed

# Regenerate one leaf's source-derived docs (real Claude Agent SDK call).
# Needs .venv; ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL must stay UNSET so the
# SDK uses local Claude Code subscription auth instead of direct-API billing.
.venv/bin/python update_documentation_agent.py lancedb_memory/models/turn

# Regenerate the Home page after editing the parent repo's root CLAUDE.md
# (exact snippet in skills/building-the-spa/SKILL.md).
python3 -c "import markdown; ..."   # -> home_claude_md.html
```

`browser_tests/` is TypeScript, one directory per module, and must be compiled
before it runs -- WSL Node has type stripping disabled and the Windows Node the
headed run uses is 22.11, which predates it. `dist/` is gitignored, so the
compiled tests are a build product like `dist/app/`. `browser_tests/`'s own
`tsconfig.json` emits; it does not extend `app/tsconfig.json`, because the tests
need Node types and `NodeNext` resolution the browser design does not.

The suite is wired through `browser_tests/composition-root/composition-root.ts`,
the only module allowed to name a concrete implementation. Host differences live
behind the `PlatformProfile` abstract factory (Windows/WSL vs Linux), and every
headed-only behaviour -- the step dialog, the window focus call, the hold-open
banner -- is a Null Object in headless runs rather than an `if` inside a test.
Tests speak only to the `SpaPage` page object; no test names a raw selector.

The headed test command uses Windows Node and native Windows Chrome so the
browser appears on the user's desktop instead of inside WSLg. It keeps a final
result page visible for ten seconds before closing automatically.

`start.sh` runs `server.py` (stdlib-only) rather than a bare static server,
because the Update Documentation tab depends on `server.py`'s
`/api/git-status`, `/api/run-update`, and `/api/run-update-status` routes.
`.venv` (`claude_agent_sdk`, `pygments`) is needed only by the updater
subprocess `server.py` launches, not by `server.py` itself.

There is nothing to lint or unit-test. Verification means running the SPA and
executing its JS — `loadFile()`'s script execution and Mermaid rendering are
invisible to `curl`/`Read`. Use headless Chrome through `index.html`'s real
nav flow, per the skill's "Verifying a diagram page actually works".

## Folders here are documentation mirrors, not source

`spa_browser_tests/` is the one exception to the rule below: it documents
source that lives **inside this repo**, at `browser_tests/`, rather than one
level up in `agent_blocks`. It is named `spa_browser_tests/` precisely because
`browser_tests/` is already taken on disk by the source it describes, and
`doc_source_map.py` resolves its branch against `DOCS_ROOT` instead of
`REPO_ROOT`. Any future block documenting this repo's own code needs the same
two adjustments.

Top-level folders that share a name with a real `agent_blocks` project
(`lancedb_memory/`, `catherine_agent_sdk/`, `voice_communication/`,
`claude_agent_adapter/`, `solid_agent_systems/`, `mazda/`) are *documentation
trees* inside this SPA, not the real code — each leaf holds only the six fixed
generated doc files described in the skill (overview, source, class/sequence
diagrams, construction status, update report). The actual source they document
lives one level up, in the parent `agent_blocks` checkout.

## One item is defined in three places at once — four for a top-level section

There is no single registry. Adding, renaming, or moving a leaf means editing
all three, or it half-works in a way nothing warns about:

1. `app.js`'s `sections` object — the *only* thing that drives navigation.
   Nested `items` maps mirror nested folders.
2. The folder on disk under `spa_documentation/`, holding the six fixed
   basenames (all literally `basic_agent*`; `app.js` hardcodes them as
   `overviewFile` / `detailTabs` rather than deriving them from the item key).
   Empty files are expected — `loadFile()` renders a "still blank" placeholder.
3. `doc_source_map.py`'s `resolve_source_path()` — the authoritative item-path
   -> real-source-file mapping (`REPO_ROOT` resolves to `agent_blocks`, the
   parent of this repo), used by both `server.py` and
   `update_documentation_agent.py`.

4. **Only when adding, renaming, reordering or removing a *top-level*
   section**: `browser_tests/nav-fixtures/nav-fixtures.ts`'s
   `HOME_NAV_LABELS`. Unlike (1)-(3) this one is not silent — it is the one
   place that fails loudly. Two of the six characterization tests assert the
   home nav exactly and in order, so `npm test` goes red until the fixture
   matches. That failure is the feature: updating the fixture in the same
   commit is what records the nav change as deliberate. Adding a *leaf* under
   an existing section does not touch it.

A leaf missing from (3) still renders and navigates fine; only git status and
Update Documentation silently stop working for it. `mazda/` is currently in
that state — it is in `sections` with no mapping branch. `resolve_source_path`
is not mechanical (per-section shapes, plus an explicit
`VOICE_COMMUNICATION_SOURCE_FILES` table) and validates every path segment
against `_SAFE_PART`, because item paths arrive from an untrusted query string.

## Staleness is source-dirty OR docs-missing

`/api/git-status` calls a leaf dirty if its mapped source is Git-dirty *or* any
of `SOURCE_DERIVED_DOCS` (Source, Class Diagram, Sequence Diagram) is missing
or zero-byte. A clean git diff alone does not mean docs were ever generated, so
a never-touched source with never-generated docs must still count as needing an
update. Overview / Construction Status / report are excluded — they are
hand-authored status text, not derived from source. `git-status` asks git for
each file's own toplevel rather than assuming `REPO_ROOT`, since
`catherine_agent_sdk/` is a separate repo.

`update_documentation_agent.py` has no template file to import; its prompt
points the agent at `catherine_agent_sdk/basic_agent/` pages as the structural
reference. The updater began as a Python-source workflow — the fixed
`.py.html` filename does not make a source Python, and TypeScript-backed
objects need a TypeScript lexer and language-appropriate diagrams.

## Two invariants that break only in production

- **Keep in-page URLs relative.** `api/git-status?item=...` in `app.js` and
  `vendor/mermaid.min.js` in the diagram pages must never gain a leading
  slash. The SPA is reverse-proxied under `/agent-block/` in the main Letta
  dashboard, where a root-relative path resolves against the proxy's domain
  and 404s. The document always loads at the server root regardless of nav
  depth, so a plain relative path is always correct.
- **Bump the cache-busting query strings** on `styles.css?v=...` and
  `app.js?v=...` in `index.html` whenever either file changes; they are the
  only cache control this SPA has.

`?embedded=1` adds `body.embedded`, which hides the in-page nav for the
dashboard iframe (the parent mirrors the nav in its own sidebar). Normal nav
state lives in module-level variables (`currentTop`, `itemPath`,
`currentDetail`) and has no history integration.

## Lessons whose leaf file is the source of truth

`voice_communication/conversation_agent/basic_agent_construction_status.html`
owns the overall Conversation Agent plan and task navigation. Two of its lesson
bodies do not live in it. Their source of truth is the leaf `index.html` under
`basic_agent_construction_status/declare_the_plug_in_point/`:

- `interface_file/index.html` (task `stage-1a`)
- `event_contracts/index.html` (task `stage-1b`)

The plan references each with `data-lesson-src` and carries no body of its own
for them; do not copy a lesson body back into the plan file. `app.js`'s
`renderCanonicalConstructionLesson()` fetches the leaf, clones its
`article.construction-lesson`, and rebases every `href`, `src` and
`data-api-path` against the leaf's URL, so one file serves both the standalone
page and the in-SPA view.

This is a live failure mode, not a hypothetical: the plan carried a stale
16.5 KB inline duplicate of the Event Contracts lesson, so improvements to the
leaf page were invisible to anyone using the SPA nav — the two rendered
differently and nothing said so. `check_construction_status_consistency.py`'s
`REQUIRED_LESSON_SRC` now pins both task ids; add a task there in the same
change that promotes its lesson.

## Style

Python here follows the parent workspace's bracket-spacing convention (see the
root `CLAUDE.md`): a space inside every non-empty bracket pair, adjacent
brackets stacked tight. `app.js` follows the same convention in JavaScript —
`document.getElementById( "content" )`, `[ currentTop, ...itemPath ]`. Match
it when adding code.
