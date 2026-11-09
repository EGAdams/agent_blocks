"""DeploymentRunner: one request in, one OperationResult out -- and, whatever happens, one ledger line and
one run record (every step, by ADW id). An exception becomes a failed result naming it, never a traceback."""
from __future__ import annotations

import json
from pathlib import Path

from ..models import OPERATIONS, DeployRequest, LedgerEntry, OperationResult
from .catalog import OPERATION_TABLE
from .common import Deps, Refused, result


class DeploymentRunner:
    def __init__(self, deps: Deps, runs: Path) -> None:
        self._deps, self._runs = deps, runs

    def run(self, req: DeployRequest) -> OperationResult:
        deps = self._deps
        deps.trace.step("request", operation=req.operation, project=req.project, dry_run=req.dry_run,
                        task_id=req.task_id, client=req.client)
        op = OPERATION_TABLE.get(req.operation)
        try:
            if op is None:
                raise Refused(f"no operation {req.operation!r}; operations: {', '.join(OPERATIONS)}")
            project = None
            if op.needs_project:
                if req.project not in deps.catalog.names():
                    raise Refused(f"no project {req.project!r}; projects: {', '.join(deps.catalog.names())}")
                project = deps.catalog.get(req.project)
            answer = op.run(deps, req, project)
        except Refused as exc:
            answer = result(False, True, f"refused: {exc}", refused=True)
        except Exception as exc:
            deps.trace.step("error", ok=False, error=type(exc).__name__, message=str(exc)[:500])
            answer = result(False, False, f"{req.operation} failed: {type(exc).__name__}: {str(exc)[:300]}")
        answer.data["adw_id"] = deps.adw_id
        self._keep(req, answer)
        return answer

    def _keep(self, req: DeployRequest, answer: OperationResult) -> None:
        deps = self._deps
        entry = LedgerEntry(at=deps.clock.now(), adw_id=deps.adw_id, task_id=req.task_id, client=req.client,
                            project=req.project or "*", operation=req.operation, ok=answer.ok, verified=answer.verified,
                            line=answer.line[:500], release_id=answer.data.get("release_id"), dry_run=req.dry_run)
        deps.ledger.record(entry)
        self._runs.mkdir(parents=True, exist_ok=True)
        record = {"adw_id": deps.adw_id, "request": req.model_dump(), "result": answer.model_dump(),
                  "steps": [s.model_dump() for s in deps.trace.steps()]}
        (self._runs / f"{deps.adw_id}.json").write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")
