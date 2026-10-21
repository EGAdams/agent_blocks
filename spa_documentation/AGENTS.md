# Repository Guidelines

## Project Structure & Module Organization

This nested repository is a framework-free documentation SPA for the parent `agent_blocks` workspace. `index.html`, `app.js`, and `styles.css` still provide the live shell, navigation, and presentation. `server.py` serves static content plus update/status APIs; `doc_source_map.py` maps navigation leaves to source files in the parent repository; `update_documentation_agent.py` regenerates source-derived pages. Top-level trees such as `lancedb_memory/`, `voice_communication/`, and `claude_agent_adapter/` are documentation mirrors, not application source.

`app/` is a parallel strict-TypeScript refactor design. Its `.ts` files are
interfaces, immutable boundary shapes, and factory declarations; they are not
loaded by `index.html` and do not implement the SPA yet. The authoritative
migration work order is `app_refactor_plan.md`. `app_refactor_plan.html` is the
visual progress companion to that work order.

`app_tests/` mirrors all 62 `app/**/*.ts` modules as focused
`<name>.test.ts` design stubs. These files document planned evidence only;
they contain no executable assertions and are not a test suite. Keep each test
file limited to the same single responsibility as its source counterpart.

`spa_browser_tests/` is the documentation tree for this repo's own
`browser_tests/` suite — the one block whose source lives inside this
repository rather than in the parent workspace. Adding, renaming or reordering
a **top-level** nav section also means updating `HOME_NAV_LABELS` in
`browser_tests/nav-fixtures/nav-fixtures.ts`, or two characterization tests go
red.

Each leaf directory uses six fixed files: `basic_agent.html`, `basic_agent.py.html`, `mermaid_class.html`, `mermaid_sequence.html`, `basic_agent_construction_status.html`, and `basic_agent_update_documentation.html`. Keep a leaf synchronized across its folder, `app.js`'s `sections`, and `doc_source_map.py`.

## Build, Test, and Development Commands

- `./start.sh` — serve the SPA at `http://localhost:8931/index.html`.
- `./start.sh 9000` — serve on an alternate port.
- `node --check app.js` — validate JavaScript syntax.
- `/home/adamsl/letta-code/node_modules/.bin/tsc -p app/tsconfig.json` — type-check the TypeScript design stubs without emitting JavaScript.
- `/home/adamsl/letta-code/node_modules/.bin/tsc -p app_tests/tsconfig.json` — type-check the mirrored test-design stubs; this does not run tests.
- `python3 -m py_compile server.py doc_source_map.py update_documentation_agent.py` — validate Python syntax.
- `.venv/bin/python update_documentation_agent.py lancedb_memory/models/turn` — regenerate one mapped leaf; this invokes the Claude Agent SDK.
- `git diff --check` — detect whitespace errors before submitting changes.

The live SPA still has no build step, package manifest, linter, or executable
unit-test suite. `app/tsconfig.json` is deliberately `noEmit`; the TypeScript
migration plan adds a repository-local compiler and emits browser ESM to
`dist/app/` before changing `index.html`.

## Coding Style & Naming Conventions

Match existing four-space indentation and the repository's spaced-bracket style in Python and JavaScript, for example `document.getElementById( "content" )`. Use `snake_case` for Python names and documentation directories, and `camelCase` for JavaScript functions and variables. Keep API and Mermaid asset URLs relative so reverse-proxy hosting under `/agent-block/` continues to work. When changing `app.js` or `styles.css`, bump its cache-busting query string in `index.html`.

TypeScript uses the same four-space and spaced-bracket style, strict interfaces,
and emitted `.js` import specifiers. Internal modules import contracts from the
owning module, not from `app/interfaces/index.ts`; that file is a public
type-only facade.

## Testing Guidelines

Run the SPA and exercise the real navigation in a JavaScript-capable browser. For diagram changes, confirm Mermaid produces an SVG and pan/zoom controls work; `curl` cannot validate injected scripts. Check Update Documentation status for any changed mapping and confirm all six leaf files exist.

Before TypeScript runtime cutover, verify that the `/agent-block/` reverse proxy
revalidates emitted ES submodules. `server.py` already sends the
`Cache-Control: no-cache` header, but versioning only the entry module cannot
invalidate cached imports if the proxy ignores that header.

## Commit & Pull Request Guidelines

The short history uses informal, descriptive subjects without Conventional Commit prefixes. Prefer concise imperative subjects such as `Add Toyota voice documentation`. Pull requests should explain scope, identify affected navigation paths and source mappings, list checks run, and include screenshots for visible UI or diagram changes. Do not fold unrelated dirty-worktree changes into the commit.
