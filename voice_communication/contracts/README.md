# Contracts

One boundary, expressed twice. `ts/src/typescript_contracts/contracts.ts` and
`py/src/voice_communication/pydantic_models/models.py` describe the same JSON
wire format for two different processes, so neither is the source of truth —
their agreement is.

`test_contract_parity.py` is the enforcement, and it is the reason the two
package roots share one repository: a change to either catalog is a defect
until the other changes in the same commit, and only a test with both trees in
one checkout can say so.

It asserts three things:

- the two catalogs declare the same shape names,
- each shape declares the same field names, comparing TypeScript's camelCase
  against the pydantic *serialization alias* (`latency_ms` → `latencyMs`, via
  `VoiceModel`'s `alias_generator=to_camel` and `serialize_by_alias=True`),
- `AgentEventKind` and `VoiceHealthStatus` carry the same string values.

Field *types* are deliberately not compared. `SessionId = string` against
`UUID`, and `PipelineHandle.pipelineId: string` against `UUID`, are the
JSON-over-the-wire representation versus the validated runtime type. Those
pairs are listed in `DOCUMENTED_TYPE_DIVERGENCES`, which a fourth check keeps
honest — a stale entry naming a field that no longer exists is itself drift.

## Running

```bash
cd voice_communication
../lancedb_memory/.venv/bin/python contracts/test_contract_parity.py
```

That is the supported invocation today: the borrowed venv has `pydantic` but
not `pytest`. The checks are also written as plain `test_*` functions, so
`python -m pytest contracts/` works wherever `pytest` is installed — the
standalone `main()` exists so parity is checkable without one.

Either way it needs `pydantic` but no network, no API key, and no installed
TypeScript — the TS side is read as text.
