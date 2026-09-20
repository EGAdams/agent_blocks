---
id: voice-session
name: VoiceSession
group: Planned core
status: working
---

# VoiceSession

> The object that owns one conversation. Built in the browser layer; no caller has adopted it yet.

**Working / Needs Testing** — js/abstract/voice-session.js, 13 tests. The lifecycle and the fence are real; nothing in the live UI holds a session yet.

## Responsibility

- Own the identity and legal lifecycle of one voice conversation: which session this is, which agent turn ('generation') is currently live, and which state transitions are allowed — idle, listening, thinking, speaking, interrupted, closed.
- Its real job is generation fencing. When a user interrupts, the turn in flight becomes stale, and everything downstream needs one authoritative answer to 'is this output still wanted?'. Without that, a slow reply from an abandoned turn arrives late and gets spoken over the new one.
- It is deliberately framework-free: no Pipecat, no Letta, no browser APIs. That is what makes lifecycle rules testable without sleeps or a microphone.

## Contract

An illegal transition throws IllegalTransitionError; a superseded generation does not. That split is deliberate: a caller can prevent the first by writing correct code, but the second IS the race the object exists for, so it returns false instead.

```js
VoiceSession   js/abstract/voice-session.js   (shipped)

  id                        -> SessionId     stable for the conversation
  state                     -> idle | listening | thinking | speaking
                               | interrupted | closed
  currentGeneration         -> GenerationId | null

  startListening()          -> state         idle|interrupted|speaking|thinking
  beginTurn()               -> GenerationId  supersedes any live turn
  beginSpeaking(gen)        -> bool          false if gen was superseded
  completeTurn(gen)         -> bool          retires the generation
  interrupt()               -> GenerationId | null   what just went stale
  close()                   -> void          legal from any state, idempotent
  accepts(gen)              -> bool          THE FENCE
  issued(gen)               -> bool          did this session ever mint it

  injected collaborators:
    clock       Clock       session-clock.js — no Date.now() inside
    idSource    IdSource    so a test can name the generation it expects
    onStateChange(change)   { session, from, to, generation, at }
```

## Implementations

- **VoiceSession** (current) — `js/abstract/voice-session.js`
  The session and the fence. Framework-free: no Letta, no Pipecat, no browser API, no timer.
- **ManualClock / SequentialIdSource** (current) — `js/abstract/session-clock.js`
  The deterministic primitives. Tests advance the clock instead of sleeping and assert on ids like gen-2.
- **SystemClock / RandomIdSource** (current) — `js/implementation/system-session-primitives.js`
  The global-backed halves (Date, crypto.randomUUID), kept out of abstract/ per the directory rule. RandomIdSource degrades to a counter where randomUUID is missing.
- **ListenerState** (current) — `js/abstract/continuous-listener.interface.js`
  Stands in partially: idle/listening only, per-listener, no generation and no session identity.
- **RecorderState** (current) — `js/abstract/voice-recorder.interface.js`
  Stands in partially: idle/recording/processing for one clip.

## The lifecycle, as implemented

The transition that matters is interrupted: it is what makes a live turn's output stale without tearing the session down.

```mermaid
stateDiagram-v2
  [*] --> idle
  idle --> listening: start()
  listening --> thinking: turn finalized
  thinking --> speaking: first output token
  speaking --> listening: turn complete
  listening --> interrupted: user speaks over
  thinking --> interrupted: user speaks over
  speaking --> interrupted: user speaks over
  interrupted --> listening: new generation begins
  idle --> closed: close()
  listening --> closed: close()
  speaking --> closed: close()
  closed --> [*]
  note right of interrupted
    output carrying the old
    GenerationId is discarded
  end note
```

## Why the gap still hurts in the live UI

This is what the shipped renderers still do. The session and the policy that fix it now exist — what is missing is that no renderer holds one yet.

```mermaid
sequenceDiagram
  actor EG
  participant UI as Renderer
  participant Letta
  participant TTS as SpeechSynthesizer

  EG->>UI: "what did we spend in March?"
  UI->>Letta: POST /api/letta-code-message
  EG->>UI: "no wait — April"
  UI->>Letta: POST /api/letta-code-message
  Letta-->>UI: answer for MARCH (slow)
  rect rgb(247,235,233)
    UI->>TTS: speak(March answer)
    Note over UI,TTS: nothing knows this turn<br/>was superseded
  end
  Letta-->>UI: answer for APRIL
  UI->>TTS: speak(April answer)
```

## Tests

- `js/tests/voice-session.test.js` — 13 tests
  The headline case — output from a superseded generation is rejected — plus interrupt() making the live turn stale immediately, accepts() failing closed on ids it never issued, a closed session accepting nothing, completeTurn() being idempotent, and the legality table (speaking → interrupted legal, closed → listening not, no turn before listening, nothing to interrupt when idle).
- `js/tests/system-session-primitives.test.js` — 5 tests
  SystemClock reads its injected time source; RandomIdSource prefixes ids and still produces unique ones where crypto.randomUUID is absent.

### Not covered

- Anything about the live UI, because no live code holds a session yet.
- Two sessions sharing one microphone.

### Tests to write

- A renderer-level test: a reply that arrives after an interrupt is never spoken. That is the same assertion as the unit test, one layer up, and it is what will prove the adoption worked.

## Development status

### Done

- The class exists, with the full six-state lifecycle and an explicit legality table.
- Generation fencing works: beginTurn() supersedes the live turn, and accepts() refuses the old id from that moment on.
- accepts() fails closed on everything it did not mint and on everything at all once the session is closed — a stale id, an id from another session, null, and the empty string all return false.
- Time and identity are ports, so the lifecycle tests contain no sleeps and can assert on named generations.
- State changes are reported to an observer with the clock's timestamp — the ISessionObserver seam, in its smallest useful form.
- Two narrow state machines already exist and work (ListenerState, RecorderState) — evidence that the State pattern fits this codebase.

### Gaps

- Nothing in the live UI constructs a session yet, so the fence protects nothing in production.
- Session state is still spread across two listener state machines and closures inside render() functions; adopting the session means moving that, not just adding it.
- Barge-in still needs a caller: ContinuousListener has to call interrupt() when speech starts during SPEAKING.
- There is one session per conversation and no registry, so two conversations at once (agents home + an Input Options tab) have no shared owner.

## Dependencies

### Depends on

- Clock / IdSource — injected so lifecycle tests need no sleeps or randomness

### Used by

- SpokenOutputPolicy — asks accepts() before letting any text reach the speaker
- ConversationCoordinator (planned) — would consult it before releasing output

Nothing concrete: that is the point. A session that imports Letta or a browser API cannot be tested without them.

## Next work

- Give the note-command channel's queue a session and a generation id — it already serialises work, so it is the natural first caller and needs no new concurrency.
- Give InputOptionsRenderer a session, so the dialogue path can fence its replies through SpokenOutputPolicy.
- Only then wire interruption into ContinuousListener: interrupt() on speech during SPEAKING.
- Decide who owns the session when two conversations are open at once — probably one per rendered surface, but that is a decision, not a default.
