# TypeScript application refactor — work order

This is the authoritative work order for replacing the 1,301-line legacy
`app.js` with a strict TypeScript application. The design contracts already
exist under `app/`; they intentionally contain interfaces, immutable data
shapes, and factory signatures rather than implementations.

The live SPA still runs `app.js`. Nothing under `app/` is loaded by
`index.html` yet. Do not confuse a compiling design skeleton with a completed
runtime migration.

## Progress checkpoint — September 3, 2026

The review skeleton is complete and ready for team inspection:

- `app/` contains 62 strict TypeScript design modules plus its no-emit
  `tsconfig.json`;
- `app_tests/` now mirrors every TypeScript source module as
  `<name>.test.ts`, preserving the same directory boundaries;
- each test stub names one test responsibility and its planned evidence;
- `app_tests/tsconfig.json` extends the application design configuration so
  the scaffold is checked under the same strict compiler rules.
- Step 0 now has a repository-local pinned compiler, package lock, emit config,
  generated-output policy, and `start.sh` build hook;
- six executable headless-Chrome characterization tests cover home navigation,
  all detail tabs, deep links, invalid-route fallback, embedded mode, task
  drill-down and Back behavior, and both canonical lessons.

This checkpoint still contains **no application implementations, executable
TypeScript unit assertions, or browser cutover**. A mirrored `.test.ts` stub is
a review guide, not evidence that its corresponding behavior works. Step 0 is
in progress; its remaining characterization work includes the Update
Documentation lifecycle and the first compiled TypeScript unit-test slice.

## Outcome

The finished application will have:

- TypeScript source under `app/` and generated browser ESM under `dist/app/`;
- one composition root that selects every concrete browser adapter;
- no raw `fetch()` outside `app/core/fetch-http-client.ts`;
- no navigation state outside the navigation and task-navigation stores;
- one mandatory content-enhancement chain for every HTML insertion;
- passive DOM views that receive view models instead of reading application
  globals;
- strict boundary decoding for every JSON response from `server.py`;
- focused unit tests for pure policies, parsers, state machines, and adapters;
- the existing headless-Chrome navigation verification through a deliberate
  `window.__spa` test seam.

One behavior change is intentional: every inserted fragment will receive the
full enhancer chain. Inline Construction Status lessons will therefore get
project-terminal button wiring, which the legacy application currently omits.
All other migration work is behavior-preserving.

## Non-goals

- Do not introduce React, Vue, a bundler, a component framework, or a state
  library.
- Do not redesign the visual presentation or navigation hierarchy.
- Do not change the six fixed documentation leaf filenames.
- Do not redesign `server.py` endpoints during the client migration.
- Do not merge the TypeScript navigation catalog with the Python source map in
  this refactor. Registry unification is separate work.
- Do not add patterns where no variation, lifecycle, or integration boundary
  exists.

## Before each implementation step

```bash
cd /home/adamsl/agent_blocks/spa_documentation
git status --short
git diff --stat
python3 check_construction_status_consistency.py
/home/adamsl/letta-code/node_modules/.bin/tsc -p app/tsconfig.json
/home/adamsl/letta-code/node_modules/.bin/tsc -p app_tests/tsconfig.json
```

The SPA worktree must be understood before editing. The parent
`/home/adamsl/agent_blocks` repository may contain unrelated user changes; do
not include them in SPA commits.

Use one coherent commit per numbered step. Never begin the next step before
the current step is committed and green.

## Architecture laws

These rules are stricter than naming conventions. Tests and review should
enforce them.

1. **The composition root is the only concrete assembly point.**
   `app/composition-root.ts` creates adapters, injects ports, orders enhancers,
   and connects subscriptions. Feature modules must not instantiate unrelated
   concrete dependencies.
2. **Internal modules import the owning contract, not the public barrel.**
   `app/interfaces/index.ts` is a type-only facade for external consumers and
   tests. Importing it from inside `app/` would invite dependency cycles.
3. **Raw browser I/O is isolated.** Only
   `app/core/fetch-http-client.ts` may call `fetch()`. API URLs remain relative
   because a leading slash breaks reverse-proxy hosting under `/agent-block/`.
