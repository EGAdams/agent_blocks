---
id: letta-agent-adapter
name: LettaAgentAdapter
group: Planned core
status: partial
---

# LettaAgentAdapter

> Two adapters now: LettaClient isolates Python's HTTP, and a browser LettaAgentAdapter satisfies IConversationAgent. Still no streaming.

**Partial** — Python transport adapter + five strategies, plus js/implementation/letta-agent-adapter.js (18 tests). No caller for the browser one yet; nothing streams.

## Responsibility

- Translate the Letta API into whatever contract the application actually needs, so no policy object ever learns Letta's URL shapes, payload keys, or response envelope.
- Today one small piece of this is real and load-bearing: LettaClient wraps urllib with three methods, and every Letta-backed strategy in the voice system goes through it. Swapping the transport (or faking it in tests) is a one-object change, and all five strategies' tests do exactly that.
- The conversation-shaped adapter now exists on the browser side: LettaAgentAdapter turns one turn into typed AgentEvents carrying a generation identity, and owns the two transport facts renderers keep having to remember — the 930-second budget and the conversation_id that must be echoed back or the CLI silently starts a fresh session. What it still cannot do is stream or cancel server-side work; the endpoint offers neither.

## Contract

send_message hardcodes stream:false. Letta v0.16.7 on this server has streaming and background mode broken, which is why — but the limitation is currently invisible to callers.

```python
LettaClient   voice/letta_client.py   (shipped, 3 methods)

  clear_messages(agent_id)        reset history; failure never blocks the call
  send_message(agent_id, text)    POST /v1/agents/{id}/messages, stream=False
  resolve_agent_id(name)          GET /v1/agents?limit=200

LettaAgentAdapter   js/implementation/letta-agent-adapter.js   (shipped)

  submit(turn, generationId)  -> AsyncGenerator<AgentEvent>
                                 assistant_text, then terminal
  cancel(generationId)        -> void   delivery-side only

  LETTA_TURN_TIMEOUT_MS = 930000   the 900s server budget, plus headroom
  conversation resume: storage["msi-conv-<agent>"], per agent
```

## Implementations

- **LettaClient** (current) — `voice/letta_client.py`
  The transport adapter. Used by cleanup, receptionist, router, completeness and interpreter strategies.
- **LettaAgentCleanup** (current) — `voice/cleanup.py`
  Use-case adapter: transcript → tidied text.
- **LettaReceptionistIntentStrategy** (current) — `voice/receptionist.py`
  Use-case adapter: transcript → {addressed, cleaned_text}.
- **LettaAgentRouteStrategy** (current) — `router/classify.py`
  Use-case adapter: transcript → {agent, remainder}, with a deterministic first tier.
- **LettaCommandCompletenessStrategy** (current) — `voice/note_completeness.py`
  Use-case adapter: partial command → {complete, reason}.
- **LettaNoteCommandInterpreter** (current) — `voice/note_interpreter.py`
  Use-case adapter: note + command → typed intent.
- **LettaAgentAdapter** (current) — `js/implementation/letta-agent-adapter.js`
  The browser conversation adapter, over /api/letta-code-message. Request/response, so it yields two events at the end rather than streaming.
- **Streaming Letta adapter** (planned) — `—`
  Blocked on the server: Letta v0.16.7 here has streaming and background mode broken. The port shape already allows it, so this stays an adapter change.

## Adapter layering as it exists

Five use-case adapters, one transport adapter, one external system. Tests replace LettaClient wholesale.

```mermaid
flowchart TB
  subgraph Policy["Application policy"]
    P1[VoicePipeline]
    P2[NoteCommandService]
    P3["Router endpoint"]
    P4["Receptionist endpoint"]
  end
  subgraph Ports["Ports (ABCs)"]
    C1{{CleanupStrategy}}
    C2{{CommandCompletenessStrategy}}
    C3{{NoteCommandInterpreter}}
    C4{{RouteStrategy}}
    C5{{ReceptionistIntentStrategy}}
  end
  subgraph Adapters["Letta use-case adapters"]
    A1[LettaAgentCleanup]
    A2[LettaCommandCompletenessStrategy]
    A3[LettaNoteCommandInterpreter]
    A4[LettaAgentRouteStrategy]
    A5[LettaReceptionistIntentStrategy]
  end
  LC["LettaClient<br/>(transport adapter)"]
  Letta[(Letta HTTP API)]

  P1 --> C1
  P2 --> C2
  P2 --> C3
  P3 --> C4
  P4 --> C5
  A1 -.-> C1
  A2 -.-> C2
  A3 -.-> C3
  A4 -.-> C4
  A5 -.-> C5
  A1 --> LC
  A2 --> LC
  A3 --> LC
  A4 --> LC
  A5 --> LC
  LC --> Letta
```

## One adapter call, including the current failure

