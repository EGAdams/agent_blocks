"""Exercise the real ADW subprocess and trace, replacing only the Pi service.

From agent_blocks/:
uv run --with pydantic --with python-dotenv --with pyyaml --with rich \
    python -m unittest discover -s adws/adw_modules/prompt_example/tests -v
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "adws"))

from adw_modules import agents
from adw_modules.debug_console import DebugConsole
from adw_modules.prompt_example import CONFIG, PromptExample, PromptRequest


class PromptExampleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        cfg = agents.load_config(str(CONFIG))
        cfg.defaults.data_dir = str(self.path / "runtime")
        cfg.observability.db = str(self.path / "trace.db")
        for agent in cfg.agents:
            agent.prompt_engineering.system = str(ROOT / agent.prompt_engineering.system)
            agent.prompt_engineering.user = str(ROOT / agent.prompt_engineering.user)
        self.cfg = cfg
        self.config = self.path / "config.yaml"
        self.config.write_text(yaml.safe_dump(cfg.model_dump()))
        self.fake_pi = self.path / "pi"
        self.fake_pi.write_text(f"#!{sys.executable}\n" + '''
import json, os, sys
if "--list-models" in sys.argv:
    print("provider model context")
    print("anthropic claude-haiku-4-5 200K")
    raise SystemExit(0)
if os.environ.get("PROMPT_EXAMPLE_TEST_FAIL"):
    print("test service unavailable", file=sys.stderr)
    raise SystemExit(7)
assert sys.argv[sys.argv.index("--tools") + 1] == ""
assert "-e" not in sys.argv
answer = {"status": "success", "summary": "ADW is the recipe; phases are the steps; a run is cooking it once.",
          "artifacts": [], "notes_for_next_agent": ""}
print(json.dumps({"type": "message_end", "message": {
    "role": "assistant", "content": [{"type": "text", "text": json.dumps(answer)}],
    "stopReason": "stop", "usage": {}}}))
''')
        self.fake_pi.chmod(0o755)
        env = patch.dict(os.environ, {"PI_PATH": str(self.fake_pi), "NO_COLOR": "1"})
        env.start()
        self.addCleanup(env.stop)

    def rows(self, adw_id: str) -> list[tuple]:
        with sqlite3.connect(self.cfg.observability.db) as conn:
            return conn.execute(
                "SELECT name, kind, status FROM phases WHERE adw_id=? ORDER BY seq",
                (adw_id,),
            ).fetchall()

    def test_two_phases_and_fresh_run_per_selection(self) -> None:
        example = PromptExample(self.config)
        first = example.invoke(PromptRequest(prompt="What is a run?"))
        second = example.invoke(PromptRequest(prompt="What is a phase?"))
        self.assertEqual(first.outcome, "success", first.error)
        self.assertEqual(second.outcome, "success", second.error)
        self.assertNotEqual(first.adw_id, second.adw_id)
        for result in (first, second):
            self.assertEqual(self.rows(result.adw_id), [
                ("request", "engineer", "success"), ("prompt", "agent", "success")])
            self.assertIn("recipe", " ".join(result.detail))
            events = [json.loads(line) for line in Path(result.log_path).read_text().splitlines()]
            self.assertEqual({event["adw_id"] for event in events}, {result.adw_id})
            with sqlite3.connect(self.cfg.observability.db) as conn:
                row = conn.execute("SELECT adw_name, status FROM sessions WHERE adw_id=?",
                                   (result.adw_id,)).fetchone()
            self.assertEqual(row, ("adw_prompt", "success"))

    def test_agent_failure_is_traced_and_next_selection_can_succeed(self) -> None:
        example = PromptExample(self.config)
        with patch.dict(os.environ, {"PROMPT_EXAMPLE_TEST_FAIL": "1"}):
            failed = example.invoke(PromptRequest(prompt="Hello"))
        self.assertEqual(failed.outcome, "fail")
        self.assertEqual(self.rows(failed.adw_id), [
            ("request", "engineer", "success"), ("prompt", "agent", "fail")])
        recovered = example.invoke(PromptRequest(prompt="Try again"))
        self.assertEqual(recovered.outcome, "success", recovered.error)
        self.assertNotEqual(failed.adw_id, recovered.adw_id)

    def test_idle_console_closes_successfully(self) -> None:
        console = DebugConsole(self.cfg)
        self.assertEqual(console.close(), 0)
        self.assertEqual(self.rows(console.adw_id), [])

    def test_menu_runs_option_ten_and_returns_to_menu_from_adws_directory(self) -> None:
        # Replace only the example's config so the terminal test is offline
        # and writes its traces into the same temporary directory as the rest.
        bootstrap = (
            "import sys; from pathlib import Path; "
            f"sys.path.insert(0, {str(ROOT / 'adws')!r}); "
            "import adw_debug_console as menu; "
            "from adw_modules.prompt_example import PromptExample; "
            f"menu.PromptExample = lambda: PromptExample(Path({str(self.config)!r})); "
            f"sys.exit(menu.main({str(self.config)!r}, None, '/home/adamsl/smart_menu'))"
        )
        result = subprocess.run(
            [sys.executable, "-c", bootstrap], input="10\n\n12\n", text=True,
            capture_output=True, cwd=ROOT / "adws", timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Prompt ADW completed: 2 phases in one run", result.stdout)
        self.assertEqual(result.stdout.count("10. Run Prompt ADW"), 2)
        self.assertIn("request (engineer) -> prompt (agent: learner)", result.stdout)


if __name__ == "__main__":
    unittest.main()