4. **Untrusted JSON enters as `unknown`.** `HttpResponse.json()` returns
   `Promise<unknown>`. The decoders in `app/core/api-decoders.ts` must validate
   the exact `server.py` response shape before the Facade returns it.
5. **Views are passive.** DOM views render supplied models and emit callbacks.
   They do not resolve catalog nodes, fetch documents, inspect global state, or
   choose workflows.
6. **State has one owner.** Main navigation belongs to `NavigationStore`;
   Construction Status drill-down belongs to `TaskNavigationStore`; update-run
   lifecycle belongs to `UpdateRunMachine`.
7. **Content insertion is transactional.** The base DOM content host only
   replaces markup. `EnhancedContentHost` decorates it and always runs the
   entire enhancer chain after a successful insertion. Callers cannot request
   a partial chain.
8. **Pure data stays pure.** Catalog data and Mermaid theme literals do not
   import DOM, networking, state, controllers, or views.
9. **Browser globals are adapters, not ambient dependencies.** `window`,
   `document`, `navigator`, and `fetch` are passed into factories at the outer
   boundary.
10. **No `any`.** Narrow `unknown` at boundaries and use exhaustive checks for
    discriminated unions.
11. **A module gets one reason to change.** A class or interface may have
    several cohesive methods; splitting by method count alone is not SRP.
    Split when the reason, actor, integration, or lifecycle differs.
12. **A test file has the same single reason to change as its source.**
    `app_tests/<path>/<name>.test.ts` covers only
    `app/<path>/<name>.ts`. Cross-module lifecycle tests belong to the nearest
    coordinator, composition-root, or browser-integration boundary; they must
    not turn a focused unit-test file into a subsystem test bucket.

## GoF patterns and their limits

| Pattern | Location | Why it exists |
|---|---|---|
| Adapter | `fetch-http-client.ts`, `mermaid/loader.ts`, `task-model.ts` | Isolates browser APIs, the vendored Mermaid global, and authored DOM markup. |
| Facade | `docs-api.ts` | Presents a small application-facing surface over transport. Consumers still receive segregated sub-ports. |
| Strategy | `tabs/tab-strategy.ts`, `tabs/`, `diagram-interaction.ts` | Removes tab-key branching and makes post-render diagram behavior replaceable. |
| Observer | `nav-state.ts`, `task-nav.ts`, `update-run.ts` | Gives each mutable lifecycle one publisher and removes hand-placed rerender calls. |
| Chain of Responsibility | `content/enhancers/pipeline.ts` | Applies every idempotent post-insertion handler in a fixed order. |
| Decorator | `enhanced-content-host.ts` | Guarantees the chain without adding enhancement policy to the DOM host. |
| Builder | `construction/lesson-builder.ts` | Preserves the authored order of a multi-section lesson while making each assembly phase readable. |
| Composite | `catalog/doc-tree.ts`, `construction/task-tree.ts` | Traverses nested documentation and task trees through one cohesive boundary. |
| Mediator | `update/update-controller.ts` | Coordinates status, run state, and a passive view without coupling them together. |

`composition-root.ts` is an architectural composition root, not an Abstract
Factory merely because it calls factory functions. `update-run.ts` is a small
finite-state machine with an exhaustive discriminated union, not a GoF State
object hierarchy. `async-action-button.ts` uses injected Strategy data, not an
inheritance-based Template Method. Keep those distinctions honest.

Do not add Singleton, global Event Bus, Service Locator, Repository wrappers
around static constants, or factories that merely return primitive values.

## TypeScript source layout

