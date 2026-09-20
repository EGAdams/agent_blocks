---
id: overview
name: Overview
group: Start here
status: partial
---

# Overview

> What the voice system is, what actually shipped, and what is still only a plan.

**Partial** — A working voice stack ships inside the dashboard. The plan's browser-side core objects now exist and are tested, but no live caller uses them yet. The Pipecat rebuild has not been started.

## Responsibility

- Voice Communication is the path from a spoken sentence to a Letta agent doing something about it, and back to speech. Today that path is: browser captures audio → text arrives (whisper.cpp for push-to-talk, browser SpeechRecognition for continuous listening) → a narrow Letta-backed strategy decides what the text means → an agent acts → edge-tts speaks the reply.
- The single most important thing to understand before working here: there are TWO architectures in play. The original plan (2026-08-01) designed a Pipecat-based system in /home/adamsl/talking_agent_parts around VoiceSession, ConversationCoordinator, IConversationAgent and LettaAgentAdapter. That directory still contains only the plan document. Meanwhile a different, working voice system grew inside dashboard/, and its seams turned out narrower and differently named.
- As of 2026-08-26 the plan's browser-side core objects have been built inside dashboard/js/ rather than in talking_agent_parts/ — VoiceSession, the ConversationAgent port with two adapters, and SpokenOutputPolicy, with 57 tests. They are correct and they are unused: not one renderer constructs a session or calls the port. Read that sentence twice before planning work here, because it is the whole state of the project. The design questions are answered; the remaining work is adoption, and adoption is the part that changes what a user hears.
- This workspace documents the code that exists, marks each object honestly, and shows where the two designs meet. Where the shipped code has a real seam the plan never named, it gets a tab. Where the plan named an object nobody built, it gets a tab that says so and explains what stands in for it today.

## Contract

Every Letta-backed strategy above follows the same shape: clear the agent's history, send one strict-JSON prompt, parse it strictly, and fail closed on anything unexpected.

```text
Shipped ports (all have an ABC/base class + at least one implementation + tests)

  Python  dashboard/voice/, dashboard/router/
    TranscriptionStrategy        audio bytes  -> raw text
    CleanupStrategy              raw text     -> tidied text
    SpeechSynthesisStrategy      text         -> audio bytes
    ReceptionistIntentStrategy   transcript   -> {addressed, cleaned_text}
    RouteStrategy                transcript   -> {agent, remainder}
    CommandCompletenessStrategy  partial cmd  -> {complete, reason}
    NoteCommandInterpreter       note + cmd   -> edit | save | none
    NoteRepository               note         -> SavedNote

  Browser  dashboard/js/abstract/
    ContinuousListener           streaming speech (State)
    VoiceRecorder                push-to-talk capture (State)
    SpeechSynthesizer            speaking replies
    NoteDocument                 the text surface being edited
    TranscriptBuffer             final/interim accumulation
    VoiceCommandChannel          the one real coordinator

Built from the plan, browser side (tested; no live caller yet)

  Browser  dashboard/js/
    VoiceSession                 lifecycle + generation fencing
    Clock / IdSource             injected time and identity
    ConversationAgent            submit(turn, gen) -> AgentEvent stream
    LettaAgentAdapter            the first adapter, over the live endpoint
    FakeConversationAgent        the second, for tests and offline UI
    SpokenOutputPolicy           the one gate in front of the speaker

Still only a plan

    ConversationCoordinator  PipelineFactory
    VoiceCommunicationApplication
```

## Implementations

- **Dashboard voice stack** (current) — `dashboard/voice/, dashboard/router/, dashboard/js/`
  The system that actually runs. 8 Python ports + 6 browser ports, ~180 tests.
- **Pipecat rebuild** (planned) — `/home/adamsl/talking_agent_parts/`
  Plan document only. Phase 0 (baseline, ADR-001, pyproject) never started.
- **voice_agent prototype** (deprecated) — `/home/adamsl/voice_agent/`
  Standalone CLI prototype. Kept as evidence + recordings; the plan explicitly says do not port VoiceAgent or LanguageProcessor.

## The adoption backlog, in order

Everything in the middle column is built and tested with no caller. Everything in the left column is live code that still does the job by hand. The numbers are the order in section 8 — take them in that order, because step 1 is what proves the port survives a real renderer.

