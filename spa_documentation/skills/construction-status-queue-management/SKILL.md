---
name: construction-status-queue-management
description: Keeping a Construction Status plan's cross-item "what's next" sequencing (stage board, ordered unblocking queue, per-lesson pointers) in one place instead of duplicated across lesson pages. Use whenever a task's status changes, a new downstream item is added to the queue, or a lesson's "what would bring work back to this page" list needs editing.
---

# Managing the Construction Status "next steps" queue

## The problem this prevents
A plan file (e.g. `basic_agent_construction_status.html`) owns the overall
task tree and stage board for one Agent Block. Individual lesson pages (e.g.
`.../declare_the_plug_in_point/interface_file/index.html`) each cover one
finished item. It is tempting to give every lesson its own full "next steps,
in the order they unblock each other" table — but every remaining item after
stage 1 is shared across lessons, so N lessons each keeping their own copy of
that table means N places that drift the moment one row's status changes.
Read `building-the-spa`'s "Construction Status task trees" section first; this
skill is the narrower procedure for the queue specifically, not the whole task
tree feature.

## The rule
**The owning plan file is the only place with the full ordered queue.** A
lesson page gets, at most, a short section that:
1. Points at the plan's queue section by anchor (`plan.html#next-steps`), and
2. Names only the 1-3 triggers that would specifically reopen *this* lesson
   (not the whole queue) — e.g. "if the reconciliation drops the session id,
   `submit()` grows a parameter and this file changes."

If you find yourself pasting the same Order/Object/State/Why table into a
second lesson page, stop — shrink it to a pointer instead, per the worked
example below.

## Where the plan-level queue lives
In the plan file, the queue is its own numbered section immediately after
"The single next action" (the one-line version) — e.g. section 6, id
`next-steps` — as a `<table class="construction-status-table">` with columns
`Order | Object to work on | State | Why it comes here, and what it unblocks`,
followed by a `<ul>` titled "What would bring work back to \<lesson\>" for
each lesson that has one. Link each row's object to its own task page (or its
future location, if not yet built) and tag it with which stage/step it belongs
to (`<em>Stage 2 &middot; step 3</em>`).

## Checklist: a task's status changes (e.g. Not started → Done)
Touch all of these, in the plan file unless noted:
1. The task tree entry's `data-task-status` attribute.
2. The stage board row's status chip (section "The stage board").
3. The next-steps queue row for that item — remove it once done, or update
   its `State` chip if it moved to "In progress".
4. "The single next action" line/paragraph, if the item that just finished
   was the one named there — replace it with the new front-of-queue item.
5. Any lesson's "what would bring work back" list that named this item as a
   trigger — check whether the condition it described actually landed, and
   if so, update that lesson's own status chip and "how to check this
   yourself" section too.
6. If the finished item is itself a lesson with its own Construction Status
   page (like Interface File), update that page's own status badge and
   "Where this item stands" table — do not leave it saying "In progress"
   after the plan says "Done".

## Checklist: adding a brand-new downstream item to the queue
1. Add a row to the plan's queue table with the next `Order` number, a link
   to the item's task page (or a best-guess future path if the page does not
   exist yet), a `State` chip, and a `Why` cell explaining what it unblocks
   and why it sits at that position (not earlier, not later).
2. If the item deserves its own task-tree entry (most do), add the matching
   `<li class="construction-task">` per `building-the-spa`'s task-tree
   conventions — `data-task-id`, `data-task-label`, `data-task-status`, and
   either an inline `<div class="construction-task-lesson" hidden>` or a
   `data-lesson-src` reference if the lesson is authored as its own file.
3. If it changes what currently blocks or unblocks another row, update that
   row's `Why` cell too — the queue's ordering claims are the whole point of
   the table, so a stale claim is worse than a missing row.

## Checklist: shrinking or writing a lesson's local pointer
1. Keep exactly one callout naming the single first-priority item (usually
   copied verbatim from the plan's own "work on this one first" callout).
2. List only the triggers specific to this lesson's own file(s) — check each
   one still makes sense (does the file/line it describes still exist?).
3. Link to the plan with an anchor, not just the bare filename, so the
   reader lands on the queue section instead of the top of the plan.
4. Do not repeat `Order`, `State`, or `Why` cells for items that are not
   triggers for this specific lesson — that content only needs to exist once.

## Run the consistency checker
`spa_documentation/check_construction_status_consistency.py` is a stdlib-only
script that automates the three checks below. Run it after any edit to a
plan file or a lesson it points at:

```bash
cd spa_documentation
python3 check_construction_status_consistency.py                 # every plan file under the repo
python3 check_construction_status_consistency.py path/to/basic_agent_construction_status.html   # one plan file
```

It exits 1 and prints one line per finding if it sees:
- **`STATUS_MISMATCH`** — a task's `data-task-status` disagrees with its
  `data-lesson-src` target's own `construction-lesson-badge`, or a queue
  row's status chip disagrees with its linked page's badge.
- **`BROKEN_LESSON_LINK`** / **`BROKEN_QUEUE_LINK`** — a `data-lesson-src` or
  queue-row link points at a file that does not exist.
- **`DUPLICATE_QUEUE_TABLE`** — a lesson under a plan's own subtree contains
  its own copy of the "Object to work on" queue table instead of a pointer
  back to the plan.
- **`MISSING_REQUIRED_LESSON_SRC`** — a task listed in the script's
  `REQUIRED_LESSON_SRC` table (currently just Interface File / `stage-1a`)
  doesn't carry the `data-lesson-src` it's required to. This is the specific
  regression that once let the plan silently revert to a 245-line stale
  inline copy of the Interface File lesson instead of referencing the real
  file — nothing else in this checker would have caught a *missing*
  `data-lesson-src` on a task that's still allowed to embed content inline.
  Whenever a lesson gets promoted to "its leaf file is the sole source of
  truth, referenced via `data-lesson-src`" (as documented in CLAUDE.md), add
  its task id to `REQUIRED_LESSON_SRC` in the same change.

It only reads markup — it does not edit anything, and it does not know
whether a given status is *correct*, only whether two places that claim to
agree actually do. A clean run does not mean the content is right, only that
nothing has drifted apart.

## Verifying the edit
- Run the consistency checker (above) — it replaces hand-grepping for the
  three drift patterns it covers.
- Follow every relative link you added or changed by counting directory
  levels by hand (or `find` the target file) — these pages are several
  levels deep and a wrong `../` count fails silently in a browser; the
  checker only follows `data-lesson-src` and queue-row `href`s, not
  breadcrumbs or pager links.

## Worked example
`voice_communication/conversation_agent/basic_agent_construction_status.html`
section 6 ("What's next, in the order it unblocks") holds the full 7-row
queue and the per-lesson "what would bring work back" list. The Interface
File lesson
(`basic_agent_construction_status/declare_the_plug_in_point/interface_file/index.html`,
section 9) was cut down from a duplicated copy of that same table to a
two-paragraph pointer plus its own two triggers (session id removed from
`AgentEvent`; cancellation added to `IConversationAgent`). The Event Contracts
lesson's section 9 is the second instance, written as a pointer from the
start. Use them as the template shape for the next lesson that needs it.

Both directions of the pointer have to be written in the same change: the
lesson's section 9 points up at the plan's queue, and the plan grows a
matching "What would bring work back to \<lesson\>" list under the queue
table. A lesson pointing at a queue that names no trigger for it sends the
reader on a round trip for nothing.