```text
app/
  main.ts                         entry-point seam only
  config.ts                       runtime configuration values
  composition-root.ts             concrete assembly and lifecycle
  tsconfig.json                   strict, no-emit design check

  interfaces/
    index.ts                      public type-only facade

  core/
    api-contracts.ts              exact server.py wire DTOs
    api-decoders.ts               runtime validation of unknown JSON
    http-client.ts                transport port
    fetch-http-client.ts          sole raw fetch adapter
    docs-api.ts                   Facade plus segregated API ports
    clipboard.ts                  clipboard/fallback adapter only

  catalog/
    sections.ts                   pure documentation tree data
    doc-tree.ts                   Composite traversal only
    document-path.ts              item-key and fragment-path policy
    tab.ts                        pure tab identity and metadata shape
    tabs.ts                       pure tab descriptors and filenames

  nav/
    nav-state.ts                  navigation Observer subject
    nav-model.ts                  passive-view data
    nav-presenter.ts              state/catalog -> view-model mapping
    nav-view.ts                   DOM rendering only

  routing/
    hash-route.ts                 pure hash parser
    hash-router.ts                browser hash-event adapter

  content/
    fragment-source.ts            HTML fragment source port/adapter
    content-host.ts               base DOM sink
    enhanced-content-host.ts      enhancer Decorator
    content-controller.ts         chooses the content use case
    enhancers/
      content-enhancer.ts         idempotent handler contract
      pipeline.ts                 ordered Chain of Responsibility
      execute-scripts.ts          injected-script adapter
      render-lesson-diagrams.ts   Mermaid enhancer adapter
      async-action-button.ts      shared action-button binder
      run-tests-buttons.ts        run-tests Strategy data
      project-terminal-buttons.ts terminal Strategy data

  tabs/
    tab-strategy.ts               activation Strategy contract
    tab-registry.ts               Strategy lookup only
    static-fragment-tab.ts        ordinary tab Strategy
    construction-status-tab.ts    Construction Status Strategy
    update-docs-tab.ts            Update Documentation Strategy

  mermaid/
    runtime.ts                    vendored-global adapter port
    theme.ts                      pure theme data
    loader.ts                     lazy runtime loading only
    render.ts                     render orchestration
    diagram-interaction.ts        post-render Strategy contract
    pan-zoom.ts                   viewport interaction
    step-tooltips.ts              sequence tooltip interaction

  construction/
    task-model.ts                 one-task DOM Adapter
    task-tree.ts                  Composite traversal
    task-status.ts                status wording and aggregation
    task-trail.ts                 stage/step trail formatting
    task-nav.ts                   task-navigation Observer subject
    task-nav-presenter.ts         task state -> nav contribution
    lesson-builder.ts             generated lesson Builder
    canonical-lesson-source.ts    canonical page loading/extraction
    url-rebaser.ts                imported URL rewriting only
    canonical-lesson.ts           canonical rendering coordinator
    summary.ts                    landing summary renderer
    construction-controller.ts    construction use-case coordinator

  update/
    status-cache.ts               in-memory status repository
    git-status.ts                 one status-refresh use case
    update-run.ts                 update finite-state machine
    update-view.ts                passive update DOM view
    update-controller.ts          status/run/view Mediator

  testing/
    spa-test-seam.ts              typed window.__spa contract
```

## TypeScript test-design layout

`app_tests/` is a structural mirror, not a second implementation tree:

```text
app/<path>/<name>.ts       -> app_tests/<path>/<name>.test.ts
app/tsconfig.json          -> app_tests/tsconfig.json
```

All 62 source modules have exactly one corresponding test-design stub. The
stub documents the source module's sole responsibility and the evidence that
future tests must provide. It currently exports an empty module so it can be
strictly type-checked without pretending that assertions exist.

The mirror deliberately has no shared kitchen-sink test helper, global mock
container, or all-purpose fixture. Add a helper later only when at least two
implemented tests need the same cohesive behavior. Browser-wide behavior
belongs behind `app/testing/spa-test-seam.ts` and in a future integration-test
entry point, not in arbitrary unit files.

The old `core/dom.js` design is deliberately gone. Script execution is a
content enhancer; clipboard behavior is a core browser adapter. They have
different actors and different failure modes.

The old broad `construction/task-model.js` design is also gone. Reading one
task, traversing the task tree, aggregating statuses, and formatting trails
are separate responsibilities.

## Dependency direction

```text
main.ts
  -> composition-root.ts
      -> concrete browser adapters and feature factories
          -> feature controllers/presenters
              -> narrow ports + immutable models
                  -> pure catalog/contracts
```

Additional constraints:

