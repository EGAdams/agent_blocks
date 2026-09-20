---
id: spoken-output-policy
name: SpokenOutputPolicy
group: Planned core
status: working
---

# SpokenOutputPolicy

> The single gate between agent output and the speaker. Built and tested; not yet wired into a renderer.

**Working / Needs Testing** — js/abstract/spoken-output-policy.js, 10 tests. Nothing calls it yet — the renderers still decide speakability inline.

## Responsibility

- Answer one question in one place: should this agent event be read aloud? Two things decide it, and today each renderer answers both by hand. First, is the event kind speakable — reasoning, tool calls, tool results and status lines are not; only assistant_text is. Second, is the turn still wanted — a reply from a superseded generation is stale, and speaking it talks over the answer the user actually asked for.
- It holds no synthesizer and touches no DOM. The policy returns a verdict — {speak, reason, text} — and the caller speaks. That is what makes 'the March answer was discarded' an assertion in a test file rather than something you have to reproduce by talking to the machine.
- The rejection reason is part of the contract, not a debug string. RejectionReason.SUPERSEDED and RejectionReason.NOT_SPEAKABLE are different failures with different fixes, and a log that conflates them sends the next person to the wrong place.

## Contract

Order matters inside admit(): the fence is checked before the text, so an event that is both stale and blank is reported as stale. Reporting it as empty would send the reader looking for a formatting bug.

```js
SpokenOutputPolicy   js/abstract/spoken-output-policy.js   (shipped)

  admit(event)      -> { speak, reason, text }
  admitAll(events)  -> string[]        in order, unspeakable dropped
  static isTerminal(event) -> boolean

  RejectionReason
    NOT_AN_EVENT    not an AgentEvent at all
    NOT_SPEAKABLE   kind is never spoken
    SUPERSEDED      generation superseded — the fence rejected it
    EMPTY           nothing left after trimming

  injected collaborators:
    session    VoiceSession        supplies accepts(generationId)
    speakable  (kind) -> boolean   defaults to isSpeakable
```

## Implementations

- **SpokenOutputPolicy** (current) — `js/abstract/spoken-output-policy.js`
  The policy itself. No DOM, no synthesizer, no HTTP.
- **composeSpokenText** (deprecated) — `js/implementation/detail-renderers.js`
  What stands in today: a per-renderer helper that filters reply rows by type. Knows nothing about generations, so it cannot reject a stale reply.

## The gate

Two checks, one place. Everything that fails either one stops here instead of reaching the speaker.

```mermaid
flowchart TB
  EV["AgentEvent"] --> K{"speakable kind?"}
  K -->|no| R1["reject: NOT_SPEAKABLE"]
  K -->|yes| G{"session.accepts<br/>(generationId)?"}
  G -->|no| R2["reject: SUPERSEDED"]
  G -->|yes| T{"text after trim?"}
  T -->|no| R3["reject: EMPTY"]
  T -->|yes| S["speak(text)"]
```

## Tests

- `js/tests/spoken-output-policy.test.js` — 10 tests
  Current assistant text is spoken and trimmed; reasoning/tool_call/tool_result/status/terminal never are; the March-then-April case discards the superseded answer and speaks the current one; a stale blank event reports SUPERSEDED not EMPTY; junk is rejected; admitAll preserves order.

### Not covered

- Any real speech path — no renderer calls the policy, so nothing proves the live UI obeys it.

### Tests to write

- A renderer test asserting that a reply arriving after an interrupt is never handed to the synthesizer.

## Development status

### Done

- The policy exists, with both checks and a named reason for each rejection.
- admitAll() collapses a whole turn into the ordered list of things to say.
- Non-events (null, a bare string, a number) are rejected rather than thrown on — output arrives from a network, so the gate cannot assume it is well formed.

### Gaps

- No caller. Speech still goes out through composeSpokenText in detail-renderers.js.
- No barge-in: something has to call session.interrupt() when the user speaks over the agent, and nothing does yet.
- The verdict is not surfaced anywhere a user can see, so a discarded answer is currently invisible rather than explained.

## Dependencies

### Depends on

- VoiceSession — for accepts(generationId)
- AgentEventKind / isSpeakable — the speakable vocabulary

### Used by

- Nothing yet. InputOptionsRenderer and the agents-home router still decide inline.

SPEAKABLE_KINDS lives next to the event vocabulary in conversation-agent.interface.js rather than in here, so adding an event kind forces a decision about whether it can be spoken in the same edit.

## Next work

- Give InputOptionsRenderer a VoiceSession and route its speech through the policy, deleting composeSpokenText's type filtering.
- Call session.interrupt() from ContinuousListener when speech starts during SPEAKING — that is barge-in, and the policy is the half of it that already exists.
- Show the rejection: a one-line 'answer discarded — you asked something else' beats silence.
