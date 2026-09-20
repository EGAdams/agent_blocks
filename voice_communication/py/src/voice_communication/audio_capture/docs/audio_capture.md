---
id: audio-capture
name: Audio Capture
group: Shipped ports
status: finished
---

# Audio Capture

> Two State-machine ports: push-to-talk (VoiceRecorder) and continuous listening (ContinuousListener).

**Finished** — Both ports complete, both implemented, 28 tests across the four files.

## Responsibility

- Turn microphone input into text, and own the lifecycle of doing so. Two genuinely different jobs, so two ports rather than one: VoiceRecorder records a clip and transcribes it once, while ContinuousListener stays open and streams recognized text until stopped.
- Both are State-pattern base classes with Template-Method start/stop: the base owns the legal transitions and the concrete implements only the primitive operations. That is what makes 'start is a no-op when already listening' testable with no browser present.
- Deliberately provider-agnostic. ContinuousListener's only abstract primitives are openListening/closeListening, so a future wake-word listener (openWakeWord was evaluated and deferred) could replace the browser recognizer without a single caller changing.

## Contract

```js
ContinuousListener   js/abstract/continuous-listener.interface.js

  state / isListening
  start() stop() toggle()          Template Method over the primitives
  setCallbacks({onStateChange, onResult, onError})
  openListening()  -> Promise<bool>   abstract
  closeListening() -> void            abstract
  onResult(text, isFinal)             isFinal=false for live/interim

VoiceRecorder   js/abstract/voice-recorder.interface.js

  state / isRecording / lastError
  start() stop() toggle()
  openStream() beginCapture() endCapture() transcribe(blob)   abstract

  RecorderState  = idle | recording | processing
  ListenerState  = idle | listening
```

## Implementations

- **BrowserSpeechRecognitionListener** (current) — `js/implementation/browser-speech-recognition-listener.js`
  Chrome-family SpeechRecognition. Auto-restarts on the native onend so 'continuous' really is.
- **MediaRecorderVoiceRecorder** (current) — `js/implementation/media-recorder-voice-recorder.js`
  MediaRecorder → POST /api/voice → whisper.cpp.
- **Wake-word listener** (planned) — `—`
  openWakeWord evaluated and deliberately deferred (real ML training work). The port is already shaped for it.
- **Pipecat transport** (planned) — `/home/adamsl/talking_agent_parts/`
  The plan would hand streaming audio to Pipecat instead. Not started.

## Two capture lifecycles

Left: push-to-talk transcribes once on stop. Right: continuous streams results and restarts itself through natural silences.

```mermaid
stateDiagram-v2
  direction LR
  state "VoiceRecorder" as VR {
    [*] --> idle
    idle --> recording: start()
    recording --> processing: stop()
    processing --> idle: transcribe resolves
    processing --> idle: transcribe throws
  }
  state "ContinuousListener" as CL {
    [*] --> idle2
    idle2 --> listening: start()
    listening --> listening: native onend, auto-restart
    listening --> idle2: stop()
    listening --> idle2: fatal error
  }
```

## The microphone contention gap

Three listeners exist and nothing arbitrates. The home screen now renders two of them on one page.

```mermaid
flowchart TB
  Mic([One microphone])
  RL["routerListener<br/>Agents home"]
  RCL["receptionistListener<br/>Toyota's note"]
  NCL["noteCommandListener<br/>command channel"]
  Mic --> RL
  Mic --> RCL
  Mic --> NCL
  ARB{{"MISSING:<br/>arbitration"}}
  RCL -.-> ARB
  NCL -.-> ARB
  style ARB stroke-dasharray: 6 4,stroke:#9b2c39,color:#9b2c39
```

## Tests

- `js/tests/continuous-listener.test.js` — 8 tests
  Template-Method transitions, no-op guards, interim/final delivery, and that setCallbacks rebinds without touching session state.
- `js/tests/browser-speech-recognition-listener.test.js` — 9 tests
  The session is continuous+interim, auto-restarts on onend, does NOT restart after an explicit stop, and distinguishes fatal errors from transient no-speech.
- `js/tests/voice-recorder.test.js` — 6 tests
  idle→recording→processing→idle, and that a throwing transcribe still returns the machine to idle.
- `js/tests/media-recorder-voice-recorder.test.js` — 5 tests
  The browser wiring: stream open failure is reported rather than thrown.
- `js/tests/transcript-merge.test.js` — 8 tests
  Word-level overlap trimming when the recognizer re-transcribes the previous utterance's tail.

### Not covered

- Two listeners active simultaneously — the most likely real-world failure now that the home screen has two boxes.
- A recognizer that stops emitting without firing onend or onerror.

### Tests to write

- A test asserting that starting a second listener while one is active is either prevented or explicitly reported.
- A microphone-arbitration decision, then a test for it.

## Development status

### Done

- Both state machines enforce their transitions: start when already started, or stop when idle, are no-ops rather than errors.
- The recognizer auto-restarts on the native silence-triggered onend, so continuous listening survives natural pauses.
- Fatal errors (permission denied, no microphone, network) force the state back to idle and report a human-readable reason; transient ones (no-speech) are ignored and the session keeps going.
- setCallbacks lets a long-lived listener be re-claimed by a freshly rendered view without losing the session — this is what keeps listening alive across dashboard navigation.
- mergeFinalChunk trims the tail the Android recognizer re-flushes on restart, which otherwise doubled words during long sessions.

### Gaps

- Three separate listeners now exist (router, receptionist, note-command) and nothing arbitrates between them. Two active at once means two browser recognizers competing for one microphone — unreliable in Chrome, and not covered by any test.
- No interruption/barge-in support: the listener cannot tell the rest of the system 'the user started talking over the reply'.
- Push-to-talk and continuous are wired separately in every renderer that offers both, rather than through a shared capture facade.

## Dependencies

### Depends on

- Browser SpeechRecognition / MediaRecorder — injected, never imported
- POST /api/voice for the push-to-talk transcription leg

### Used by

- NoteCommandPanelRenderer — the command channel's listener
- InputOptionsRenderer — Toyota's note dictation and per-agent push-to-talk
- AgentsRouterRenderer — continuous listening for agent-name routing

Every browser dependency is constructor-injected, which is why all 28 tests run in Bun with no DOM and no microphone.

## Next work

- Decide the arbitration rule: one active listener at a time, or explicitly allow two and verify it in a real browser.
- Add an onSpeechStart signal so barge-in becomes possible once VoiceSession exists.
- Consider a small CaptureFacade so renderers stop wiring push-to-talk and continuous separately.