- `core/` never imports views or controllers.
- `catalog/` never imports browser adapters or controllers.
- views never import `DocsApi`, `HttpClient`, or catalog traversal.
- controllers depend on interfaces, not `create*` factories.
- only the composition root imports unrelated concrete factories.
- `interfaces/index.ts` re-exports types but owns no behavior.

## Compiler and generated output

`app/tsconfig.json` is intentionally `noEmit: true` while the design contains
declarations only. It enforces:

- `strict`;
- `exactOptionalPropertyTypes`;
- `noUncheckedIndexedAccess`;
- `useUnknownInCatchVariables`;
- `verbatimModuleSyntax`;
- browser ESM-compatible `.js` import specifiers.

`app_tests/tsconfig.json` extends those rules and currently checks only the
test-design stubs. It is not a runner configuration and does not prove that a
single assertion has executed.

TypeScript cannot run directly in the browser. Implementation step 0 must add
a repository-local TypeScript compiler, a build configuration, and scripts:

```text
app/**/*.ts  --tsc-->  dist/app/**/*.js
index.html             loads dist/app/main.js as type="module"
```

`dist/` is generated and should not be hand-edited. `start.sh` must build
before starting `server.py`, and a separate type-check command must remain
available. Do not introduce a bundler merely to avoid ESM files.

All TypeScript imports use emitted `.js` specifiers. Never change them to
`.ts`; browsers load the generated modules, not the source tree.

`server.py` already sends `Cache-Control: no-cache`. Before cutover, verify
that the dashboard reverse proxy preserves revalidation for emitted
submodules. If it does not, solve deployment caching explicitly before
shipping; query-versioning only `main.js` cannot invalidate its imports.

## Migration sequence

### Step 0 — Toolchain and characterization safety net

Add `package.json`, lock the TypeScript version, add build/test configs, ignore
generated output, and teach `start.sh` to build before serving. Do not change
`index.html` yet.

**Progress:** the one-for-one `app_tests/` design scaffold, repository-local
compiler and package lock, build config, generated-output policy, `start.sh`
hook, and executable headless-Chrome harness are complete. Six browser tests
now characterize the initial navigation slice. Update lifecycle
characterization and compiled TypeScript unit assertions remain.

Capture the legacy behavior before moving code:

- hash-route parsing cases;
- catalog node resolution and file paths;
- all five detail tabs;
- Construction Status drill-down and Back behavior;
- canonical Interface File and Event Contracts lessons;
- Update Documentation clean, dirty, running, completed, and failure states;
- `?embedded=1` hiding the in-page nav.

Use plain Node assertions against compiled TypeScript for unit tests. Keep the
headless-Chrome test as an integration test; HTTP fetches alone cannot prove
script execution or Mermaid rendering.

### Step 1 — Pure catalog and routing policies

Implement:

- `catalog/sections.ts`;
- `catalog/doc-tree.ts`;
- `catalog/document-path.ts`;
- `catalog/tabs.ts`;
- `routing/hash-route.ts`.

Move literals verbatim. Pass the hash string into the parser; it must never
read `window.location`. Add unit tests before deleting the corresponding
legacy functions.

### Step 2 — HTTP Adapter, boundary decoders, and DocsApi Facade

Implement `HttpClient`, the raw-fetch Adapter, response decoders, and the
Facade. Move all eight fetch call sites behind the narrow gateway interfaces.

Acceptance rules:

- `rg "fetch\\(" app --glob '*.ts'` finds a call only in
  `fetch-http-client.ts`;
- malformed JSON and structurally invalid JSON fail at the decoder boundary;
- every application API path is relative;
- `UNKNOWN_STATUS` remains a named Null Object and is not duplicated.

### Step 3 — Mermaid Strategies and the enhancer chain

Implement the Mermaid runtime Adapter, loader, renderer, pan/zoom Strategy,
and step-tooltip Strategy. Implement every content enhancer and the ordered
pipeline:

```text
execute scripts -> render diagrams -> wire run-tests -> wire terminal
```

Then implement the base content host and its enhancer Decorator. This is the
one intentionally behavior-changing step: inline lessons gain terminal-button
wiring. Verify idempotence by applying the chain twice in a focused test.

### Step 4 — Navigation Observer, Presenter, and passive view

