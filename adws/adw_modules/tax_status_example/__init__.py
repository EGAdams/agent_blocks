"""Run the finance tax-status ADW with playground-owned traces.

The workflow and its finance adapters stay in rol_finances. A subprocess keeps
that repo's adw_modules separate from the playground's package of the same name.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import yaml
from pydantic import BaseModel, Field

from ..data_types import ActionResult, ActionSpec, SSSFConfig

PLAYGROUND = Path(__file__).resolve().parents[3]
PHASE_NAMES = (
    "read_tracked_questions", "count_category_work", "check_question_progress",
    "check_missing_amounts", "publish_progress_report",
)
PHASES = " -> ".join(PHASE_NAMES)
SPEC = ActionSpec(
    action_id="tax_status_adw", title="Run Tax Status ADW (5 code phases)", scope="adw",
    description="Read current finance records, report remaining tax-preparation work, and save a report in a fresh playground run.",
)


class PhaseSummary(BaseModel):
    seq: int
    name: str
    kind: str
    status: str


class TaxStatusResult(ActionResult):
    phases: list[PhaseSummary] = Field(default_factory=list)
    report_path: str = ""
    report_url: str = ""


class TaxStatusExample:
    def __init__(self, finance_root: Path | None = None,
                 playground: Path = PLAYGROUND) -> None:
        self.finance_root = (finance_root or Path(os.environ.get(
            "ROL_FINANCE_ROOT", str(PLAYGROUND.parent / "rol_finances")))).expanduser().resolve()
        self.playground = playground.resolve()
        self.last_result: TaxStatusResult | None = None

    def invoke(self) -> TaxStatusResult:
        clock = time.monotonic()
        adw_id = uuid4().hex[:8]
        cfg = SSSFConfig()
        cfg.defaults.data_dir = str(self.playground / "adws/adw_data")
        cfg.observability.db = str(self.playground / "adws/adw_data/sssf.db")
        session_dir = Path(cfg.defaults.data_dir) / "sessions" / adw_id
        workflow = self.finance_root / "adws/adw_tax_status.py"
        phases: list[PhaseSummary] = []
        detail = [f"ADW: {workflow}", f"Run: {adw_id} (fresh for this selection)"]
        report_path = ""
        report_url = ""
        error = ""
        try:
            if not workflow.is_file():
                raise FileNotFoundError(f"Finance workflow not found: {workflow}. Set ROL_FINANCE_ROOT to the finance checkout.")
            uv = shutil.which("uv")
            if uv is None:
                raise FileNotFoundError("uv is required to load the finance workflow's dependencies.")
            session_dir.mkdir(parents=True, exist_ok=False)
            config_path = session_dir / "playground-config.yaml"
            config_path.write_text(yaml.safe_dump(cfg.model_dump()), encoding="utf-8")
            sys.stdout.flush()
            completed = subprocess.run(
                [uv, "run", str(workflow), "--config", str(config_path),
                 "--finance-root", str(self.finance_root), "--adw-id", adw_id],
                cwd=self.finance_root, stdin=subprocess.DEVNULL, check=False,
                timeout=180,
            )
            db = Path(cfg.observability.db)
            if db.is_file():
                # Read-only: a failed child must never create a substitute trace DB.
                with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as connection:
                    rows = connection.execute(
                        "SELECT seq, name, kind, status FROM phases WHERE adw_id=? ORDER BY seq",
                        (adw_id,),
                    ).fetchall()
                phases = [PhaseSummary(seq=seq, name=name, kind=kind, status=status)
                          for seq, name, kind, status in rows]
            detail.extend(f"{phase.seq}. {phase.name} [{phase.kind}] — {phase.status}"
                          for phase in phases)
            report = session_dir / "remaining-work.html"
            if report.is_file():
                report_path = str(report)
                detail.append(f"Report: {report}")
                viewer = os.environ.get("PLAYGROUND_VIEWER_URL", "http://localhost:"
                    + os.environ.get("PLAYGROUND_VIEWER_PORT", "4613")).rstrip("/")
                report_url = f"{viewer}/api/sessions/{adw_id}/reports/remaining-work.html"
                detail.append(f"Open report: {report_url}")
            if completed.returncode:
                error = f"Tax-status exited with status {completed.returncode}. Any report is incomplete; read the source errors above."
            elif ([phase.name for phase in phases] != list(PHASE_NAMES)
                  or any(phase.kind != "code" or phase.status != "success" for phase in phases)
                  or not report_path):
                error = "The workflow did not produce five successful code phases and its HTML report."
        except (OSError, ValueError, sqlite3.Error, subprocess.TimeoutExpired) as exc:
            error = f"{type(exc).__name__}: {exc}"
        self.last_result = TaxStatusResult(
            action_id=SPEC.action_id, outcome="fail" if error else "success",
            summary="Tax Status ADW incomplete" if error else "Tax Status ADW completed: 5 code phases in one run",
            detail=detail, error=error, adw_id=adw_id,
            elapsed_ms=int((time.monotonic() - clock) * 1000),
            log_path=str(session_dir / "events.jsonl"), phases=phases,
            report_path=report_path, report_url=report_url,
        )
        return self.last_result
