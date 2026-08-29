# Voice Communication

This directory is the source-side skeleton for the Voice Communication Agent
Blocks. It establishes types and dependency boundaries only; production
behavior will be added one active block at a time.

## Layout

The layout follows `lancedb_memory/agent_memory`: the public dependency
boundary is centralized, while every Agent Block owns a directory that can
grow independently.

```text
voice_communication/
  interfaces/                 public TypeScript and Python interface catalogs
  typescript_contracts/       browser/application boundary shapes
  pydantic_models/            cross-process runtime validation shapes
  voice_session/              one directory per individual Agent Block
  conversation_agent/
  ...
  pipecat_local_service/
  pipeline_factory/
  index.ts                    thin TypeScript package facade
  __init__.py                 thin Python package facade
```

An object's interface stays in its own directory. Its concrete adapter,
implementation, and focused tests will be added beside that interface only
when the block becomes active. Concrete dependencies are selected at a future
composition root, never inside the interface catalog.

## Type boundaries

- `interfaces/index.ts` is the public TypeScript interface catalog.
- `interfaces/__init__.py` is the public Python interface catalog.
- TypeScript interfaces describe Toyota/browser/application objects.
- Pydantic models validate every JSON shape crossing into or out of the
  separate local Pipecat service.
- Python Protocols define the Pipecat-side Strategy, Factory Method, and Facade
  ports.
- Concrete Letta, Pipecat, STT, TTS, transport, and logging adapters will depend
  on these ports rather than the reverse.

The TypeScript and Pydantic contracts intentionally share field names and event
values. A contract-parity test will be added when the Pipecat service block
becomes active.

The existing design reference remains:

- `/home/adamsl/letta-code/dashboard/voice_communication_plan.html`
- `/home/adamsl/letta-code/dashboard/js/plans/voice-communication/`

Pipecat runs as a separate local service. The dashboard remains Toyota's
composition boundary and communicates with that service through an adapter.

## Planned construction order

1. IConversationAgent and LettaAgentAdapter adoption
2. VoiceSession lifecycle
3. SpokenOutputPolicy and interruption
4. ConversationCoordinator
5. PipecatServiceClient, PipecatLocalService, and PipelineFactory
6. ToyotaVoiceApplication
7. VoiceHealthObserver and controlled rollout
