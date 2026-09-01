# mazda

Planning-stage package for the reusable, typed application objects behind the
Mazda document-verification agent. See `MIGRATION_PLAN.md` for the ownership
boundary, target shape, and migration order this directory follows.

Nothing here is a live implementation yet. Each subpackage is a stub that
names the interface it will eventually satisfy, mirroring the architecture
skeleton in `spa_documentation/mazda/`.

- `interfaces/` — public `typing.Protocol` contracts.
- `models/` — Pydantic boundary models shared across contracts.
- `finance_orchestrator/`, `intake_dispatch/`, `intake_evidence/`,
  `trainer_escalation/`, `agent_engine/`, `agent_engine_variant/`,
  `*_factory/` — one planned block per directory.
- `adapters/` — concrete integrations, added only once a block migrates.
- `composition/` — composition root wiring interfaces to adapters.
