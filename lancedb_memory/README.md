# lancedb_memory — conversational agent with persistent memory

A stateful conversational agent: Claude Agent SDK for completion, LanceDB for
vector recall, a JSON nexus as a portable text archive, Pydantic for every data
contract, and Protocol-based ports so no layer knows what the layer below it is.

## Run

```bash
cd lancedb_memory
.venv/bin/python main.py --session default        # persistent
.venv/bin/python main.py --no-persist             # in-memory, throwaway
.venv/bin/python main.py --session alice --model claude-haiku-4-5-20251001

# --embedding-profile picks BOTH the embedder and its matching table:
.venv/bin/python main.py --embedding-profile hash      # default: free, instant, for tests
.venv/bin/python main.py --embedding-profile sentence  # free, local semantic model, for real chats
.venv/bin/python main.py --embedding-profile openai    # costs money, needs OPENAI_API_KEY, best quality
```

`ANTHROPIC_API_KEY` and `ANTHROPIC_BASE_URL` must be **unset** so the SDK uses
local Claude Code subscription auth. `main.py` is the only file that names a
concrete implementation.

## Test

```bash
.venv/bin/python tests/test_agent.py               # turn loop; no network, no disk
.venv/bin/python tests/test_lancedb_store.py       # real LanceDB; no network
.venv/bin/python tests/test_embedding_profiles.py  # profile factory; no network, no torch import
```

## finance_team -- vendor patterns, receipt audits, and progress checks

A separate composition root (`finance_team/`) that reuses this repo's adapter
convention but, unlike `ConversationAgent`, actually enables tools
(Bash/Read/Write/Glob/Grep, `permission_mode="bypassPermissions"`) because the
job requires querying the rol_finances MySQL DB and walking its receipt
directories -- see `finance_team/adapters/tool_agent.py` for why that adapter
diverges from `agent_memory/adapters/claude_model.py`.

5 agents do map-reduce vendor-payment-pattern analysis, partitioned along the
`expenses.method` enum (a partition that already exists in the schema, so the
4 mappers share no state and run fully concurrently):

| Agent | Scope | Output |
|---|---|---|
| `mapper_cash` | `method='CASH'` rows | `reports/vendor_patterns/cash_monthly_totals.json` |
| `mapper_card` | `method='CARD'` rows | `reports/vendor_patterns/card_monthly_totals.json` |
| `mapper_bank` | `method='BANK'` rows | `reports/vendor_patterns/bank_monthly_totals.json` |
| `mapper_other` | `method='OTHER'` rows | `reports/vendor_patterns/other_monthly_totals.json` |
| `synthesizer` | reads all 4 above | `reports/vendor_patterns/synthesis_report.html` (missed bills, amount anomalies, new recurring candidates) |

A 6th, independent agent audits receipt images on its own:

| Agent | Scope | Output |
|---|---|---|
| `receipt_scanner` | `readable_documents/receipts/` + 3 other receipt dirs | `reports/orphaned_receipts_report.md` |
| `expense_reconciler` | finds expense rows without matching receipts | `reports/unaccounted_expenses_report.md` |
| `progress_check` | reads existing report files only, no DB access | `reports/finance_team_progress_report.md` |

Unlike the seven analysis agents, `progress_check` is excluded from a bare
`orchestrate.py` run. It runs only via `--only progress_check` or Toyota's
routing.

Run it:

```bash
cd finance_team/..   # i.e. lancedb_memory/
.venv/bin/python -m finance_team.orchestrate                     # full team
.venv/bin/python -m finance_team.orchestrate --skip-receipts     # just the 5
.venv/bin/python -m finance_team.orchestrate --only mapper_bank   # one agent
```

Role briefs (system prompt, task prompt, DB credentials briefing) live in
`finance_team/roles.py` and are pure data -- no SDK import there, matching
`agent_memory/ports.py` staying Protocol-only.

## receptionist_team -- Toyota and Mazda, one engine, two profiles

