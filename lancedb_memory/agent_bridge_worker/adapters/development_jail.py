"""Trusted tests and interpreter bound read-only into the development check jail."""
from pathlib import Path
from agent_comms.executors.jail import BwrapJail, WorkspacePrivatePaths


def development_check_jail() -> BwrapJail:
    root = Path(__file__).resolve().parents[3]
    return BwrapJail(WorkspacePrivatePaths(), state_dirs=[], network=False,
        env={"PYTHONDONTWRITEBYTECODE": "1"}, file_binds=[
            (root / "lancedb_memory/.venv", Path("/opt/bridge-test-venv")),
            (root / "agent_bridge/development/check.py", Path("/opt/bridge-check.py")),
            (root / "lancedb_memory/agent_comms/tests/test_machine_ops.py", Path("/opt/bridge-check-tests/test_machine_ops.py")),
            (root / "lancedb_memory/receptionist_team/tests/test_bridge_monitor.py", Path("/opt/bridge-check-tests/test_bridge_monitor.py")),
        ])