Implement `NavigationStore`, `NavigationPresenter`, and `NavigationView`.
State changes publish once; subscribers decide whether to render navigation or
content. Remove direct DOM work from navigation commands.

Verify Home, folder, leaf, detail, and Back transitions as state tests before
browser verification.

### Step 5 — Content controller and tab Strategies

Implement the fragment source, content controller, Strategy registry, static
fragment Strategy, Construction Status Strategy, and Update Documentation
Strategy.

There must be no branching on literal tab keys outside Strategy construction
and route validation. Adding a tab should require a descriptor and one
Strategy registration, not edits across controllers and views.

### Step 6 — Construction Status subsystem

Implement in dependency order:

1. task DOM Adapter;
2. task-tree Composite;
3. status and trail policies;
4. task-navigation Observer and presenter;
5. lesson Builder;
6. canonical source and URL rebaser;
7. summary renderer and construction controller.

Preserve the exact authored DOM structure. The consistency checker must still
report 66 plan files and no inconsistencies. Walk both canonical lessons
through the real SPA navigation; they are the most fragile path.

### Step 7 — Update Documentation subsystem

Implement the status cache, refresh use case, update State machine, passive
view, and Mediator. Timers belong only to the State machine and must be disposed
when navigation leaves the update tab.

Test every discriminated state exhaustively. A failed status request degrades
to the Null Object; a failed poll remains recoverable and must not create
parallel polling loops.

### Step 8 — Composition root and browser cutover

Implement `composition-root.ts`, `main.ts`, runtime configuration, and the
typed `window.__spa` seam. Wire subscriptions and disposal in the composition
root only.

Change `index.html` to load the generated module entry, bump the cache stamp,
and update the SPA skill's browser recipe to call `window.__spa`.

Only after the TypeScript browser flow passes every check:

- delete the legacy `app.js`;
- remove temporary parity adapters;
- confirm `?embedded=1` behavior;
- confirm the dashboard reverse proxy loads every emitted submodule freshly.

## Verification after every step

While the project remains at the review-only scaffold checkpoint, run:

```bash
/home/adamsl/letta-code/node_modules/.bin/tsc -p app/tsconfig.json
/home/adamsl/letta-code/node_modules/.bin/tsc -p app_tests/tsconfig.json
python3 check_construction_status_consistency.py
git diff --check
```

After Step 0 installs the repository-local toolchain, the standing commands
become:

```bash
npm run typecheck
npm test
node --check dist/app/main.js
python3 check_construction_status_consistency.py
git diff --check
```

Run the SPA through JavaScript-capable Chrome and verify the changed path. For
Mermaid or injected content, confirm rendered SVGs and wired controls in the
DOM; `curl` is not evidence.

Before the final cutover, exercise:

- every top-level section and representative nested leaves;
- all five fixed detail tabs;
- deep links with item, tab, and anchor;
- task drill-down and Back at every level;
- canonical and inline Construction Status lessons;
- Mermaid render, zoom, pan, reset, and sequence tooltips;
- run-tests and terminal button wiring without invoking the destructive route;
- Update Documentation from status check through terminal state;
- standalone and `?embedded=1` modes;
- the deliberate `window.__spa` browser-test API.

## Review rejection checklist

Reject a change if any answer is yes:

- Did a feature module import a concrete adapter instead of receiving a port?
- Did a view fetch data, traverse a domain tree, or read global state?
- Did a controller build detailed DOM markup?
- Did raw JSON get cast with `as` instead of decoded from `unknown`?
- Did a second module start calling `fetch()`?
- Did a content path bypass `EnhancedContentHost`?
- Did a state mutation require a caller to remember a rerender ritual?
- Did a switch on tab keys escape the Strategy boundary?
- Did a module acquire a second actor or second reason to change?
- Did a unit-test file begin covering behavior owned by another source module?
- Did a test stub get counted as executable coverage or a passing assertion?
- Was a GoF pattern added without an actual source of variation or lifecycle?
- Was the cache stamp bumped without verifying emitted submodule caching?
- Was a migration step left uncommitted?

If any answer is yes, stop and repair the boundary before continuing.
