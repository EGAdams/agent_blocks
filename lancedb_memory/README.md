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
