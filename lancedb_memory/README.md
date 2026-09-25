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

## finance_team -- 6-agent vendor pattern + receipt audit team

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

## receptionist_team -- Toyota, the finance team's receptionist

A 7th composition root (`receptionist_team/`) alongside `finance_team/`: a
persistent, conversational agent named **Toyota** you chat with from the
command line, backed by the same LanceDB-memory conversational engine as this
repo's own `main.py` (`agent_memory.agent.ConversationAgent`, unmodified).
Toyota knows about all 6 `finance_team` roles and, when a request clearly
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
turn if the dashboard or mic isn't available -- `--voice` needs `arecord` on
`PATH` for capture (WSL/WSLg audio) and `ffplay`/`mpg123`/`paplay` for
playback; text mode always works regardless.

**Auth is unchanged from the rest of this repo:** `ANTHROPIC_API_KEY` and
`ANTHROPIC_BASE_URL` stay unset so both Toyota's own replies and the
finance_team roles she delegates to run on local Claude Code subscription
(OAuth) auth -- no paid API key. The `GROQ_API_KEY` already configured
elsewhere on this machine is untouched; it keeps doing exactly what it did
before (the dashboard's own STT), reached only indirectly over HTTP.

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
| Decorator | `TokenBudgetedRecall`, `RetryingModel`, `CachingEmbedder` |
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
