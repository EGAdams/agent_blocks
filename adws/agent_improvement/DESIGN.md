# Agent improvement in ADWs — design

One bounded, observable self-improvement cycle of one target agent, as interface-based ADWs over the existing
RSI framework (`~/rol_finances/tools/self_improving_agent/agent_self_improvement`, reached only through `rsi.py`).
Sydney is the first target. Mazda keeps her finance evaluator and her existing RSI wiring; this engine does not
rename, replace or re-route her.

## Layers

```
Bridge (agent-improvement)  ->  lancedb_memory/agent_bridge_worker/improvement_agent.py
CLI / EG                     ->  adws/adw_improvement_cycle.py   (the 10 phases, one SSSF session)
                                 adws/adw_improvement_ops.py     (status/proposal/evaluation/report/lineage/health,
                                                                  decide/cancel/rollback; one SSSF phase each)
                             ->  agent_improvement/service.py    (the operations, callable without any ADW)
                             ->  agent_improvement/engine.py     (Cycle + phases, judged by the profile)
                             ->  rsi.py                          (the existing framework: experiments, gates,
                                                                  activation, rollback, revisions store)
```

`profiles.py` is the only file that names a target. The engine takes a `TargetProfile` (catalog, evaluator,
judge sources, evidence, change policy, promotion policy, environments, runner, budgets) and has no
conditional on an agent's name.

## Phases

| Phase | Responsibility | Input | Output | Failure / recovery |
| --- | --- | --- | --- | --- |
| resolve | snapshot the environment's prompt, register it as the baseline revision, fingerprint the judge | profile, environment | baseline revision, judge sha | refuses an unknown environment |
| evidence | read the reference material the improver may cite, plus the parent cycle's patch, gate reasons and training replies (never its held-out tasks) | profile.evidence, parent record | evidence notes | — |
| baseline | run every training task on the baseline, score it with the profile's judge | baseline | scores, deficiencies | stops `no_deficiency` when all pass; `failed` when only transport failed |
| propose | ask the improver for one patch | prompt section, deficiencies, evidence | proposal (find/replace, summary) | the improver is not the judge; it sees no hidden tests |
| prepare | apply the patch through the change policy, register the candidate revision | proposal | candidate revision, diff | a policy violation is recorded and blocks at gates |
| experiment | run baseline and candidate arms on training, regression and held-out tasks | both revisions | arm scores, tokens | budget limits stop the cycle `failed` |
| gates | patch policy, judge integrity, safety, cost, token growth, regression, held-out, usefulness | everything above | gate results, decision | any block → `rejected`; a person-only environment → `awaiting_approval` |
| activate | re-check the judge, activate through `rsi.ActivationService`, write, verify what the target loads | approved decision | activated revision, loaded sha | any error → automatic rollback |
| settle | for a rejection, confirm the baseline is untouched; for a rollback, confirm the restore | state | rollback evidence | — |
| report | write `report.md` | the record | report path | always rewritten |

A phase's `description` must explain what it does, not restate its name: SSSF's `PhaseParams` refuses one
that equals the name with spaces, and a test checks every phase.

Every phase is idempotent through `record.completed` and runs only in the states it allows, so a resumed
attempt skips finished phases and never repeats a worker run, a write, or an activation.

## Roles kept apart

- **Worker**: the target itself (`ProbeAgentRunner` → `receptionist_team.improvement_probe`), run by the experiment.
- **Trainer**: the task catalog (`sydney_tasks()`), training / regression / held-out roles.
- **Improver**: `ClaudeCliImprover` or `FileImprover`. It proposes; it never scores.
- **Judge**: `BridgeTaskEvaluator` plus the gate chain. Its sources are fingerprinted at resolve and re-checked
  before gates and before activation; a candidate that changed them is blocked.
- **Promotion**: `EnvironmentPromotionPolicy`. The pilot has a standing policy approver; live needs a named
  person (`EG`). The target's own Bridge identity may read its cycles but never start, decide or roll back one.

A candidate can only change text inside the profile's prompt section (Sydney: "THE STAR TREK BRIDGE" to
"LIMITS"), cannot add or remove placeholders or ROUTE lines, and cannot use the forbidden phrases. It has no
path to the judge, the hidden task expectations, the thresholds, the promotion policy, the audit history or the
budgets: none of those are prompt text.

## Approvals and resume

