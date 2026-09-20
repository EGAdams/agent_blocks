---
id: iconversationagent
name: IConversationAgent
group: Planned core
status: partial
---

# IConversationAgent

> The port now exists, with two adapters and one shared contract suite. The renderers have not been moved onto it yet.

**Partial** — js/abstract/conversation-agent.interface.js + LettaAgentAdapter + FakeConversationAgent, 29 tests. InputOptionsRenderer still fetches directly.

## Responsibility

- Submit one user turn to a conversation engine, stream back the public assistant events, and cancel by run identity. One narrow contract for 'something that can hold a conversation'.
- Its value is substitution. With this port, swapping Letta for a local model, a different cloud agent, or a fake for tests is a composition-root change. Without it — which is where we are — every caller is welded to Letta's HTTP shape.
- It also draws a line the renderers do not: public assistant text is not the same thing as reasoning, tool calls, tool results, or status events. Only the first is speakable, and that is a property of the event kind, enforced once by SpokenOutputPolicy — not re-decided in each renderer.

## Contract

submit() is an async generator so a streaming adapter and a request/response one present the same shape — the non-streaming Letta path simply yields two events at the end. Callers write one loop either way, which is what makes the adapters substitutable.

```text
ConversationAgent   js/abstract/conversation-agent.interface.js   (shipped)

  submit(turn, generationId)  -> AsyncGenerator<AgentEvent>
  cancel(generationId)        -> void   safe for an unknown generation

  ConversationTurn = { agent, text, conversationId? }
  AgentEvent       = { kind, text, generationId, name?, detail? }

  parseAgentEvent(raw, gen)   untrusted -> AgentEvent | null (fail closed)
  isSpeakable(kind)           only assistant_text

  AgentEvent kinds =
      assistant_text   speakable
    | reasoning        never spoken
    | tool_call        never spoken
    | tool_result      never spoken
    | status           never spoken
    | terminal         ends the stream

What exists instead (each a separate, narrow Letta strategy):

  ReceptionistIntentStrategy.evaluate(transcript)
  RouteStrategy.classify(text)
  CommandCompletenessStrategy.assess(partial)
  NoteCommandInterpreter.interpret(request)

...plus the raw fetch the renderers still make directly:

  POST /api/letta-code-message  { agent, text } -> { ok, reply }
```

## Implementations

- **ConversationAgent** (current) — `js/abstract/conversation-agent.interface.js`
  The port, the AgentEvent vocabulary, and parseAgentEvent — which drops an unrecognisable event rather than guessing a kind for it.
- **LettaAgentAdapter** (current) — `js/implementation/letta-agent-adapter.js`
  The first adapter, over /api/letta-code-message. Owns the 930s timeout and per-agent conversation resume so no renderer has to.
- **FakeConversationAgent** (current) — `js/implementation/fake-conversation-agent.js`
  Scripted adapter, and a first-class one: it passes the same contract suite as the Letta adapter, so the voice UI can be driven end to end with no server.
- **Direct fetch in renderers** (current) — `js/implementation/detail-renderers.js`
  InputOptionsRenderer.send() POSTs /api/letta-code-message itself. This is the concrete dependency the port would remove.

## The renderers today vs. the port that now exists

Left: what the shipped renderers still do — policy depends on a concrete technology. Right: what is now built and tested, waiting for a caller. Both sides are real code; only the left one runs.

```mermaid
flowchart LR
  subgraph Now["Today"]
    R1["InputOptionsRenderer<br/>(high-level policy)"]
    L1["POST /api/letta-code-message<br/>(concrete Letta)"]
    R1 --> L1
  end
  subgraph Wanted["With the port"]
    R2["ConversationCoordinator<br/>(high-level policy)"]
    P{{"IConversationAgent"}}
    A1[LettaAgentAdapter]
    A2[FakeConversationAgent]
    A3["LocalModelAdapter<br/>(future)"]
    R2 --> P
    A1 -.-> P
    A2 -.-> P
    A3 -.-> P
  end
```

## The turn the port would own

Note the event kinds: only assistant_text reaches the synthesizer. Today that filtering is re-decided in each renderer.

