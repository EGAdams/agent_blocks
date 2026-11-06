"""Trusted gate mounted read-only beside a read-only development checkout."""
import ast
import importlib.util
import sys
import unittest
from pathlib import Path

root = Path.cwd()
sys.path.insert(0, str(root / "lancedb_memory"))
suite = unittest.TestSuite()
for name in ("test_machine_ops", "test_bridge_monitor"):
    spec = importlib.util.spec_from_file_location(name, f"/opt/bridge-check-tests/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
if not unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful():
    raise SystemExit(1)
for package in ("agent_comms", "agent_bridge_worker", "receptionist_team"):
    for path in (root / "lancedb_memory" / package).rglob("*.py"):
        ast.parse(path.read_text(), filename=str(path))
assert not Path("/home/adamsl/agent_blocks/lancedb_memory/agent_comms/agents.json").exists(), "production checkout is visible"
assert not Path("/home/adamsl/.config/agent_bridge").exists(), "production credentials are visible"
print("Trusted integration tests, syntax and production isolation checks passed.")