How the 2026-08-12 outage looked from inside: the shared worker agent sat on a dead Gemini BYOK handle, so every adapter returned its fail-closed value and the UI simply looked idle. Fixed by moving the agent to chatgpt-plus-pro/gpt-5.6-luna; kept here because the failure MODE is permanent even though this instance is resolved.

```mermaid
sequenceDiagram
  participant S as LettaCommandCompletenessStrategy
  participant LC as LettaClient
  participant Letta
  participant Gemini as lc-gemini provider

  S->>LC: clear_messages(agent)
  LC->>Letta: POST .../messages/clear
  S->>LC: send_message(agent, prompt)
  LC->>Letta: POST /v1/agents/{id}/messages
  Letta->>Gemini: generate
  rect rgb(247,235,233)
    Gemini--xLetta: 401 UNAUTHENTICATED
    Letta--xLC: HTTP 401 llm_authentication
  end
  LC--xS: raises
  S-->>S: CommandCompleteness.incomplete("detector unavailable")
  Note over S: fails closed — correct,<br/>but invisible to the user
```

## Tests

- `dashboard/tests/test_cleanup.py` — 7 tests
  Prompt construction, history clearing, and that a failed cleanup falls back to the raw transcript rather than losing it.
- `dashboard/tests/test_router_classify.py` — 14 tests
  The deterministic exact-name tier runs before any LLM call, word-boundary matching, and that every parse failure fails closed to 'no agent'.
- `dashboard/tests/test_note_commands.py` — 27 tests
  Both newer adapters, including that an unreachable Letta yields a fail-closed service instead of an exception, and that a down server is never cached.
- `dashboard/tests/test_receptionist.py` — 5 tests
  Strict JSON parsing and refusal to act on invented model output.
- `js/tests/letta-agent-adapter.test.js` — 18 tests
  The shared contract suite, plus the characterization of the live call: exact POST body, the 930s timeout, per-agent conversation resume, ok:false surfacing the server error, and no network call at all for an empty turn.

### Not covered

- LettaClient itself has no direct unit test — it is only exercised through fakes that replace it.
- resolve_agent_id's pagination behaviour when more than 200 agents exist.
- A 401 from the provider (the failure actually happening in production right now) is covered only by the generic 'exception → fail closed' path.

### Tests to write

- A LettaClient test against a stubbed urlopen, pinning URL shapes and the clear_messages-never-raises guarantee.
- A test that the browser adapter is what the renderer actually calls — meaningful only once the renderer is moved onto it.
- A test asserting a provider auth failure produces a distinguishable status, so the dashboard can show 'LLM auth failed' instead of silently waiting.

## Development status

### Done

- One transport adapter isolates all Letta HTTP access for the voice system.
- Five use-case adapters share one pattern: clear history, one strict-JSON prompt, strict parse, fail closed.
- Agent-id resolution is centralised, and the note-command factory now degrades to a fail-closed service when Letta is unreachable instead of raising into the request handler.
- Every adapter is tested with an injected fake client — no test touches the network.
- The browser adapter exists and passes the shared IConversationAgent contract suite, so a fake and the real thing are interchangeable to a caller.
- The 930s timeout and per-agent conversation resume now live in one object instead of being re-remembered by each renderer, and a failed turn no longer risks overwriting a good conversation id.

### Gaps

- No streaming: send_message pins stream:false, so a long reply arrives as one lump.
- No cancellation of server-side work — LettaAgentAdapter.cancel() suppresses delivery only, because the endpoint offers nothing else. That is a narrower guarantee than the name suggests and is documented at the call site.
- Generation identity exists in the browser adapter but on none of the five Python strategies, so a late reply from those still cannot be fenced.
- Nothing calls the browser adapter yet — the renderers keep their own fetch.
- resolve_agent_id fetches up to 200 agents and scans linearly on every cold build.
- A provider auth failure is indistinguishable from 'still thinking' at the UI. The lc-gemini 401 below went unnoticed for a day because every adapter correctly failed closed and the dashboard simply looked idle.

## Dependencies

### Depends on

- Letta HTTP API
- urllib (stdlib)

### Used by

- All five Letta-backed strategies in voice/ and router/
- IConversationAgent — LettaAgentAdapter is its first adapter

Correct direction: the strategies depend on LettaClient's three methods, and every strategy's tests inject a fake client with the same three methods. No strategy imports urllib.

## Next work

- Surface provider auth failures distinctly from 'still thinking'. A dead LLM should light up the dashboard, not look like silence — this is the single highest-value gap on this tab.
- Surface provider auth failures distinctly from 'still thinking', so a dead LLM is visible in the dashboard rather than looking like silence.
- Add a direct LettaClient test with a stubbed urlopen.
- Once IConversationAgent exists, build LettaAgentAdapter against it — streaming first, cancellation second.
