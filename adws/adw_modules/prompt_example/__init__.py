"""Menu adapter for the unchanged adw_prompt.py imported from rol_finances.

Each invocation starts that actual ADW in a child process, so it owns a fresh
Run, its signal handlers, and its two phases. The file demo's Run stays open.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .. import agents
from ..data_types import ActionResult, ActionSpec, GenericOutput

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG = Path(__file__).with_name("config.yaml")
DEFAULT_PROMPT = "Explain ADW, phase, and run in three short sentences, using a cooking example."
PHASES = "request (engineer) -> prompt (agent: learner)"
SPEC = ActionSpec(
    action_id="prompt_adw", title="Run Prompt ADW (2 phases)", scope="adw",
    description="Record your question, ask one agent, and finish a fresh run whose two phases you can inspect.",
)


class PromptRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    prompt: str = Field(min_length=1)


class PromptExample:
    def __init__(self, config: Path = CONFIG) -> None:
        self.config = config.resolve()
        self.last_result: ActionResult | None = None

    def invoke(self, request: PromptRequest) -> ActionResult:
        clock = time.monotonic()
        adw_id = uuid4().hex[:8]
        cfg = agents.load_config(str(self.config))
        data_dir = Path(cfg.defaults.data_dir)
        if not data_dir.is_absolute():
            data_dir = REPO_ROOT / data_dir
        session_dir = data_dir / "sessions" / adw_id
        detail = ["ADW: adw_prompt.py", f"Phases: {PHASES}",
                  f"Run: {adw_id} (fresh for this selection)"]
        error = ""
        try:
            sys.stdout.flush()  # keep the preview ahead of the child's live output
            completed = subprocess.run(
                [sys.executable, str(REPO_ROOT / "adws" / "adw_prompt.py"),
                 "--agent", "learner", "--config", str(self.config),
                 "--adw-id", adw_id, "--", request.prompt],
                cwd=REPO_ROOT, stdin=subprocess.DEVNULL, check=False,
            )
            if completed.returncode:
                error = f"ADW exited with status {completed.returncode}; see the output above and its trace."
            else:
                envelope = GenericOutput.model_validate_json(
                    (session_dir / "learner" / "envelope.json").read_text())
                if envelope.status != "success":
                    error = envelope.summary or "Agent reported failure."
                detail.append(envelope.summary)
        except (OSError, ValueError) as exc:
            error = f"{type(exc).__name__}: {exc}"
        self.last_result = ActionResult(
            action_id=SPEC.action_id, outcome="fail" if error else "success",
            summary="Prompt ADW failed" if error else "Prompt ADW completed: 2 phases in one run",
            detail=detail, error=error, adw_id=adw_id,
            elapsed_ms=int((time.monotonic() - clock) * 1000),
            log_path=str(session_dir / "events.jsonl"),
        )
        return self.last_result
