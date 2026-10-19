"""Run the real tax-status workflow with an offline, read-only repository.

uv run --with pydantic --with python-dotenv --with pyyaml --with rich \
  python -m unittest discover -s adws/adw_modules/tax_status_example/tests -v
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

from adw_modules.data_types import SSSFConfig
from adw_modules.tax_status_example import PHASE_NAMES, TaxStatusExample


class TaxStatusExampleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.finance = self.path / "finance"
        (self.finance / "adws").mkdir(parents=True)
        self.playground = self.path / "playground"
        (self.finance / "barnes_2025_tax_preparation_plan.md").write_text(
            "## Still open\n1. Honoraria need checking.\n## How the money works\n")
        (self.finance / "adws/tax_work_plan.yaml").write_text(
            "placeholders:\n- symbol: TOTAL_INCOME_FROM_CHURCH\n  needed_from: Verified records\n")
        # Only the repository boundary is replaced. main(), phases, HTML report,
        # run lifecycle, and trace writer are the actual finance implementation.
        workflow = self.finance / "adws/adw_tax_status.py"
        workflow.write_text('''# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich", "pymysql"]
# ///
import argparse, os, sys
from pathlib import Path
from datetime import date
from decimal import Decimal
''' + f"sys.path.insert(0, {str(ROOT.parent / 'rol_finances/adws')!r})\n" + '''
import adw_tax_status as actual
from adw_modules.tax_work.models import Snapshot, Category, Expense
class ReadOnlyRepository:
    def snapshot(self):
        if os.environ.get("TAX_STATUS_TEST_UNAVAILABLE"):
            raise ConnectionError("test database unavailable")
        return Snapshot(categories=[Category(id=3, name="Personal")], expenses=[
            Expense(id=1, expense_date=date(2025, 1, 1), amount=Decimal("10"))])
actual.repository = ReadOnlyRepository
actual.agents.execute = lambda *_: (_ for _ in ()).throw(AssertionError("No AI calls allowed"))
parser = argparse.ArgumentParser()
parser.add_argument("--config")
parser.add_argument("--finance-root", type=Path)
parser.add_argument("--adw-id")
args = parser.parse_args()
sys.exit(actual.main(args.config, args.finance_root, args.adw_id))
''')

    def example(self) -> TaxStatusExample:
        return TaxStatusExample(self.finance, self.playground)

    def test_five_real_phases_report_and_new_run_on_repeat(self) -> None:
        before = {path: path.read_bytes() for path in self.finance.rglob("*") if path.is_file()}
        example = self.example()
        first = example.invoke()
        second = example.invoke()
        self.assertNotEqual(first.adw_id, second.adw_id)
        for result in (first, second):
            self.assertEqual(result.outcome, "success", result.error)
            self.assertEqual([p.name for p in result.phases], list(PHASE_NAMES))
            self.assertTrue(all(p.kind == "code" and p.status == "success" for p in result.phases))
            html = Path(result.report_path).read_text()
            self.assertIn("Sources checked", html)
            self.assertTrue(result.report_url.endswith(
                f"/api/sessions/{result.adw_id}/reports/remaining-work.html"))
            self.assertIn("Payments still needing a category decision: 1", html)
            events = [json.loads(line) for line in Path(result.log_path).read_text().splitlines()]
            self.assertFalse(any(event["type"] == "agent_start" for event in events))
            self.assertEqual({event["adw_id"] for event in events}, {result.adw_id})
            with sqlite3.connect(self.playground / "adws/adw_data/sssf.db") as db:
                self.assertEqual(db.execute("SELECT adw_name,status FROM sessions WHERE adw_id=?",
                    (result.adw_id,)).fetchone(), ("adw_tax_status", "success"))
        self.assertEqual(before, {path: path.read_bytes() for path in self.finance.rglob("*") if path.is_file()})

    def test_unavailable_source_is_failed_despite_five_executed_phases(self) -> None:
        with patch.dict(os.environ, {"TAX_STATUS_TEST_UNAVAILABLE": "1"}):
            result = self.example().invoke()
        self.assertEqual(result.outcome, "fail")
        self.assertEqual(len(result.phases), 5)
        self.assertIn("incomplete", result.error)
        html = Path(result.report_path).read_text()
        self.assertIn("Incomplete — some sources could not be checked", html)
        self.assertNotIn("Payments still needing a category decision: 0", html)

    def test_missing_checkout_returns_failure(self) -> None:
        result = TaxStatusExample(self.path / "missing", self.playground).invoke()
        self.assertEqual(result.outcome, "fail")
        self.assertIn("ROL_FINANCE_ROOT", result.error)
        self.assertEqual(result.phases, [])

    def test_option_eleven_runs_and_returns_to_menu(self) -> None:
        cfg = SSSFConfig()
        cfg.defaults.data_dir = str(self.path / "console")
        cfg.observability.db = str(self.path / "console.db")
        config = self.path / "console.yaml"
        config.write_text(yaml.safe_dump(cfg.model_dump()))
        bootstrap = (
            "import sys; from pathlib import Path; "
            f"sys.path.insert(0, {str(ROOT / 'adws')!r}); "
            "import adw_debug_console as menu; "
            "from adw_modules.tax_status_example import TaxStatusExample; "
            f"menu.TaxStatusExample = lambda: TaxStatusExample(Path({str(self.finance)!r}), Path({str(self.playground)!r})); "
            f"sys.exit(menu.main({str(config)!r}, None, '/home/adamsl/smart_menu'))"
        )
        completed = subprocess.run([sys.executable, "-c", bootstrap],
            input="11\n12\n", text=True, capture_output=True, cwd=ROOT / "adws",
            timeout=60, check=False)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(completed.stdout.count("11. Run Tax Status ADW"), 2)
        self.assertIn("Tax Status ADW completed: 5 code phases in one run", completed.stdout)
        for phase in PHASE_NAMES:
            self.assertIn(phase, completed.stdout)


if __name__ == "__main__":
    unittest.main()
