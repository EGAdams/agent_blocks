"""Offline summary contract tests; no database or report generation."""
from pathlib import Path
import tempfile
import unittest

from adw_interface_finance_report import SummaryStripFigureReader


class Log:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class SummaryTests(unittest.TestCase):
    def read(self, label):
        with tempfile.TemporaryDirectory() as scratch:
            page = Path(scratch) / "report.html"
            page.write_text(f"<small>Received</small><b>$100.00</b>"
                            f"<small>{label}</small><b>$30.00</b>"
                            "<small>Spent</small><b>$70.00</b>"
                            "<small>Cash 12/31/25</small><b>$40.00</b>")
            return SummaryStripFigureReader(Log()).read(page)

    def test_current_report_label_and_value_are_preserved(self):
        figures = self.read("Left over")
        self.assertEqual(figures.available_label, "left over")
        self.assertEqual(figures.available, "$30.00")
        self.assertEqual(figures.cash, "$40.00")

    def test_legacy_report_label_still_works(self):
        figures = self.read("Available to spend")
        self.assertEqual(figures.available_label, "available to spend")
        self.assertEqual(figures.available, "$30.00")

    def test_unknown_label_never_invents_a_value(self):
        self.assertEqual(self.read("Unknown figure").available, "?")


if __name__ == "__main__":
    unittest.main()
