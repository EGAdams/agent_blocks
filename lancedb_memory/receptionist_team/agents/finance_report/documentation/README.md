# Finance report

Sydney runs the existing `adws/adw_interface_finance_report.py` through
`FinanceReportRunner`. The result comes directly from the workflow.
The report is for River of Life nonprofit 2025 data. No email is sent.

Generation requests are forced from the current user message; recalled requests
and capability questions do not launch a build. Other years are refused.
Sonnet/Opus requests keep their explicit recipient. For conversational desks,
the post office infers a message when a work verb is not on their capability
card; an explicit `type=` is still validated unchanged.

Runtime requirements: the receptionist venv dependencies, the rol_finances
venv and report scripts, master database access, and `soffice` (LibreOffice
Calc). Live host: DESKTOP-2OBSQMC. The workflow reports an error if a build
step fails and never substitutes remembered figures.