```mermaid
sequenceDiagram
  participant CO as ConversationCoordinator
  participant ICA as IConversationAgent
  participant LAA as LettaAgentAdapter
  participant Toyota
  participant SOP as SpokenOutputPolicy
  participant TTS as SpeechSynthesizer

  CO->>ICA: submit(turn, gen-7)
  ICA->>LAA: (selected adapter)
  LAA->>Toyota: POST /v1/agents/{id}/messages
  Toyota-->>LAA: reasoning
  LAA-->>CO: reasoning (gen-7)
  CO->>SOP: gate
  SOP--xTTS: rejected — not speakable
  Toyota-->>LAA: assistant_text
  LAA-->>CO: assistant_text (gen-7)
  CO->>SOP: gate
  SOP->>TTS: speak
  Toyota-->>LAA: terminal
  LAA-->>CO: terminal (gen-7)
```

## Tests

- `dashboard/tests/test_receptionist.py` — 5 tests
  One Letta-backed strategy's prompt, strict parsing, and fail-closed behaviour — the pattern a real adapter would follow.
- `dashboard/tests/test_note_commands.py` — 27 tests
  Two more Letta strategies: malformed replies, contradictory replies, and an unreachable server all leave state untouched.
- `js/tests/http-note-command-services.test.js` — 7 tests
  The browser-side adapters validate responses and fail closed on transport errors.
- `js/tests/conversation-agent-contract.js` — 6 tests
  The shared suite, run against both adapters: one terminal event ends every turn, every event carries the generation it was submitted with, the answer arrives as speakable assistant text, nothing else is speakable, a cancelled turn delivers nothing and raises TurnCancelledError, and cancelling an unknown generation is safe.
- `js/tests/letta-agent-adapter.test.js` — 18 tests
  The contract suite plus the characterization: the exact POST body, the 930s timeout, per-agent conversation resume, an explicit turn id winning over the stored one, ok:false surfacing the server error, a failed turn not overwriting the remembered conversation, and no network call at all for an empty turn.
- `js/tests/fake-conversation-agent.test.js` — 11 tests
  The contract suite plus scripting: unspeakable kinds pass through in order, a script function sees the turn, malformed entries are dropped exactly as a real adapter drops them, and cancelling mid-stream stops delivery at the next event.
- `js/tests/spoken-output-policy.test.js` — 10 tests
  The event-kind half of this contract at the point of use — see the SpokenOutputPolicy tab.

### Not covered

- The live path: InputOptionsRenderer still has its own fetch, and no test asserts the renderer goes through the port — because it does not.
- Streaming and event ordering beyond two events: no adapter streams yet.

### Tests to write

- A renderer test asserting InputOptionsRenderer.send() goes through the port, once it does.
- A third adapter — even a deliberately odd one — to prove the contract suite catches a divergence rather than just describing the two adapters that exist.

## Development status

### Done

- The port exists, with submit/cancel and a typed six-kind event union.
- Two adapters satisfy it, and a shared contract suite runs against both — that suite is the Liskov requirement made executable.
- Cancellation by generation id exists and is safe to call for a generation that already finished, was never submitted, or was cancelled before.
- parseAgentEvent fails closed on adapter output the same way the note-command parsers do: an unknown kind is dropped, and blank assistant text never becomes a spoken blank.
- A characterization test now pins the live request shape and the 930s budget, which is what made adopting the port safe.
- Four narrow, fail-closed Letta strategies exist and demonstrate the pattern works well in this codebase.

### Gaps

- No caller. The renderers still fetch directly, so nothing in production goes through the port yet.
- Still no streaming: /api/letta-code-message returns one lump after up to 900 seconds, so LettaAgentAdapter yields assistant_text then terminal at the end.
- Cancellation is delivery-side only for the Letta adapter — the endpoint has no server-side cancel, so the work still runs and only the answer is dropped.
- Only assistant_text and terminal are ever produced today; reasoning, tool_call, tool_result and status are contract-only until something streams.

## Dependencies

### Depends on

- Nothing concrete — that is the entire purpose of the port

### Used by

- Nobody yet — InputOptionsRenderer and AgentsRouterRenderer still call fetch directly
- ConversationCoordinator (planned)

The Dependency-Inversion violation is now fixable rather than fixed: the port and its adapters exist, so pointing InputOptionsRenderer at them is a composition-root change. Until that lands, high-level UI policy still imports a Letta-shaped HTTP call, and the 930-second timeout still leaks into renderer code as the symptom.

## Next work

- Point InputOptionsRenderer at the port instead of fetch, deleting its inline conversation-id bookkeeping and its knowledge of the 930s timeout. This is the step that makes the port load-bearing.
- Do the same for AgentsRouterRenderer.
- Thread a VoiceSession generation id through both, so replies pass SpokenOutputPolicy before reaching the synthesizer.
- Give the adapter real streaming once the Letta server's streaming is usable — the port shape already allows it, so that becomes an adapter change and nothing else.
