# Notes Review — design

Reviewing the blinking verification item in one of EG's notes (the probate deed among them): what blinks,
stop it, make another item blink, rewrite it, resolve it, move to the next one, undo, verify, watch. Every
one of these is an ADW operation with its own ADW id, and nothing else in the workspace re-implements them.

```
uv run adws/adw_notes_review.py notes.<operation> --note <file name> [flags]   # all eleven, one harness
uv run adws/adw_notes_<operation>.py --note <file name> [flags]                 # each one on its own
```

## Who calls it

| caller | path |
|---|---|
| a shell / Claude Code | the ADWs above |
| the Bridge worker | agent `notes`, operations `review_*` (`lancedb_memory/agent_bridge_worker/notes_ops/review.py`), each a subprocess of the ADW |
| ChatGPT | MCP tools `notes_*` (`agent_bridge/gateway/src/NotesTools.php`) → Bridge task → the worker above |
| Toyota | her `bridge_notes` route, `REVIEW_PLANS` in `receptionist_team/bridge_client/notes_plans.py` → the same worker |

The spellings differ only in punctuation: `notes_inspect` (MCP) = `review_inspect` (worker) = `notes.inspect` (ADW).

## Patterns, one per file

| pattern | where | job |
|---|---|---|
| Facade | `plane.py` `ReviewPlane` | the one object an operation talks to: store, snapshots, page, guards, observer, clock |
| Abstract Factory | `plane.py` `default_plane()` | the only place naming concretes; tests build a plane from fakes |
| Command + Template Method | `operations.py` | `ReviewOperation` runs every operation the same way; `WriteOperation` is the skeleton of every write (read → plan → guards → snapshot → write → read back → verify) |
| Chain of Responsibility | `guards.py` | the five rules below, first objection stops the write |
| Memento | `snapshots.py` `FileSnapshotStore` | the whole note before every write (an undo is refused if someone edited after it), under the gitignored `adws/adw_data/notes_review/` |
| Adapter | `store.py` `BridgeNoteStore` | the dashboard's token-protected Notes interface, the only way the note is written |
| Remote Proxy | `page.py` `AdwPageReader` | what the real dashboard tab shows, through `browser.notes_state`; `NoPage` is its Null Object |
| Observer + Composite | `observers.py` `JsonlActivity`, `FanOut`, `NoObserver` | an activity line per operation |
| Registry | `catalog.py` `COMMANDS` | public name → operation; the ADW harness and the one-operation ADWs run from it |

Every dependency is an `I`-prefixed Protocol in `interfaces.py`; data crossing a boundary is a Pydantic model in
`models.py`.

## The five guards

- `RevisionGuard` — a change to the words names the revision the caller read, and the note still holds it.
- `UnrelatedTextGuard` — only the item's own line (and the focus marker line) may differ afterwards.
- `EvidenceGuard` — resolving needs evidence or EG's approval, in words; the reason is kept in the snapshot.
- `StaysOpenGuard` — an edit may not quietly resolve an open item; that is `mark_resolved`'s job.
- `ActuallyResolvedGuard` — `mark_resolved` may not call an item done while its line still reads as open.

The worker and the gateway also refuse a `mark_resolved` without evidence or approval, and a `watch` without
`until`, before a task is queued — a convenience, not the enforcement. The enforcement is here.

## The focus marker

`<!-- notes-review-focus: TARGET -->` in the note says which item blinks; an empty TARGET stops all blinking.
Item ids are `r-` + the first 10 hex of sha256(the item's focus target), so an id holds while that item's own words do, snapshot ids `s-` + 10 hex. Every
write's answer carries the note's *new* revision; when the read-back or the page check fails, the answer is
`ok: false` even though the note was written, so a caller does not carry a revision it cannot trust.

## Watching

`notes.watch` replaced the retired `adw_notes_highlight_watch.py`, which read highlights off its own headless
copy of the page. Watching now reads the saved note by the dashboard's own rules and, with `--page`, confirms on
the real dashboard tab. `--until clear | change | active_change`.

## Tests

```
cd adws && uv run --with pytest --with pydantic pytest -q notes_review/tests
```

All offline, against a synthetic deed with no real names. The real deed is never committed.