Another composition root (`receptionist_team/`) alongside `finance_team/`:
persistent, conversational agents you chat with from the command line, backed
by the same LanceDB-memory conversational engine as this repo's own `main.py`
(`agent_memory.agent.ConversationAgent`, unmodified). `--agent` picks which
one -- **Toyota** (default) or **Mazda** (see the Mazda section below); the
rest of this section describes Toyota.
Toyota knows about all `finance_team` roles and, when a request clearly
calls for one, delegates to it and relays the result -- otherwise she just
talks. Routing is a `LanguageModel` Decorator (`RoutingModel`, same shape as
`agent_memory/adapters/decorators.py`'s `RetryingModel`): the underlying
`ConversationAgent` never knows a delegation happened.

```bash
cd lancedb_memory
.venv/bin/python -m receptionist_team.main                  # typed chat
.venv/bin/python -m receptionist_team.main --voice           # mic in / spoken out
.venv/bin/python -m receptionist_team.main --role-model claude-haiku-4-5-20251001
```

**Voice reuses the dashboard's existing pipeline** instead of re-implementing
speech I/O: `receptionist_team/adapters/voice_io.py` calls the running
`dashboard-server.service` over HTTP -- `POST /api/voice` (whisper.cpp/Groq
STT, same path the browser's push-to-talk uses) for input, `POST /api/tts`
(server-side edge-tts) for output. It never imports from `dashboard/`, only
talks HTTP to `localhost:8765`, and fails closed to typed input/output per
turn if the dashboard or mic isn't available -- `--voice` needs `parecord` on
`PATH` for capture (WSL/WSLg audio) and `ffplay`/`mpg123`/`paplay` for
playback; text mode always works regardless.

**Auth is unchanged from the rest of this repo:** `ANTHROPIC_API_KEY` and
`ANTHROPIC_BASE_URL` stay unset so both Toyota's own replies and the
finance_team roles she delegates to run on local Claude Code subscription
(OAuth) auth -- no paid API key. The `GROQ_API_KEY` already configured
elsewhere on this machine is untouched; it keeps doing exactly what it did
before (the dashboard's own STT), reached only indirectly over HTTP.

### Opening a voice channel to discuss finance_team progress

Make sure `dashboard-server.service` is running on `localhost:8765`,
`ANTHROPIC_API_KEY` and `ANTHROPIC_BASE_URL` are unset, `parecord` is on
`PATH` for mic capture, and at least one of `ffplay`, `mpg123`, or `paplay`
is available for playback. Then open the channel:

```bash
cd lancedb_memory
.venv/bin/python -m receptionist_team.main --voice --session toyota
```

Ask "what's the finance team's progress so far?" to route to
`progress_check`, which reads existing reports without querying the DB.
"Run the card mapper" instead requests a new, slower DB-backed analysis.
Close and reopen with the same `--session toyota` to recall earlier
conversation and route results from the persistent LanceDB session.

## Mazda -- the second receptionist, same engine

Mazda is no longer a package of her own. `mazda_team/` was retired and she is
now a **profile** inside `receptionist_team/`: `profiles.py` holds the short
list of things that differ between her and Toyota (prompts folder, roster, how
a delegation runs, session, nexus subdirectory, peer recall, TTS voice, which
decorators apply), and `main.py --agent mazda` picks it.

```bash
cd lancedb_memory
.venv/bin/python -m receptionist_team.main --agent mazda           # typed chat
.venv/bin/python -m receptionist_team.main --agent mazda --voice   # mic in / spoken out
.venv/bin/python -m receptionist_team.progress.cli                 # Michi snapshot, no LLM
```

She is a **document-verification** receptionist, not a spreadsheet one: her
prompt (`agents/mazda/system_prompt_template/system_prompt.md`) was written
from the Letta Mazda's own answers about her responsibilities -- statement
arithmetic checked by script, every transaction confirmed present in the
database, duplicates, vendor keys, categories, and one HTML verification
report per statement. Her roster is in `mazda_roles.py`:

| Agent | What she routes to it for | Wired? |
|---|---|---|
| `parser` | pulling transaction rows out of a statement PDF | no |
| `vendor_identity` | working out who an unclear merchant is | no |
| `receipt_linker` | matching receipts to transactions without double-entry | no |
| `categorization` | checking a category fits its vendor key | no |
| `router` | ordering an unclear, multi-step verification request | no |
| `michi` | where every report stands, what's blocked, what's next | **yes** |

The five unwired names are real Letta agents this package cannot reach yet.
Routing to one goes to `delegation.py`'s `UnwiredRunner`, which says plainly
that the connection isn't built and nothing has run -- Mazda's own standing
rule is never to claim work happened without a tool confirming it. Wiring one
in is a new entry in `profiles.py`'s runner map, nothing else.

Michi came across from `mazda_team` intact as `receptionist_team/progress/`.
Her status and completion checks are deterministic Python
(`progress/tracker.py`); an LLM only explains the snapshot and proposes
priorities, and it never sees anything the tracker didn't collect. She reads
`<repo_root>/finance_reports/<year>/<month>/<account>.json` (fields `year`,
`month`, `account`, `transactions` with `id` and `resolved`, plus optional
`generated_at`, `generated_by`, `notes`) and writes only
`progress/michi_state.json`. The repo root defaults to
`receptionist_team/workspace/`; `MICHI_REPO_ROOT`,
`MICHI_EXPECTED_ACCOUNTS`, `MICHI_MONTHS_BACK` and `MICHI_STALE_DAYS`
override the defaults (they were `MAZDA_TEAM_*` before the move). Nothing
writes that report schema yet, so an empty workspace honestly reports
not-started reports.

Mazda's voice is `en-US-AriaNeural` (Toyota keeps the dashboard's own
`en-GB-SoniaNeural`); `DashboardVoiceIO` sends an explicit `voice` id in each
`/api/tts` request when a profile names one. `MAZDA_EDGE_TTS_VOICE` still
overrides it. Both receptionists share one `receptionist_team/.env` -- copy
`.env.example`.

The pre-existing `/home/adamsl/agent_blocks/mazda/` is an unrelated system.

## Layout

```
agent_memory/
  models.py      Pydantic contracts: Turn, EmbeddedTurn, RecalledTurn,
                 MemoryQuery, AgentReply
  ports.py       Protocols: Embedder, MemoryStore, RecallStrategy,
                 PromptComposer, LanguageModel, TurnObserver, Clock
  agent.py       ConversationAgent -- may import models and ports, NOTHING else
  recall.py      SimilarityRecall, TokenBudgetedRecall
  prompts.py     TemplatePromptComposer (<<CONVERSATION>>, <<MESSAGE>>)
  observers.py   Console, Cost, NexusArchive, Composite, Null
  clock.py       SystemClock
  config.py      Settings -- every path defaults inside this repo
  adapters/      the only modules importing lancedb / pyarrow / claude_agent_sdk /
                 sentence_transformers / openai
                   embedding_profiles.py  Factory Method: profile name -> (embedder, table)
main.py          composition root + REPL
```

## Patterns

| Pattern | Applied to |
|---|---|
| Repository (PoEAA) | `MemoryStore` — persistence and retrieval, hidden from the agent |
| Adapter | `LanceDBMemoryStore`, `ClaudeAgentModel`, `HashingEmbedder`, `SentenceTransformerEmbedder`, `OpenAIEmbedder` |
| Strategy | `Embedder`, `RecallStrategy` — interchangeable behind one Protocol each |
| Decorator | `TokenBudgetedRecall`, `RetryingModel`, `CachingEmbedder`, `RoutingModel`, `AgentHandoffModel`, `WorkflowModel` |
| Strategy | `IDelegationRunner` — `ToolAgentRunner` runs a real role, `UnwiredRunner` says plainly that one isn't connected, `MichiRunner` answers from a snapshot; `RunnerMap` dispatches by agent name |
| Observer | `TurnObserver` — logging, cost, nexus archive |
| Abstract Factory | `build_agent()` in `main.py` — assembles the whole matched family: embedder, store, recall, model, observers |
| Factory Method | `build_embedder()` in `embedding_profiles.py` — one profile name in, one matched (embedder, table) pair out; `PROFILE_BUILDERS` is a registry, so a new profile is a new dict entry, not an edited `if/elif` |
| Facade | `ConversationAgent.handle_turn()` |
| Null Object | `NullObserver`, `InMemoryMemoryStore` |

## Design decisions worth knowing

**LanceDB is the sole read path.** The table stores the message body, so recall
never touches the filesystem. In the predecessor script a missing nexus file
silently emptied the recalled conversation; `test_lancedb_store.py` now asserts
that deleting the nexus entirely does not affect recall.

**The nexus is a write-only archive.** One JSON file per turn under `nexus/`,
kept so the raw text survives independently of LanceDB and the vector store can
later be swapped for one that does not retain message bodies.

**Recall happens before persistence,** so a turn is never its own context.

**Every turn is session-scoped.** `--session` partitions memory; the store
filters before the vector search rather than after.

**Three embedders, three tables, one switch.** Anthropic serves no embeddings
endpoint, so the choice is explicit rather than a single hardcoded default.
`--embedding-profile hash` (default) uses `HashingEmbedder`: a 256-dim
L2-normalised bag-of-words, deterministic, offline, no API key -- it matches on
shared vocabulary, not meaning, which is enough to exercise the whole recall
pipeline for free. `sentence` uses a small local `sentence-transformers` model
(384-dim, downloaded once, runs on CPU) for genuinely meaning-based recall
without any API cost. `openai` calls OpenAI's embeddings API (1536-dim, costs
money, needs `OPENAI_API_KEY`) for the strongest quality. A vector's width is
baked into it, so each profile owns its own LanceDB table
(`embedding_profiles.py` pairs them); `LanceDBMemoryStore` still raises on a
dimension mismatch rather than writing unsearchable rows if that pairing is
ever broken.

**The SDK runs isolated.** `setting_sources=[]` and `allowed_tools=[]` stop the
Agent SDK from loading the enclosing repo's `CLAUDE.md` or reading files; the
agent's only context is what our store recalls.
