"""Trusted command catalog. Requests select names, never commands or paths."""
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[3]
MEMORY = ROOT / "lancedb_memory"
UNITS = frozenset({"dashboard-server.service", "adw-supervisor.service",
                   "agent-bridge-worker.service", "agent-blocks-server.service",
                   "toyota-receptionist.service"})
PROPERTIES = ("Id", "LoadState", "ActiveState", "SubState", "MainPID", "ControlGroup",
              "Result", "NRestarts", "ExecMainStatus", "ActiveEnterTimestamp")
TESTS = {
    "post-office-operations": (str(MEMORY / ".venv/bin/python"), "-B", "-m", "agent_comms.tests.test_operations"),
    "object-debugger": (str(MEMORY / ".venv/bin/python"), "-B", "-m", "agent_comms.tests.test_object_debugger"),
}
HEALTH = {
    "dashboard-server.service": "http://127.0.0.1:8765/api/worker-activity",
    "agent-blocks-server.service": "http://127.0.0.1:8931/",
    "toyota-receptionist.service": "http://127.0.0.1:8767/health",
}


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    operation: Literal["status", "journal_tail", "check", "run_test"]
    unit: str
    lines: int = Field(default=80, ge=1, le=200)
    suite: str | None = None


class CommandSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    argv: tuple[str, ...]
    timeout_seconds: float = 10


def command(request: Request) -> CommandSpec:
    if request.unit not in UNITS:
        raise ValueError("unit is not approved")
    if request.operation != "run_test" and request.suite is not None:
        raise ValueError("suite is only valid for run_test")
    if request.operation == "status":
        return CommandSpec(argv=("/usr/bin/systemctl", "--user", "show", request.unit,
                                 "--no-pager", "--property=" + ",".join(PROPERTIES)))
    if request.operation == "journal_tail":
        return CommandSpec(argv=("/usr/bin/journalctl", "--user", "-u", request.unit,
                                 "-n", str(request.lines), "--no-pager", "-o", "short-iso"))
    if request.operation == "check":
        url = HEALTH.get(request.unit)
        if url:
            # Report the HTTP result, never the body (which can contain user data).
            return CommandSpec(argv=("/usr/bin/curl", "--silent", "--show-error", "--fail",
                                     "--max-time", "5", "--output", "/dev/null",
                                     "--write-out", "HTTP %{http_code}\\n", url))
        return CommandSpec(argv=("/usr/bin/systemctl", "--user", "is-active", request.unit))
    if request.suite not in TESTS:
        raise ValueError("suite is not approved")
    return CommandSpec(argv=TESTS[request.suite], timeout_seconds=120)