```mermaid
flowchart LR
  subgraph Live["Live renderers — do this work here"]
    IOR["InputOptionsRenderer.send()<br/>POST /api/letta-code-message"]
    ARR["AgentsRouterRenderer"]
    CDR["ChatDetailRenderer<br/>POST /api/test"]
    VCC["VoiceCommandChannel"]
  end
  subgraph Built["Built, tested, zero callers"]
    VS["VoiceSession<br/>the fence"]
    ICA{{"ConversationAgent<br/>port"}}
    LAA["LettaAgentAdapter"]
    SOP["SpokenOutputPolicy"]
    NEW["second adapter<br/>NOT YET WRITTEN"]
  end
  TTS["SpeechSynthesizer"]

  IOR -->|"1"| LAA
  IOR -->|"2"| VS
  IOR -->|"2"| SOP
  ARR -->|"4"| LAA
  CDR -->|"4"| NEW
  VCC -->|"5"| VS
  LAA -.satisfies.-> ICA
  NEW -.satisfies.-> ICA
  SOP --> TTS
  VS -.->|"accepts(gen)"| SOP
```

## What actually runs today

Solid boxes exist, are tested, and run. The dashed box is the gap as the live UI still has it: VoiceSession and the conversation port now exist in js/, but no renderer holds one, so renderers still reach the agent directly.

```mermaid
flowchart TB
  subgraph Browser
    Mic([Microphone])
    CL["ContinuousListener<br/>(State)"]
    VR["VoiceRecorder<br/>(State)"]
    TB[TranscriptBuffer]
    VCC["VoiceCommandChannel<br/>the only coordinator"]
    ND["NoteDocument"]
    SS["SpeechSynthesizer"]
  end
  subgraph Server["dashboard/ Python"]
    TR["TranscriptionStrategy<br/>whisper.cpp"]
    CU[CleanupStrategy]
    RS[RouteStrategy]
    RI[ReceptionistIntentStrategy]
    CC[CommandCompletenessStrategy]
    NI[NoteCommandInterpreter]
    NR[NoteRepository]
    TTS["SpeechSynthesisStrategy<br/>edge-tts"]
    LC["LettaClient<br/>(Adapter)"]
  end
  Letta[(Letta agents)]

  Mic --> CL
  Mic --> VR
  VR -->|"POST /api/voice"| TR
  TR --> CU
  CU --> LC
  CL --> TB
  TB --> VCC
  VCC -->|"POST /api/note-command-complete"| CC
  VCC -->|"POST /api/note-command-apply"| NI
  NI --> NR
  VCC --> ND
  CL --> RS
  CL --> RI
  CC --> LC
  NI --> LC
  RS --> LC
  RI --> LC
  LC --> Letta
  TTS --> SS

  MISSING["MISSING: VoiceSession +<br/>IConversationAgent<br/>renderers POST Letta directly"]
  ND -.-> MISSING
  MISSING -.-> Letta

  style MISSING stroke-dasharray: 6 4,stroke:#9b2c39,color:#9b2c39
```

## Plan vs. reality

The plan's objects on the left, what stands in for each today on the right. Four of the plan's six headline objects have no code at all.

```mermaid
flowchart LR
  subgraph Planned["Plan (talking_agent_parts) — 0 lines written"]
    VS[VoiceSession]
    CO[ConversationCoordinator]
    ICA[IConversationAgent]
    LAA[LettaAgentAdapter]
  end
  subgraph Shipped["Shipped (dashboard/)"]
    LS["ListenerState + RecorderState<br/>no session object"]
    VCC["VoiceCommandChannel<br/>note commands only"]
    STRAT["4 narrow Letta strategies<br/>no single port"]
    LC["LettaClient<br/>3 methods, no streaming"]
  end
  VS -.->|"stands in"| LS
  CO -.->|"partially"| VCC
  ICA -.->|"scattered across"| STRAT
  LAA -.->|"partially"| LC
```

## One turn, end to end (note dictation + a spoken command)

The completeness check is the reason a pause mid-sentence does not fire a half-command. Note that no object here owns the turn — that is the missing VoiceSession.

```mermaid
sequenceDiagram
  actor EG
  participant CL as ContinuousListener
  participant TB as TranscriptBuffer
  participant VCC as VoiceCommandChannel
  participant CC as CommandCompletenessStrategy
  participant NI as NoteCommandInterpreter
  participant ND as NoteDocument

  EG->>CL: "Put a"
  CL->>TB: accept(text, final)
  TB->>VCC: committed = "Put a"
  VCC->>CC: assess("Put a")
  CC-->>VCC: complete=false, "trails off"
  Note over VCC,ND: four second pause — nothing happens,<br/>because no timer drives this
  EG->>CL: "period at the end"
  CL->>TB: accept(text, final)
  TB->>VCC: committed = "Put a period at the end"
  VCC->>CC: assess(...)
  CC-->>VCC: complete=true
  VCC->>NI: apply(note, command)
  NI-->>VCC: kind=edit, revised note
  VCC->>ND: setText(revised)
```

