"""Regression coverage for Sydney repeating report requests instead of running them."""
from unittest.mock import patch
from pathlib import Path

from tests.fakes import ScriptedModel

from ..adapters.finance_report_runner import FinanceReportRunner
from ..adapters.routing_model import RoutingModel
from ..profiles import SYDNEY


class Workflow:
    def __init__(self, line="Published the 2025 report to ADW Generated."):
        self.calls = []
        self.result = line

    def line(self, purpose, flags):
        self.calls.append((purpose, flags))
        return self.result


REQUESTS = [
    "Can you create a fresh River of Life finance report for us? We've already sent the taxes now. We need to send the freshest finance report to Mom so she can email it out to all the board members.",
    "Can you create the latest finance report from the database using the data in the database and put it on the ADW generated page please?",
    "Generate the latest financial report for the nonprofit 2025 data.",
]


async def test_real_requests_run_the_workflow_without_a_model_or_window_handoff():
    for request in REQUESTS:
        workflow = Workflow()
        inner = ScriptedModel([])  # fails if called
        model = RoutingModel(inner, "unused", SYDNEY.roster,
                             FinanceReportRunner(workflow), override=SYDNEY.route_override)
        reply = await model.complete(f"USER: {request}\nAGENT:")
        assert workflow.calls == [(request, [])]
        assert reply.text == workflow.result


async def test_real_subprocess_contract_passes_through_success_and_failure():
    for message, code in [("Published the report at /reports/fresh.html", 0),
                          ("No finance report was published. Database unavailable.", 1)]:
        def process(argv, **kwargs):
            assert Path(argv[1]).name == "adw_interface_finance_report.py"
            assert argv[2] == REQUESTS[1]
            Path(argv[argv.index("--message-out") + 1]).write_text(message)
            return type("Done", (), {"returncode": code, "stderr": "", "stdout": ""})()
        with patch("receptionist_team.adapters.adw_process.subprocess.run", process):
            reply = await FinanceReportRunner().run(None, "finance_report", "", REQUESTS[1])
        assert reply.text == message


async def test_other_year_does_not_run_the_2025_build():
    workflow = Workflow()
    reply = await FinanceReportRunner(workflow).run(None, "finance_report", "", "Generate the 2026 finance report")
    assert not workflow.calls
    assert "2025" in reply.text and "No report" in reply.text


async def test_other_books_do_not_run_the_nonprofit_build():
    workflow = Workflow()
    reply = await FinanceReportRunner(workflow).run(None, "finance_report", "", "Create my personal 2025 finance report")
    assert not workflow.calls and "nonprofit" in reply.text


async def test_history_discussion_and_negation_do_not_generate_reports():
    for message in ["Hello", "Do you know how to create finance reports for the nonprofit?",
                    "How do I generate the finance report?", "Don't generate the finance report.",
                    "Do not create a finance report", "Cancel the finance report build"]:
        prompt = f"USER: {REQUESTS[1]}\nAGENT: old result\nUSER: {message}\nAGENT:"
        assert SYDNEY.route_override.forced_agent(prompt) is None, message
    assert SYDNEY.route_override.forced_agent(REQUESTS[1]) is None
    assert SYDNEY.route_override.forced_agent("USER: Ask Sonnet to fix the finance report code\nAGENT:") == "sydney_post_office"


async def test_profile_wires_the_report_to_the_deterministic_runner():
    runner = SYDNEY.build_runner()
    assert isinstance(runner._runners["finance_report"], FinanceReportRunner)
    assert SYDNEY.roster.has("finance_report")
    role = SYDNEY.roster.role("finance_report")
    assert "Never change database records" in role.system_prompt
    assert "finance_report" in SYDNEY.system_prompt


if __name__ == "__main__":
    from tests.runner import main
    main(dict(globals()))
