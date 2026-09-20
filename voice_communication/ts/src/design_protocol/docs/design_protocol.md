---
id: design-protocol
name: Design Protocol
group: Working agreement
status: finished
---

# Design Protocol

> The SOLID Change Protocol and Gang of Four First rules this project works under.

**Finished** — Carried forward unchanged from the original plan. This is the standing agreement.

## Responsibility

- Record how change is made in this project, so the rules survive independently of whoever is working. Every defect, feature request, and refactor in the voice system follows this protocol.
- New leaf rule: every bug and alteration begins with a SOLID audit, and the first design catalog consulted for a remedy is the Gang of Four book. A pattern is selected only when it explains the forces and improves the object boundaries — pattern names are not permission to add ceremony.
- Decision and boundaries: Pipecat, if adopted, owns frame flow, transports, turn detection, interruption propagation and STT/TTS plumbing. Our objects own application policy — which agent receives a turn, what may be spoken, how one active run is enforced, how stale output is rejected, and what a session means.

## Contract

The original plan document is preserved verbatim at /voice_communication_plan_v1.html — this tab is a summary, not a replacement.

```text
SOLID Change Protocol   (required for every change)

  1. Reproduce first        narrowest failing/characterization test; record RED
  2. Audit all five         name the violated boundary and cite the evidence
  3. Open the GoF catalog   record the selected pattern, or why none is needed
  4. Change the smallest    prefer a port/injection/split over another conditional
       object boundary
  5. Run shared contract    every adapter for a port passes the same suite;
       tests                fakes are first-class adapters
  6. Refactor after GREEN   remove obsolete branches and duplicated policy

Principle              Question asked before editing
  Single Responsibility  How many independent reasons can this object change?
  Open/Closed            Can the behavior be added as a new implementation?
  Liskov Substitution    Can every implementation honor preconditions, errors,
                         cancellation and ordering?
  Interface Segregation  Is a client forced to depend on methods it does not use?
  Dependency Inversion   Does policy import Pipecat, Whisper, OpenAI,
                         sounddevice, pyttsx3 or Letta directly?

Gang of Four First      Use in this system
  Strategy               turn policy, STT/TTS choice, spoken-output policy
  Adapter                LettaAgentAdapter, Pipecat frame bridges
  State                  VoiceSession lifecycle
  Factory Method         PipelineFactory
  Chain of Responsibility SpokenOutputPolicy gates
  Observer               session events → transcripts, metrics, dashboard
  Command                confirmed external actions, cancellable turns
  Decorator              metrics/tracing/retries around a port
  Facade                 VoiceCommunicationApplication

Rejected patterns: no global Singleton service locator, no general-purpose
event bus before a second real consumer exists, no Abstract Factory until
compatible product families genuinely exist, no custom frame hierarchy that
merely duplicates Pipecat frames.
```

## Implementations

- **Pattern decision card** (current) — `voice_communication_plan_v1.html`
  Change/defect, failing test, SOLID violation, forces, candidates, selected pattern, port changed, contract tests, observed GREEN, obsolete code removed.
- **gof_debug_tacticts.md** (current) — `~/tactical_debug_toolbox/`
  The wider repo-level doctrine this protocol is consistent with.

## The change protocol as a loop

Step 3 is the one most often skipped; recording 'no pattern warranted' is a valid outcome.

```mermaid
stateDiagram-v2
  [*] --> Reproduce
  Reproduce --> Audit: RED recorded
  Audit --> Catalog: violated boundary named
  Catalog --> Change: pattern selected or "none"
  Change --> Contract: smallest boundary moved
  Contract --> Refactor: all adapters pass
  Refactor --> [*]: obsolete branches deleted
  Catalog --> Change: no pattern warranted
```

## Tests

- `dashboard/tests/test_project_plans.py` — 7 tests
  That this workspace exists, is reachable from the Project Plans tab, and still documents the SOLID protocol and the planned core objects.

### Not covered

- The dependency rule itself — nothing fails if a policy module imports Letta tomorrow.

### Tests to write

- An import-linter style test asserting that voice/ policy modules never import urllib or letta_client directly.
- One shared contract suite, starting with the five Letta strategies' fail-closed behaviour.

## Development status

### Done

- The protocol is written down and has been followed for the shipped ports — every one has an ABC, injected collaborators, and offline tests.
- The dependency rule holds in the shipped Python: no strategy imports urllib, and no policy object imports Letta.

### Gaps

- The plan requires an automated test enforcing the dependency rule. No such test exists.
- Shared contract suites per port do not exist — each implementation is tested separately, so Liskov is asserted by convention rather than proven.
- Pattern decision cards are not being filled in; the record of why each pattern was chosen lives only in code comments.

## Dependencies

### Depends on

- Nothing — it is the working agreement itself

### Used by

- Every tab in this workspace

Dependency rule from the plan: domain imports no Pipecat, Letta, OpenAI, Faster-Whisper, sounddevice, pyttsx3, filesystem, network or dashboard modules. Application imports only domain values and ports. Concrete construction is restricted to adapters and composition roots.

## Gotchas

### Two drivers, one async render (2026-08-13)

This workspace can be driven from two places at once: the dashboard's Voice Communication sub-nav calls InterfaceWorkspace.show() AND sets the iframe's hash, which fires the workspace's own hashchange listener into show() a second time. render() is async — it awaits Mermaid — so the two calls interleaved: the second cleared the container mid-flight and both then appended, producing duplicated numbered sections and an uncaught "matrix is not invertible" from pan/zoom attaching to detached SVGs. Fix: show() is now a no-op when the requested spec is already current. Two regression tests in interface-workspace.test.js fail without that guard. Neither side's tests caught it alone — it only exists at the seam where the two navigations meet.

### The plan HTML is a real file now, not a symlink

voice_communication_plan.html and _v1.html used to be symlinks into ~/talking_agent_parts/, which meant they never travelled with a git push and a handoff to another machine silently got the old document. They are versioned files in dashboard/ as of 2026-08-13. Do not reintroduce the symlink.

### Mermaid traps on Project Plans pages

`Note` is a reserved word in sequenceDiagram — a participant named Note fails to parse. A single failed diagram injects a GLOBAL error element into document.body that survives tab switches, so one broken diagram makes every later tab look broken; validate sources with mermaid.parse() rather than eyeballing. Never top-level-await visibility in the boot module: the tab's iframe starts hidden, so its load event would never fire. And svg-pan-zoom measures at construction, before layout settles — the deferred requestAnimationFrame re-fit is why diagrams are centred.

## Next work

- Write the dependency-rule test the plan asks for.
- Build one shared contract suite over the five Letta strategies.
- Start filling in pattern decision cards for changes to the voice system.
- Commit WIP before it collides. The 2026-08-13 merge cost real reconciliation work purely because two agents built the same feature in the same file with neither side committing — see Gotchas above.

## Links

- [Original plan document (v1, verbatim)](/voice_communication_plan_v1.html)
