# Handoff — Permanent Object Debugger Bridge Agent

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