A live cycle stops at gates with `awaiting_approval` only after the diff, experiments and gate evidence are
recorded. `adw_improvement_ops.py decide --cycle-id … --approve|--reject --approver EG --key <unique>` records
one decision (a repeat with the same key is a no-op, a conflicting one is refused). Re-running
`adw_improvement_cycle.py --cycle-id …` resumes the same cycle at attempt + 1. A failed attempt is appended to `record.attempt_errors`; it overwrites
`record.reason` only while the cycle is still running, so a gate's decision is never replaced by a later crash. `health` reports
`waiting_for_person` for such a cycle, not `stalled`.

## Feature switch and cutover

- `--environment pilot` (default) writes only the staged tree (`~/staging/rsi_pilot/agent_blocks`).
- `--environment live` refuses unless `AGENT_IMPROVEMENT_LIVE=1`, and even then needs EG's decision; its
  activation writes Sydney's real prompt and restarts `sydney-receptionist.service`.
- The Bridge door: gateway agent `agent-improvement` (protected) in the host's `bridge.php`, and the worker
  registers `ImprovementAgent` only while `~/.config/agent_improvement/bridge_enabled` exists on SQMC.
  `chatgpt-bridge` is EG's own connector, so its decide and rollback are recorded as `EG` (`PEOPLE` in
  `improvement_agent.py`); any other sender is recorded under its own name and is not an approver.

Turning the switch off is the rollback of the cutover; nothing else changes.

## Observability

Each run is one SSSF session, so the ADW debugger shows every phase, attempt and fault. Events pass through
`observe.RedactingEvents`: millisecond UTC timestamps, the correlation ids (cycle, target, environment,
Bridge task, request key) and credential masking. Faults (`--fail-at`, `--stall-at`, `--kill-at`,
`--simulate activation-verify-mismatch`) are test-only and recorded on the cycle as simulated.

## Data

`<data>/<target>/`: `cycles/<id>/record.json`, `report.md`, `cancel.requested`, `slots/<id>.pid`
(concurrency), `requests.json` (idempotency keys) and `improvement.sqlite3` (the framework's revision,
experiment and activation tables). Pilot data defaults to `~/staging/rsi_pilot/data`, live to
`~/.local/share/agent_improvement`. `~/.mazda/self_improvement.sqlite3` is never opened.

## Mazda

`mazda_profile()` builds her judge from the framework's own `FinanceEvaluationFactory`. Her worker and system
message live in her Letta agent on another machine and are not reachable from here, so her environments are
`NotWiredEnvironment`, which refuses rather than pretends. Wiring her is a runner plus an environment in her
profile; the engine does not change.

## Pilot results (SQMC, 2026-11-16 UTC)

| Gen | Cycle | ADW | Candidate | Decision |
| --- | --- | --- | --- | --- |
| 1 | sydney-20261116T021721Z-bbe4a9 | 8f5670ee, resumed bef5edb1 | w-4b0750039fc6 | rejected: regression (unit sent without `.service`) |
| 2 | sydney-20261116T022827Z-149b5f | d7d279d6 | w-0315f352386b | rejected: held_out (variation sent bare JSON, no `machine-ops@machine_ops:` prefix) |
| 3 | sydney-20261116T032717Z-c1da44 | 0fe89d9d, settled dc456429 | w-fa166077f5b7 | every gate allowed but usefulness (`needs_human`: no measurable improvement); rejected by EG's delegate, because approving a change that scored no better would manufacture an improvement |

Live, gen 1: sydney-20261116T033427Z-74e4d0 (`AGENT_IMPROVEMENT_LIVE=1`), candidate w-97f13d8083d6, fixed
`bridge_target` and `payload_strict_json` on training and held-out but newly failed `result_succeeded` on both,
so it was rejected by the regression gate before any approval was asked for. Sydney's live prompt and service were not touched.

All four left the baseline w-c57f4465b82a active. The pilot lineage is at its generation limit (3). No activation or
rollback has happened for real yet; those paths are covered by the offline tests only. Through the Bridge, a toyota-bridge
`lineage` read was verified end to end on 2026-11-15; no `start` or `decide` has been sent over it yet. Cycle 1's `reason` still holds the phase-9 crash text from
before `attempt_errors` existed; the record was left as written.

## Known limits

- Tokens are an estimate (`prompt_chars/4`); the probe has no provider usage figure.
- Sydney's `star_trek_bridge` route is read-only, so her tasks are read-only Machine Ops requests.

## Tests

    cd adws && uv run --quiet --with pydantic --with pytest pytest -q agent_improvement/tests
    cd lancedb_memory && .venv/bin/python -m agent_bridge_worker.tests.test_improvement_agent
