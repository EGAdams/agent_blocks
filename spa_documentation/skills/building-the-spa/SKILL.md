---
name: building-the-spa
description: Conventions for extending the agent_blocks documentation SPA (spa_documentation/) — directory layout, nav drill-down behavior, source mapping, fixed detail tabs, and Mermaid verification. Use when adding a new agent/tab, filling in a skeleton HTML file, or touching SPA code or documentation pages.
---

# Building the agent_blocks documentation SPA

## What this is
A single-page app at `/home/adamsl/agent_blocks/spa_documentation/` that documents
the `agent_blocks` repo. Plain HTML/CSS/JS, no build step, no framework — modeled
on `/home/adamsl/largo_spa` (fixed left sidebar nav, `#content` main area,
`fetch()` html partials into it).

Run it with `./start.sh` (optionally `./start.sh <port>`) from this directory,
then open `http://localhost:8931/index.html`. `start.sh` launches `server.py`,
not a bare static server, because the Update Documentation tab uses its API.

## Files
- `index.html` — shell: `.brand` label + a single `<nav id="nav">`, and `<main id="content">`.
- `app.js` — all navigation logic, the `sections` config object, and the live
  Update Documentation button/status behavior.
- `styles.css` — fixed left sidebar (170px), single nav link style, `.back` variant (red).
- `doc_source_map.py` — maps each SPA leaf path to its real source file and
  defines the six fixed leaf filenames.
- `server.py` — static server plus git-status and documentation-update routes.
- `update_documentation_agent.py` — regenerates source-derived pages for one
  mapped leaf and writes its update report.
- `home_claude_md.html` — generated from the repo's root `CLAUDE.md` via Python's
  `markdown` package. Regenerate after editing `CLAUDE.md`:
  ```bash
  python3 -c "
  import markdown
  with open('/home/adamsl/agent_blocks/CLAUDE.md') as f:
      text = f.read()
  html = markdown.markdown(text, extensions=['fenced_code', 'tables'])
  with open('home_claude_md.html', 'w') as f:
      f.write(html)
  "
  ```

## Nav is a single flat list, not stacked tiers
`#nav` is one `<nav>` element whose *contents are replaced* at each drill-down
level — it is not three nav bars stacked on top of each other. Exactly one of
these renders at a time:

1. **Home level**: `Home` (active) + one link per top-level key in `sections`.
2. **Folder level** (a top-level tab or nested group was clicked): `Home` or
   `Back` + one link per child in that node's `items`. Only those children
   show; sibling sections/groups disappear until navigating back.
3. **Item/detail level** (a leaf item was clicked): `Back` (red) + the five fixed
   detail tabs (`Source`, `Class Diagram`, `Sequence Diagram`, `Construction
   Status`, `Update Documentation`). `Back` returns to the containing folder.
4. **Construction-task level** (an opted-in Construction Status page is open):
   `Back` (red) + one link per task at the current hierarchy level. A task with
   child tasks gets the existing red Excel comment/note corner triangle via
   `.has-children`; a leaf task does not. Clicking a parent replaces the nav
   with only its direct children. `Back` moves up one task level, and from the
   task root returns to the five fixed detail tabs. The content pane shows only
   the selected task's details, never the expanded hierarchy.

When adding nav behavior, preserve this: never show two levels at once.

## Construction Status task trees

Construction Status pages may become TaskMaster-style drill-down plans without
adding per-object JavaScript. The page itself is the source of truth:

- Use one hidden root `<ol class="construction-task-tree" hidden>` and a
  preceding `<section class="construction-task-focus" hidden>` where `app.js`
  renders the selected task.
- Every task is a direct list child with `class="construction-task"`, a unique
  `data-task-id`, a concise `data-task-label` for the sidebar, and a
  `data-task-status` such as `planned`, `current`, or `done`.
- Put direct child tasks in `<ol class="construction-task-children">` inside
  their parent task. Any depth is supported.
- Do not duplicate task definitions in `app.js`. Its generic parser derives
  the current sidebar level and `.has-children` triangles from this markup.