## Tests

- `dashboard/tests/ (voice + router)` — 68 tests
  Transcription arg-building, cleanup fallback, synthesis caching, routing fail-closed behaviour, receptionist intent parsing, and the whole note-command channel.
- `dashboard/js/tests/ (voice-related)` — 111 tests
  Both capture state machines, speech synthesis selection, transcript merging, the command channel's completeness gating, and the note surfaces.

### Not covered

- Nothing tests two speech paths running at once — the note listener and the command listener both hold a browser SpeechRecognition instance.
- No test covers a Letta call that hangs rather than fails; every failure test raises immediately.
- No end-to-end test drives audio in and asserts speech out.

### Tests to write

- A renderer-level test that a reply arriving after an interrupt is never spoken — the same assertion as the VoiceSession unit test, one layer up, and the proof that adoption worked.
- A latency measurement harness: end-of-speech → transcript, and transcript → first audio.

## Development status

### Done

- Capture works on both paths: push-to-talk (MediaRecorder → whisper.cpp) and continuous (browser SpeechRecognition), each behind its own State-machine port.
- Speech-to-agent routing works and fails closed — an ambiguous transcript never guesses an agent.
- The note + command channel is complete end-to-end, including LLM-judged command completeness and agent-chosen save filenames.
- Speech synthesis works server-side (edge-tts) with a per-agent voice catalog.
- Every port has an ABC/base class, at least one implementation, and unit tests with injected collaborators.
- The shared worker agent (transcript-cleanup-agent) runs on chatgpt-plus-pro/gpt-5.6-luna as of 2026-08-13, replacing the dead lc-gemini handle that had been silently failing every voice-cleanup, receptionist and note-command call. Verified live end to end.
- The plan's browser-side core is now built and tested: VoiceSession (generation fencing), the ConversationAgent port with a Letta adapter and a first-class fake sharing one contract suite, and SpokenOutputPolicy as the single gate in front of the speaker — 57 tests across five files. None of it has a caller yet; that is the next step, not a missing piece of the design.
- This guide is the shipped documentation: 14 tabs served at Project Plans → Voice Communication, navigated from the dashboard's own sub-nav, with the interface list driven by the same specs that render each page so the tabs cannot drift from the content.

### Gaps

- The session object exists but nothing constructs one, so in the live UI conversation state is still split across ListenerState, RecorderState and per-render closures.
- Generation fencing exists and is tested, and protects nothing yet: no renderer holds a session, so a late reply from a superseded turn is still spoken. Barge-in needs that adoption plus one interrupt() call from ContinuousListener.
- The IConversationAgent port exists with two adapters, but renderers still call POST /api/letta-code-message directly, so nothing in production benefits from it yet.
- Cancellation is delivery-side only — the endpoint has no server-side cancel, so a cancelled Letta call still runs to completion.
- The Letta adapter is request/response only — no streaming, so replies arrive in one lump after up to 900 seconds.
- Every finalized speech fragment costs one 3-6s LLM round-trip to the completeness detector. That, not model choice, is the dominant latency in the loop — see the Note Command Channel tab.

## Dependencies

### Depends on

- Letta HTTP API (via voice/letta_client.py — the one transport adapter)
- whisper.cpp + ffmpeg binaries (borrowed from lettabot)
- edge-tts (server-side speech synthesis)
- Browser SpeechRecognition + MediaRecorder APIs

### Used by

- Dashboard home screen — Toyota's note + command channel
- Dashboard Agents home — speech routing to a named agent
- Every agent's Input Options page — push-to-talk and spoken replies

The layering mostly holds: policy talks to ports, and concrete technology sits behind adapters. It leaks in exactly three renderer methods, and those three are the entire adoption backlog — InputOptionsRenderer.send() and ChatDetailRenderer's send path in js/implementation/detail-renderers.js, plus AgentsRouterRenderer. Each POSTs an agent endpoint itself and then decides on its own what is safe to speak. See section 8 for the order to take them in.

## Gotchas

### The objects are built. Nothing calls them. Both halves are true.

The natural mistake on arriving here is to read the green status pills and assume the voice path is fenced. It is not. VoiceSession, the ConversationAgent port and SpokenOutputPolicy are green because they exist, are correct, and are tested — not because they run. Every one of them has zero callers in the live dashboard. If you are debugging a stale answer being spoken over a new one, you are looking at renderer code that has never heard of any of this.