- Keep the definition tree hidden. Navigation is responsible for expanding and
  collapsing levels; the main pane shows the landing copy or one focused task
  presentation at a time.
- A page may opt into the dynamic university-textbook task renderer by adding
  `construction-textbook-focus` to its focus section. `app.js` then derives the
  selected lesson title, purpose, status reading, scoped counts, and child/leaf
  guidance from the same hidden task markup; do not duplicate that information
  in JavaScript.
- A task may carry an authored lesson: one direct child
  `<div class="construction-task-lesson" hidden>` holding `<section><h2>...</h2>`
  blocks. The renderer clones those sections into the generated lesson after the
  purpose callout, and numbers every `h2` in the finished page, so authored
  headings must not carry their own numbers. An optional `data-lesson-title` on
  that same div names the document -- it replaces the masthead `h1`, which
  otherwise repeats the task row's `<strong>`. Use it when the lesson reads as
  a standing document rather than an instruction ("Interface File Construction
  Status", not "Write the interface file"); it changes the masthead only, so
  nav labels and child rosters keep the short row text. Use it to say what a task means,
  what exists today, what is missing, how we will know it is done, and the next
  concrete action; without it a task still renders, just generically.
- The Interface File task is intentionally different: its lesson source of truth
  is `basic_agent_construction_status/declare_the_plug_in_point/interface_file/index.html`.
  Its task entry in `basic_agent_construction_status.html` contains only a
  `data-lesson-src` reference. Edit the leaf `index.html`; never duplicate its
  article in the overall Conversation Agent plan.
- **A lesson's Mermaid figures need no `<script>` of their own.** Author the
  markup only -- `.mermaid-figure[data-diagram]` > `.diagram-toolbar` +
  `.mermaid-viewport` > `.mermaid-canvas` > `pre.mermaid` -- and `app.js`'s
  `renderLessonDiagrams()` renders and wires pan/zoom for every figure in the
  focus panel. It runs right after `executeScripts( focus )` (which is still
  there, because a lesson may carry a script for something else, and cloned
  `<script>` nodes never run on their own). Keeping this in `app.js` is what
  lets a lesson page stay short; do not paste a per-page copy back in.
  The standalone-open assets (`page.css` / `page.js`) live once, under
  `voice_communication/conversation_agent/basic_agent_construction_status/assets/`.
  A lesson tree in another section links to that one copy by relative path
  (`spa_browser_tests/`'s pages do) rather than getting its own; a second copy
  drifts the moment either is edited. They only matter for a direct open —
  inside the SPA, `app.js` does the diagram and button wiring itself.
  A sequence figure may add a `<ol class="diagram-step-tooltips" hidden>` as
  a direct child of `.mermaid-figure` (after `.mermaid-viewport`); `page.js`
  and `app.js` both zip it positionally against the rendered
  `svg .sequenceNumber` elements, so it needs **exactly one `<li>` per
  autonumbered message**, in order. A short list silently leaves the trailing
  steps untipped rather than erroring, so count the messages in the `pre`
  before writing it. Notes are not numbered and get no entry.
  `MERMAID_THEME` there carries the two settings that are easy to get wrong:
  `textColor` dark, for sequence message text drawn on the page background, and
  `classText` white, for class-diagram labels drawn inside the navy boxes. The
  renderer reads each definition from the `<pre>`'s **innerHTML** with entities
  decoded, because `textContent` drops `<br/>` in labels. Label
  `foreignObject`s still need `overflow: visible` in `styles.css` or descenders
  clip wherever Mermaid's own font stack is not installed.
- **A completed item shows its real source in a full VS Code panel**, with
  filename tab, gutter line numbers and status bar, because the frame is what
  makes a reader trust it is the file on disk and not a paraphrase. Generate
  it -- the markup is a two-column grid per line and is not worth hand-writing:

  ```bash
  .venv/bin/python scripts/generate_vscode_panel.py \
      claude_agent_adapter/models.py \
      --tab claude_agent_adapter/models.py \
      --lines 7-8 --lines 15-25 --caret 15
  ```

  It prints one line to paste into the lesson. The source path is relative to
  the `agent_blocks` workspace root, not this repo. Repeat `--lines` for an
  excerpt; each extra range emits the `.vscode-code-elision` gap row. Omit
  `--lines` for a whole file. It picks the lexer, the tab badge and the
  status-bar language from the file extension, so `.ts` and `.py` both work --
  a new language needs its badge rule in `styles.css` first, since there is no
  generic fallback badge. It reproduces the Interface File, Event Contracts and
  Publish Barrel panels byte-for-byte; that is the regression test if you
  change it. `pre.lesson-snippet` exists for short illustrative fragments that
  are not a real file; it shares the same Dark+ token palette, which is scoped
  `.construction-lesson :is( .vscode-editor, pre.lesson-snippet )`. Panels run
  3-5 KB each, so include the ones that carry the item and cut the rest. When
  the item owns only part of a shared file, show real line ranges with a
  `.vscode-code-elision` row between them rather than the whole file -- the
  "Event Contracts" lesson does this to `contracts.ts`, 5 KB instead of 19 KB,
  and the preserved line numbers are what keep it checkable.
  `voice_communication/conversation_agent/`'s "Interface File", "Event
  Contracts", and "Publish Barrel" lessons are the worked examples of the whole
  shape (the last one shows a lesson whose item spans two languages: three
  panels, TypeScript and Python, each excerpted): a status
  table of what is done and what is not, the source, two figures, what has to
  happen next, and how to check it yourself. Its presentation lives in `styles.css` scoped under
  `.construction-lesson`.
- The renderer derives the rest and must not be duplicated per page: a
  masthead trail (`Stage n of m > Step n of m`, with a leading `Stage n --` /
  `Step n --` prefix stripped from the title), a status-badge reading, a child
  roster table with status chips, and a progress count. Only stages (tasks with
  children) get the roster and the count.
- Set `data-construction-object` on the textbook focus (and normally the hidden
  tree) so generated lesson editions and footers name the owning Agent Block.
  The shared textbook presentation lives in `styles.css`; new object plans do
  not need to duplicate those style rules inside each HTML fragment.
- Landing pages may expose live totals with `data-construction-count`,
  `data-construction-completion`, and `data-construction-progress`. The generic
  renderer calculates them from task statuses whenever Construction Status
  opens. The full hierarchy must remain hidden even when these summaries are
  visible.
- Pages without `.construction-task-tree` retain the ordinary five-tab behavior.

Treat the task hierarchy as a SOLID decomposition, not indentation for its own
sake. A red-triangle branch is one cohesive workstream. An untagged leaf is one
small outcome that can be implemented and verified independently. Split a leaf
again when it still contains multiple responsibilities, multiple unrelated
kinds of evidence, or work owned by different Agent Blocks. Do not place UI
state mutation under an agent transport interface merely because both appear in
the same end-to-end sequence.

### A standalone leaf lesson page's feature set

**A leaf lesson has two renderings, and editing the file only fixes one of
them.** The standalone page is what you get by opening its URL; the SPA shows
whatever the plan's task carries. If the plan embeds its own copy of the lesson
body, the nav renders that copy and your edits to the leaf file are invisible
to every reader who arrives through the sidebar -- with nothing anywhere
reporting a mismatch. So before editing any leaf lesson, grep the plan for the
task's `data-lesson-src`; if the task has an inline
`<div class="construction-task-lesson">` body instead, promote it first
(replace the body with the reference, add the task id to the checker's
`REQUIRED_LESSON_SRC`), then edit the leaf. Event Contracts was found in
exactly this state -- a 16.5 KB stale inline duplicate, the second time this
trap has been hit on this plan.

Some lessons are authored as their own `index.html` under the plan's folder
tree rather than inline in the plan (see `data-lesson-src` above). Those are
full HTML documents with their own `<head>`, loading `../../assets/page.css`
and `../../assets/page.js`, and they carry five interactive features beyond
the prose. **Treat the set as a unit** -- a page with the article but none of
the buttons reads as unfinished next to its siblings, and the gap is invisible
until someone opens both. `declare_the_plug_in_point/interface_file/` is the
reference implementation; `event_contracts/` was brought up to match it and is
the worked example of *adding* them to an existing page.

1. **"Open Terminal in Project"** in the masthead, inside a
   `.project-terminal-control` div with a `.project-terminal-status` sibling.
2. **VS Code panels** for the item's real source -- see the generator above.
3. **Pan/zoom Mermaid figures**, with step tooltips on any sequence diagram.
4. **A "run the checks" button** at the end of the "how to check this
   yourself" section, running the same checks that section lists in prose.
5. **A pointer to the plan's next-steps queue**, never a second copy of it
   (load `construction-status-queue-management` before writing it).

Features 1 and 4 are the same mechanism. Both buttons POST to
`../../../../../api/open-<slug>-project-terminal` and
`../../../../../api/run-<slug>-tests`; `server.py`'s `LESSON_PROJECTS` maps
`<slug>` to a fixed directory plus a fixed `scripts/run_<lesson>_tests.sh`,
and `page.js` wires both kinds generically from `data-open-project-terminal` /
`data-run-test-suite`. **Adding them to a lesson is one registry row plus the
script -- do not hand-write another pair of routes.** Nothing from the request
reaches a shell: the slug selects the directory and the script, which is what
keeps a browser-triggered terminal launch safe. Model a new check script on
`scripts/run_interface_file_tests.sh`: `set +e` so one press reports every
check rather than stopping at the first failure, a numbered label per check,
a pass/fail tally, and `exec bash` at the end so the tab stays usable.

Keep the two cache-busting query strings (`page.css?v=`, `page.js?v=`) in step
across every lesson page when either asset changes -- they are per-page, so
bumping one page's and forgetting its siblings leaves half the lessons on a
stale script.

Use `voice_communication/conversation_agent/basic_agent_construction_status.html`
as the working reference.

That file owns the overall IConversationAgent plan and task hierarchy. The
Interface File lesson is authored separately at
`basic_agent_construction_status/declare_the_plug_in_point/interface_file/index.html`
and is loaded into the plan through its task's `data-lesson-src` reference.

Cross-item "what's next" sequencing (the stage board's ordered queue, and
what each lesson's "what would bring work back to this page" list says) is
its own recurring chore with its own failure mode — the same queue duplicated
across lessons drifting out of sync. Load the `construction-status-queue-management`
skill before touching any of that, rather than re-deriving the convention. It
also documents `check_construction_status_consistency.py`, a stdlib script at
the repo root that flags exactly this kind of drift (status mismatches,
broken lesson/queue links, duplicated queue tables) — run it after editing
any plan file or the lessons it points at.

## Directory layout mirrors `sections` in app.js
Each section key in `app.js`'s `sections` object is expected to have a same-named
folder here (e.g. `catherine_agent_sdk/`). Nested `items` maps mirror nested
folders; leaf items hold these exact six files (see
`catherine_agent_sdk/basic_agent/` for the reference set):

```
basic_agent.html                       # overview, loads when the item tab is clicked
basic_agent.py.html                    # "Source" detail tab
basic_agent_construction_status.html   # "Construction Status" detail tab
mermaid_class.html                     # "Class Diagram" detail tab
mermaid_sequence.html                  # "Sequence Diagram" detail tab
basic_agent_update_documentation.html  # report body for "Update Documentation"
```

Yes — the basenames are literally `basic_agent*` even inside `future_agent/`.
That's intentional per the current skeleton; `app.js` hardcodes these filenames
as constants (`overviewFile`, `detailTabs`) rather than deriving them from the
item key. If a new item ever needs different basenames, that's a deliberate
decision to revisit `app.js`, not an oversight.

## Adding a new section or item
1. Add the key to `sections` in `app.js` (label + `items: {}` or item map).
2. Create the matching folder(s) under `spa_documentation/`, each leaf with the six
   files above (can be empty — see below).
3. Add or update its source mapping in `doc_source_map.py`; without a resolvable
   mapping, git status and Update Documentation cannot work.
4. **A new top-level section only**: add its label to `HOME_NAV_LABELS` in
   `browser_tests/nav-fixtures/nav-fixtures.ts`, in the position the nav
   actually renders it. Two of the six characterization tests assert the home
   nav exactly and in order, so `npm test` is red until you do. Do it in the
   same commit as step 1 — that pairing is what makes the nav change
   deliberate rather than accidental. Adding a leaf under an existing section
   does not touch the fixture.
5. No other nav wiring is needed; navigation renders from `sections`.

If the block documents source that lives **inside this repo** rather than in
`agent_blocks`, two things change: the docs folder cannot reuse the source
directory's name (`spa_browser_tests/` documents `browser_tests/`), and its
`resolve_source_path()` branch resolves against `DOCS_ROOT`, not `REPO_ROOT`.

Folder nodes may have an optional `_overview.html` alongside their child
folders. `showCurrentContent()` loads it as the folder cover page and executes
any embedded scripts, so a polished overview can include the same interactive
Mermaid pattern as detail pages.

## Blank files are expected, not bugs
Skeleton files are created empty and filled in later. `loadFile()` in `app.js`
already handles this: an empty file renders a `.placeholder` "still blank"
message instead of a blank white page, and a 404 renders a "not built yet"
message. When filling in a page for real, just write real HTML into the file —
no wrapper markup required, `loadFile()` injects it as-is into `#content`.

## Style notes
- Match `largo_spa`'s visual language: dark sidebar (`#333`)/link pills
  (`#4c6ef5`), not new colors per nav tier — we deliberately moved away from a
  three-color tiered look back to one nav style plus the red `.back` link.
- Keep `main` centered with the `calc(170px + 40px)` left margin so it lines up
  with the fixed sidebar width; if the sidebar width changes, update both the
  `header` width and this margin together.

## Source tab: syntax highlighting and header line
`basic_agent.py.html` is the fixed legacy filename for the Source tab; the real
mapped source may be Python or TypeScript. It holds source pre-rendered as VS
Code Dark+-colored HTML, not raw `<pre>` text — there's no highlighter wired
into the SPA at runtime, so generate it ahead of time with the language-correct
Pygments lexer and a custom style class mapping VS Code Dark+ token colors,
then paste the output in.
See `catherine_agent_sdk/basic_agent/basic_agent.py.html` for the reference:
a `<style>` block scoped to `.vscode-pre` + Pygments' `.get_style_defs()`
output, then `<pre class="vscode-pre"><code>...</code></pre>` with the
highlighted body. Bare `http*` URLs that show up inside a `# comment` in the
source should become real `<a target="_blank" rel="noopener noreferrer">`
links, colored `inherit` from the comment span (`.vscode-pre .c1 a { color:
inherit; text-decoration: underline; }`) so the link doesn't stick out from
the surrounding comment color — only the underline marks it clickable.

Below the `<h1>Source</h1>` heading, add a one-line subtitle using the
existing `.placeholder` class (already defined in `styles.css` — gray,
italic, no new CSS needed):
```html
<p class="placeholder">Lines: 80&nbsp;&nbsp;Last modified: August, 28 08:19:55</p>
```
`Lines` is the source file's line count (`wc -l`), `Last modified` is its
mtime (`stat -c '%y'`), formatted as `Month, day HH:MM:SS`. This is
generated once and pasted in as static text, same as the highlighted body —
regenerate both if the mapped source file changes.

## Update Documentation tab and source status

The fifth detail tab loads `basic_agent_update_documentation.html`, then `app.js`
fetches relative `api/git-status?item=...` once for the open detail page. Keep
that URL relative: the SPA also runs behind `/agent-block/` in the main Letta
dashboard, where `/api/...` would target the wrong server.

`doc_source_map.py` is the source of truth for leaf-to-source mappings. Its
`DOC_FILES` includes all six page names; `SOURCE_DERIVED_DOCS` currently marks
Source, Class Diagram, and Sequence Diagram as generated evidence. A leaf is
dirty when its mapped source is Git-dirty or any source-derived page is missing
or empty. The Update button then starts `update_documentation_agent.py` through
`server.py` and displays the generated report in the sixth file.

The updater began as a Python-source workflow. When adding TypeScript-backed
objects, ensure generated source uses a TypeScript lexer and language-appropriate
diagram content; the `.py.html` filename does not make the source Python.

## Mermaid diagrams (Class/Sequence tabs) need JS execution + vendoring
`mermaid_class.html` and `mermaid_sequence.html` are not static markup —
they embed a `<script>` that renders a mermaid diagram into an interactive,
pannable/zoomable SVG. Three things have to be true for this to work in this
SPA, all already wired up as of this skill's last update:

1. **`app.js`'s `loadFile()` executes `<script>` tags.** Setting
   `content.innerHTML` does *not* run embedded scripts — the browser ignores
   them. `loadFile()` calls `executeScripts( content )` after every
   `innerHTML` assignment, which clones each `<script>` node so it actually
   runs. Don't remove this; every interactive detail page depends on it.
2. **Mermaid is vendored locally, not pulled from a CDN**, at
   `spa_documentation/vendor/mermaid.min.js` (currently v10.9.8, copied from
   an npm cache — there is no package manifest in this repo, so re-vendoring
   means finding another local copy or downloading one by hand). Diagram
   pages lazy-load it with a small `ensureMermaidLoaded()` helper that checks
   `window.mermaid` first (so navigating between diagram tabs doesn't
   re-fetch it) and otherwise appends `<script src="vendor/mermaid.min.js">`
   to `<head>` — **relative, not `/vendor/mermaid.min.js`**. This page can be
   reverse-proxied under a subpath (e.g. `/agent-block/` in the letta-code
   dashboard); a leading slash resolves against the *proxy's* domain root
   and 404s there, same failure mode as the `/api/git-status` fetch in
   app.js. The document is always loaded at the server's own root regardless
   of how deep the current item's nav path is, so a plain relative path is
   always correct here.
3. **The host document must declare `<meta charset="utf-8">`.** `index.html`
   already does. This matters because mermaid's minified bundle contains
   multibyte UTF-8 characters; without an explicit charset on the *document*
   that's loading the `<script src>`, the browser can guess the wrong
   encoding for the external script and throw `Uncaught SyntaxError: Invalid
   or unexpected token` on load. This only bites pages loaded directly
   (e.g. testing a diagram `.html` file standalone) — going through
   `index.html`'s nav is always safe. If you ever test a diagram fragment in
   isolation, wrap it in a full HTML doc with `<meta charset="utf-8">` first.

### The pan/zoom pattern (mermaid.live-style)
Each diagram page is self-contained (own `<style>` and `<script>`, no shared
JS file) and follows this structure — copy
`catherine_agent_sdk/basic_agent/mermaid_sequence.html` as the reference
rather than re-deriving it:

- `.mmd-wrap` > `.mmd-toolbar` (zoom in/out/reset buttons + a hint label) and
  `#mmd-viewport` (`.mmd-viewport`, fixed height, `overflow: hidden`, dotted
  grid background) > `#mmd-stage` (`.mmd-stage`, `position: absolute`,
  `transform-origin: 0 0`) > `<pre class="mermaid" id="mmd-source">` holding
  the raw diagram definition text.
- Do not add `will-change: transform` to `.mmd-stage`. Keeping the SVG in a
  persistent compositor layer can rasterize it and make Mermaid text blurry;
  the normal `transform` updates already provide the required pan and zoom.
- `ensureMermaidLoaded()` → `mermaid.initialize({ startOnLoad: false })` →
  `mermaid.render()` the source text → `stage.innerHTML = svg` → then wire up
  pan/zoom on the rendered stage.
- Zoom is mouse-wheel, zooming toward the cursor position (not the center) —
  `wheel` listener with `preventDefault()`, scale clamped `[0.2, 4]`. Pan is
  pointer-drag using `pointerdown`/`pointermove`/`pointerup` with
  `setPointerCapture`, scoped to `#mmd-viewport` only (not `window`) so
  listeners never leak across nav re-renders — each nav change replaces
  `#content`, which garbage-collects the old viewport and its listeners.
  Toolbar buttons zoom toward the viewport center.
- Give each `mermaid.render()` call a unique id (e.g. `"mmd-svg-" +
  Date.now()`) — reusing one across nav visits/re-renders can collide.

### Verifying a diagram page actually works
`loadFile()`'s script execution and mermaid's rendering are both invisible
to a plain `curl`/`Read` check — you have to actually run JS. Headless
Chrome is available (`google-chrome --headless --disable-gpu`) and works
well for this: serve the SPA (`./start.sh`), then either
`--dump-dom` a test harness page that calls `selectSection()` /
`selectItem()` / `selectDetail()` from `app.js` directly and logs
`document.querySelectorAll("#content svg").length`, or check
`google-chrome`'s stderr (`--enable-logging=stderr --v=1`) for `CONSOLE`
lines to catch JS errors. Don't `--virtual-time-budget` a *standalone*
diagram fragment and conclude it's broken from a `SyntaxError` there — see
the charset note above; test through `index.html`'s real nav flow instead.

### Verifying a standalone leaf lesson page
A leaf lesson `index.html` is the opposite case, and the warning above does
not apply to it: it is a complete document with its own `<meta charset>` and
its own `page.js`, so you can point headless Chrome straight at its URL and
`--dump-dom`. No harness page, no `index.html` nav flow. Serve the SPA on a
scratch port so you don't fight a running `./start.sh`:

```bash
python3 server.py 8944 &
google-chrome --headless --disable-gpu --no-sandbox --virtual-time-budget=9000 \
  --dump-dom "http://localhost:8944/<lesson path>/index.html" > dom.html
```

Then count what the JS was supposed to produce, rather than eyeballing:
`<svg` (one per figure), `aria-label="Step ` (one per sequence step, which is
how you catch a short tooltip list), `data-project-terminal-wired="true"`,
`data-run-tests-wired="true"`, `data-language="` (one per VS Code panel), and
`could not be rendered` (must be 0 -- `page.js` swallows a Mermaid failure
into the canvas as text instead of throwing, so a broken diagram is a silent
pass on every other check). Run the same counts against the reference lesson
in the same pass; that catches a shared-asset edit breaking a sibling page.

`--screenshot` with a tall `--window-size` renders the whole page, but the
`.venv` has no Pillow by default -- install it there if you need to crop, and
find the interesting bands by scanning rows for the panels' near-black
background rather than guessing offsets.

**Do not `curl` the terminal routes to test them.** They are not dry-runnable:
a successful POST really does open a Windows Terminal tab on the user's
desktop, and probing four of them leaves four tabs behind. Check the wiring
from the DOM (`data-*-wired="true"`) and the registry from `LESSON_PROJECTS`;
POST only when you actually intend a window to appear, and say so afterwards.

### Verifying a lesson renders *through the SPA*, not just standalone
Checking the leaf URL proves the file is good; it does not prove the sidebar
shows it. Verify both. The hash route (`#item=...&tab=...&anchor=...`) has no
`task=` parameter, so a construction task cannot be deep-linked and
`--dump-dom` alone cannot reach one. Drive the real sidebar from a temporary
same-origin harness in the docs root instead -- an iframe plus clicks, since
one nav level renders at a time and a nested lesson needs a click per level:

```html
<iframe id="f" src="index.html#item=<top>/<item>&tab=status"></iframe>
<div id="out">PENDING</div>
<script>
  // after the iframe loads and settles: click each nav level by visible label,
  // then copy the iframe's .construction-task-focus innerHTML into #out so
  // --dump-dom captures the rendered lesson.
</script>
```

Count the same markers as the standalone check against `#out`, and add
`Could not load the canonical lesson` (must be 0 -- `renderCanonicalConstructionLesson()`
turns a fetch failure into that placeholder rather than throwing) and the
rebased `data-api-path`, which must come back absolute against the server root.
When a click finds no link, print the nav's visible labels in the failure
message; that is what tells you which level you are on. **Delete the harness
file when you are done** -- it lives in the served docs root.