### There are three send paths, not one, and they do not share a reply shape.

InputOptionsRenderer and AgentsRouterRenderer talk to /api/letta-code-message, which returns { ok, reply } — one lump. ChatDetailRenderer talks to /api/test, which returns { replies: [{type, text}] } — an array with kinds. LettaAgentAdapter covers only the first shape. Pointing ChatDetailRenderer at it will look like it works and will silently drop every reply, because the parse fails closed. Write the second adapter.

### Cancellation here means 'do not deliver', not 'stop working'.

/api/letta-code-message has no server-side cancel. LettaAgentAdapter.cancel() suppresses the result and nothing more — the Letta run continues to completion and still costs its tokens and its 900 seconds. That is a real guarantee and a useful one, but do not build a UI that promises the user it stopped the agent.

### Adoption moves state; it does not just add a constructor call.

Conversation state today lives in closures inside render() functions — the conversation id, the in-flight flag, what has been spoken. A session that is constructed alongside that state instead of taking ownership of it gives you two sources of truth and a fence that disagrees with the UI. When you adopt VoiceSession in a renderer, delete the closure state it replaces in the same edit.

### A renderer is rebuilt on every open; the listener is not.

js/boot/agent-detail-renderers.js constructs a fresh renderer each time a tab is opened, but routerListener is deliberately long-lived so listening survives the hand-off between pages. Decide consciously which side of that line the session sits on. A session rebuilt per render cannot fence a reply that outlives the tab — which is exactly the case worth fencing.

## Next work

- READ FIRST — the objects below all exist and are tested; none of them has a caller. Do the steps in order. Each one names the file to edit and the assertion that means you are done. Do not start step 2 before step 1 is live, because step 1 is the one that proves the port survives contact with a real renderer.
- 1 · Adopt the port in InputOptionsRenderer.send() (js/implementation/detail-renderers.js — search for the 930000 literal, it is the only one). Construct a LettaAgentAdapter in js/boot/agent-detail-renderers.js and inject it, then delete send()'s inline fetch, its 930000 timeout literal, and its msi-conv-<id> localStorage bookkeeping — the adapter already owns all three. DONE WHEN: js/tests/detail-renderers.test.js still passes with its HTTP stub replaced by a FakeConversationAgent, and the Input Options tab still answers in a browser.
- 2 · Give that renderer a VoiceSession and gate its speech. Construct the session in the same boot module, call beginTurn() before submit and completeTurn() after, and replace `this._speech.speak(composeSpokenText(replies), ...)` with a SpokenOutputPolicy verdict. DONE WHEN: a renderer test asserts that a reply arriving after session.interrupt() is never handed to the synthesizer — the VoiceSession unit test's headline case, one layer up.
- 3 · Wire barge-in. ContinuousListener already reports speech start; have it call session.interrupt() when speech begins while the session is SPEAKING. This is the payoff the whole first slice was for, and it is unreachable until steps 1 and 2 land. DONE WHEN: talking over an answer stops it, live.
- 4 · Repeat for AgentsRouterRenderer, then ChatDetailRenderer. ChatDetailRenderer needs a SECOND adapter, not the same one — it POSTs /api/test and gets back { replies: [{type, text}] }, a different shape. Writing that adapter is the real test of the port: map each reply's `type` onto an AgentEvent kind and let SPEAKABLE_KINDS decide what is spoken, instead of composeSpokenText's regex. DONE WHEN: composeSpokenText has no callers and is deleted.
- 5 · Give VoiceCommandChannel a session and generation ids (js/abstract/voice-command-channel.js). It already serialises work, so it needs no new concurrency — it needs the fence, so a superseded command's result can be discarded rather than merely not started. See the ConversationCoordinator tab.
- 6 · Only then consider ConversationCoordinator, the last unbuilt core object. It is the one place where waiting was right: with steps 1-5 done, its job is visible in real code rather than guessed at from the plan.
- Independent of all of the above — cut the completeness round-trip. A cheap local pre-filter that skips the LLM for obviously-incomplete fragments would remove most of the 3-6s wait per spoken pause. This is the single biggest user-visible win on this page and it touches none of the work above, so it can run in parallel.
- Also independent, and overdue — decide explicitly whether the Pipecat rebuild is still the direction or whether the shipped dashboard stack is now the system. The first slice was built in dashboard/js/, not in talking_agent_parts/, which is a de facto answer nobody has written down. Right now the plan and the code disagree, and that ambiguity is itself a risk.

## Links

- [Original plan document (v1, verbatim)](/voice_communication_plan_v1.html)
